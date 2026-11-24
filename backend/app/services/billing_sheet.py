"""The office's billing sheet (the "super bill") for a record put together from received files.

Each evaluation's paperwork starts with a one-page billing sheet. Its TOP part says what came in;
its bottom part (billing codes and the total) is billing's and is printed blank. The office filled
the top by hand: the doctor (named in full at the top, ``reporting.DOCTOR_NAMES``; one form for
every doctor, printed in black), the appointment date (DOS), the patient, the type of visit, and one
row of the party who sent the records - the page count the sender's declaration states, the
deposition pages, the office's own page count, and why the two counts differ.

What the uploader types at upload (``POST /api/documents/combine``) is stored on the document; the
two counts are worked out here from the record itself:

- the OFFICE COUNT is every page received except the office's own internal email (a guide for the
  reviewer, not part of the records) and the envelope; a condensed deposition sheet, four
  transcript pages printed on one, counts four (the office's answers of 2026-11-19);
- the DEPOSITION PAGES are the pages of testimony only - from a transcript's first page of
  questions and answers to its last, leaving out its cover, appearances and index before and the
  reporter's certificate, errata and exhibits after (the office's answer, 2026-11-19).

Testimony is found from the stored page text, before Identify has made any rows: a page with at
least ``_MIN_QA_LINES`` lines that start with ``Q`` or ``A`` (after the transcript's own line
number) is a page of questions and answers, and such pages close together are one transcript.
Measured read-only on the live box (2026-11-19) against the reviewers' deposition rows of 740
finished records - 131 of them holding testimony, 5,755 pages from first to last question: the
count from the pages alone matched the count inside the reviewers' rows exactly on 732 records and
was off by more than 5 pages on 6; 144 pages too many in all (127 of them in 3 transcripts the
reviewers had not filed as depositions) and 16 too few.

The sheet never waits for that reading (an uploader's report, 2026-11-24: ~10 minutes on 900
pages). The office count is the pages received, known the moment the record is put together; the
deposition pages print blank until every counted page has been read (``reading``).

The sheet is never part of the record: Identify, the summaries and their page numbers never see it.
It is the first page of the record's raw download only (``put_on_top``, the office's ask of
2026-11-23), built at the moment of the download from the sheet as it stands then.

Nothing here logs page text, names or any field of the sheet.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import pymupdf
from redis.exceptions import RedisError
from sqlalchemy import select

from app.models import Document, PageText
from app.services.ocr import has_condensed_sheets
from app.services.reporting import DOCTOR_NAMES
from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

# The type of visit, in the office's order. A supplemental report has no appointment: its DOS is
# printed as SUPP too (the office's answer, 2026-11-19).
VISIT_TYPES: tuple[str, ...] = ("AME", "QME", "PQME", "IME", "SUPP")
SUPPLEMENTAL = "SUPP"

# Who reviews the records - the title line of the sheet. The office's form offers E-DATA,
# IN-HOUSE and LEONARDO, but the office reviews every record in house, so the sheet always says
# IN-HOUSE and the uploader is not asked (2026-11-20). `documents.review_source`, asked before
# that, is no longer read.
REVIEWED_BY = "IN-HOUSE"

# The rows of the party table, in the sheet's order (received_files.PARTIES).
_PARTY_ROWS: tuple[tuple[str, str], ...] = (
    ("defense", "Defense:"),
    ("applicant", "Applicant:"),
    ("insurance", "Insurance:"),
    ("copy_service", "Copy Srvs:"),
)

# Received files that are not part of the records sent, so not in the office count
# (received_files.KINDS).
NOT_COUNTED_KINDS = frozenset({"email", "envelope"})

# A line of questions and answers: an optional transcript line number, then Q or A and the words.
_QA_LINE = re.compile(r"(?m)^\s*(?:\d{1,2}\s+)?[QA][\.:]?\s+\S")
# A page with at least this many such lines is testimony. A transcript page holds 25 numbered
# lines; on the 228 deposition rows of the live box a page of testimony reads 4 or more, while
# outside them only 86 of 33,000 pages did (a report quoting a deposition, a page beside one).
_MIN_QA_LINES = 4
# Pages of testimony belong to one transcript when no more than this many pages without questions
# and answers lie between them: colloquy, a break, an exhibit read into the record. Between two
# transcripts lie the first one's certificate and errata and the next one's cover, appearances
# and index - on the live box 4 and 5 pages at the median.
_RUN_GAP = 6
# A run needs this many pages of testimony to be a transcript; fewer is a report quoting one. With
# 3 the live box counted 11 runs outside any deposition, with 5 only 3, and missed the same one row.
_MIN_RUN_PAGES = 5
# A condensed transcript prints this many of its pages on one sheet.
_CONDENSED_PAGES = 4


@dataclass(frozen=True)
class Counts:
    """The two counts the sheet carries. ``office`` is every page received but the internal email
    and the envelope, a condensed deposition sheet counting four; ``depo`` the pages of testimony,
    None while some page that could hold testimony has no text yet (a scan not read yet)."""

    office: int
    depo: int | None


def counted_pages(parts: Sequence[Mapping[str, object]] | None, page_count: int) -> list[int]:
    """The record's pages that the office counts: every page of a file that is part of the records
    sent. A record uploaded as one PDF has no parts, and every page is counted."""
    if not parts:
        return list(range(1, page_count + 1))
    pages: list[int] = []
    for part in parts:
        if part.get("kind") in NOT_COUNTED_KINDS:
            continue
        start, end = int(part["start"]), int(part["end"])  # type: ignore[arg-type]
        pages.extend(range(start, end + 1))
    return pages


def is_testimony(text: str) -> bool:
    """Whether a page's text is a page of a transcript's questions and answers."""
    return len(_QA_LINE.findall(text or "")) >= _MIN_QA_LINES


def runs_of(testimony: Iterable[int]) -> list[tuple[int, int]]:
    """``(first, last)`` page of each transcript's testimony, from its pages of testimony."""
    runs: list[list[int]] = []
    for page in sorted(set(testimony)):
        if runs and page - runs[-1][1] - 1 <= _RUN_GAP:
            runs[-1][1] = page
            runs[-1][2] += 1
        else:
            runs.append([page, page, 1])
    return [(first, last) for first, last, found in runs if found >= _MIN_RUN_PAGES]


def testimony_runs(texts: Mapping[int, str], pages: Iterable[int]) -> list[tuple[int, int]]:
    """``(first, last)`` page of each transcript's testimony among ``pages``, in page order."""
    return runs_of(p for p in pages if is_testimony(texts.get(p, "")))


def counts_of(
    counted: Sequence[int],
    testimony: Iterable[int],
    condensed: Callable[[int, int], bool],
    *,
    complete: bool = True,
) -> Counts:
    """The office count and the deposition pages of the ``counted`` pages, ``testimony`` the ones
    known to be testimony. ``condensed(first, last)`` says whether a transcript's sheets are printed
    condensed, four pages to a sheet (``ocr.has_condensed_sheets`` looks for the grid on its first
    sheets); its sheets then count four each, in both counts. Not ``complete`` - some page could
    still prove to be testimony - the deposition pages are None; the office count stands."""
    kept = set(counted)
    depo = extra = 0
    for first, last in runs_of(p for p in testimony if p in kept):
        sheets = sum(1 for page in range(first, last + 1) if page in kept)
        per_sheet = _CONDENSED_PAGES if condensed(first, last) else 1
        depo += sheets * per_sheet
        extra += sheets * (per_sheet - 1)
    return Counts(office=len(counted) + extra, depo=depo if complete else None)


def count_pages(
    parts: Sequence[Mapping[str, object]] | None,
    page_count: int,
    texts: Mapping[int, str],
    condensed: Callable[[int, int], bool],
) -> Counts:
    """The office count and the deposition pages of a record whose every page's ``texts`` is
    known (``counts_of``)."""
    counted = counted_pages(parts, page_count)
    return counts_of(counted, (p for p in counted if is_testimony(texts.get(p, ""))), condensed)


# --- the sheet never waits -----------------------------------------------------------------------
#
# The sheet - and the raw download it heads - waited for every page of the record to be read: ~10
# minutes on a 900-page record (an uploader's report, 2026-11-24). Now it never waits. The office
# count is the pages received, known the moment the record is put together; only the deposition
# pages need the pages read, and print blank until they are.
#
# Reading the PDF's own text instead was measured and dropped (read-only on the live box,
# 2026-11-24: 784 records, 136,293 pages): half the pages carry text of their own (73% on records
# put together from received files), but only 3 of 28 such records - 11 of 756 others - carry it on
# every page, so a scan was still to be read on nearly every record; and where both exist that text
# missed 1,357 of the 5,058 pages of testimony our reading finds.
#
# A transcript printed condensed, four pages to a sheet, is rare: the check found none among the
# 167 transcripts on those records. It draws pages, so its answer is kept in Redis rather than drawn
# again on each look at the sheet - the dialog reads the sheet every few seconds.

_CONDENSED_KEY = "billing:condensed:{}:{}:{}-{}"
_CACHE_SECONDS = 30 * 24 * 3600


def _cached(key: str) -> str | None:
    try:
        value = get_redis().get(key)
    except RedisError as exc:
        logger.warning("billing sheet: cache unavailable (%s)", type(exc).__name__)
        return None
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def _keep(key: str, value: str) -> None:
    try:
        get_redis().set(key, value, ex=_CACHE_SECONDS)
    except RedisError as exc:
        logger.warning("billing sheet: cache unavailable (%s)", type(exc).__name__)


def _condensed(document: Document, first: int, last: int) -> bool:
    """``has_condensed_sheets`` for one transcript of the record, worked out once and kept."""
    key = _CONDENSED_KEY.format(document.id, document.sha256, first, last)
    cached = _cached(key)
    if cached is not None:
        return cached == "1"
    found = has_condensed_sheets(document.stored_path, first, last, max_sheets=2)
    _keep(key, "1" if found else "0")
    return found


@dataclass(frozen=True)
class Reading:
    """Where the record's counted pages stand. While ``unread`` of them wait to be read the
    deposition pages are not known (``counts.depo`` is None); the office count always is."""

    counted: int
    unread: int
    counts: Counts


def stored_texts(session, document_id: str) -> dict[int, str]:
    """The stored text of every page of the record read so far, by page number."""
    rows = session.execute(
        select(PageText.page, PageText.text).where(PageText.document_id == document_id)
    ).all()
    return {page: text or "" for page, text in rows}


def reading(session, document: Document) -> Reading:
    """Count the record's pages now, from what has been read so far (the upload's reading, queued by
    ``POST /api/documents/combine``, or Identify's)."""
    texts = stored_texts(session, document.id)
    counted = counted_pages(document.received_parts, document.page_count or 0)
    unread = sum(1 for page in counted if page not in texts)
    testimony = [page for page in counted if is_testimony(texts.get(page, ""))]

    def condensed(first: int, last: int) -> bool:
        return _condensed(document, first, last)

    counts = counts_of(counted, testimony, condensed, complete=not unread)
    return Reading(len(counted), unread, counts)


@dataclass(frozen=True)
class Sheet:
    """What goes on the sheet, as printed. Empty strings and None print as blanks."""

    doctor: str = ""
    dos: str = ""
    previous_visit: str = ""
    patient_name: str = ""
    patient_id: str = ""
    location: str = ""
    claim_numbers: str = ""
    visit_type: str = ""
    interpreter: bool | None = None
    records_from: str | None = None
    declared_pages: int | None = None
    depo_pages: int | None = None
    office_count: int | None = None
    discrepancy_reason: str = ""
    date_received: str = ""


def us_date(value: date | None) -> str:
    """``MM/DD/YYYY``, the way the office writes dates, or empty."""
    return value.strftime("%m/%d/%Y") if value else ""


def dos(visit_type: str | None, appointment: date | None) -> str:
    """The sheet's DOS: SUPP for a supplemental report, else the appointment date."""
    return SUPPLEMENTAL if visit_type == SUPPLEMENTAL else us_date(appointment)


def doctor_name(doctor: str | None) -> str:
    """The sheet's header: the doctor's full name from the office's list. A doctor still stored on
    a record but no longer on the list prints as stored, after "Dr."; no doctor prints nothing."""
    doctor = (doctor or "").strip()
    if not doctor:
        return ""
    return DOCTOR_NAMES.get(doctor, f"Dr. {doctor}")


def patient_name(last: str | None, first: str | None) -> str:
    """``Last, First`` - or whichever part is known."""
    return ", ".join(part for part in ((last or "").strip(), (first or "").strip()) if part)


def sheet_for(document: Document, counts: Counts | None) -> Sheet:
    """What the record's billing sheet says. A count typed on the sheet wins over the worked-out
    one; with neither (pages still being read) the count is left blank."""
    office = document.office_count
    if office is None and counts is not None:
        office = counts.office
    depo = document.depo_pages
    if depo is None and counts is not None:
        depo = counts.depo
    return Sheet(
        doctor=document.doctor or "",
        dos=dos(document.visit_type, document.appointment_date),
        previous_visit=us_date(document.previous_visit),
        patient_name=patient_name(document.patient_last_name, document.patient_first_name),
        patient_id=document.patient_id or "",
        location=document.location or "",
        claim_numbers=document.claim_numbers or "",
        visit_type=document.visit_type or "",
        interpreter=document.interpreter,
        records_from=document.records_from,
        declared_pages=document.declared_pages,
        depo_pages=depo,
        office_count=office,
        discrepancy_reason=document.discrepancy_reason or "",
        date_received=us_date(document.received_on),
    )


# --- drawing -------------------------------------------------------------------------------------
#
# Letter size, laid out after the office's own sheet. Built-in Helvetica (no font file to ship);
# a value too long for its space is set smaller, and cut only if it still does not fit.

_PAGE_W, _PAGE_H = 612, 792
_BOLD, _REGULAR, _BOLD_ITALIC, _ITALIC = "hebo", "helv", "hebi", "heit"
_LINE_W = 0.6
# The doctor's name may run from the left margin to just before "DOS:" on the same line.
_DOCTOR_ROOM = 400
_BLACK = (0, 0, 0)

# The party table: its column edges, the header's top and bottom, and one row's height.
_COLS = (18.0, 94.5, 172.0, 234.2, 274.6, 321.5, 444.6, 522.3, 593.8)
_TABLE_TOP, _HEADER_BOTTOM, _ROW_H = 154.4, 198.7, 15.2
_HEADERS = (
    ("DEC/ATTEST", "PAGE COUNT"),
    ("DEPO", "PAGE", "INCLUDED"),
    ("SUB-", "ROSA"),
    ("OFFICE", "COUNT"),
    ("REASON FOR", "DISCREPANCY"),
    ("DEC/ATTEST", "VALID?"),
    ("DATE", "RECEIVED"),
)

# The bottom half, billing's: code, description, price. Printed as the office's sheet has it, with
# nothing marked. The prices are the California medical-legal fee schedule's.
_BILLING: tuple[tuple[str | None, tuple[tuple[str, str, str], ...]], ...] = (
    (None, (("ML200", "MISSED APPOINTMENT", "$503.75"),)),
    (
        "INITIAL VISITS",
        (
            ("ML201-95", "QME EVALUATION", "$2,015.00-includes 200 pages of records"),
            (
                "ML201-95-93",
                "QME EVALUATION WITH INTERPRETER",
                "$2,216.50-includes 200 pages of records",
            ),
            ("ML201-94", "AME EVALUATION", "$2,720.25-includes 200 pages of records"),
            (
                "ML201-94-93",
                "AME EVALUATION WITH INTERPRETER",
                "$2,921.75-includes 200 pages of records",
            ),
        ),
    ),
    (
        "FOLLOW UP VISITS",
        (
            ("ML202-95", "QME FOLLOW UP", "$1,316.25-includes 200 pages of records"),
            (
                "ML202-95-93",
                "QME FOLLOW UP WITH INTERPRETER",
                "$1,447.87-includes 200 pages of records",
            ),
            ("ML202-94", "AME FOLLOW UP", "$1,776.93-includes 200 pages of records"),
            (
                "ML202-94-93",
                "AME FOLLOW UP WITH INTERPRETER",
                "$1,908.56-includes 200 pages of records",
            ),
        ),
    ),
    (
        "SUPPLEMENTAL REPORT",
        (
            ("ML203-95", "QME SUPPLEMENTAL REPORT", "$650.00-includes 50 pages of records"),
            ("ML203-94", "AME SUPPLEMENTAL REPORT", "$877.50-includes 50 pages of records"),
        ),
    ),
    ("RECORD REVIEW", (("ML-PRR", "EXTRA PAGES OF RECORD", "$3.00 X ____________ EXTRA PAGES"),)),
    ("SUBROSA-VIDEO", (("ML205", "SUBROSA RECORDING PER HOUR", "$325/HOUR __________ hrs"),)),
)


def _width(text: str, font: str, size: float) -> float:
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _fitted(text: str, font: str, size: float, width: float) -> tuple[str, float]:
    """``text`` and a font size at which it fits ``width``: smaller down to 6 points, then cut."""
    while size > 6 and _width(text, font, size) > width:
        size -= 0.5
    if _width(text, font, size) <= width:
        return text, size
    kept = text
    while kept and _width(kept + "...", font, size) > width:
        kept = kept[:-1]
    return (kept.rstrip() + "..." if kept else ""), size


def _text(page, x: float, y: float, text: str, font: str = _REGULAR, size: float = 12) -> float:
    """Write ``text`` with its baseline at ``y``; returns the x where it ends."""
    if text:
        page.insert_text((x, y), text, fontname=font, fontsize=size, color=_BLACK)
    return x + _width(text, font, size)


def _field(page, x: float, y: float, label: str, value: str, end: float, size: float = 12) -> None:
    """A bold label, then a line to ``end`` with the value written on it."""
    start = _text(page, x, y, label, _BOLD, size) + 3
    page.draw_line((start, y + 2), (end, y + 2), color=_BLACK, width=_LINE_W)
    shown, shown_size = _fitted(value, _REGULAR, size, end - start - 4)
    _text(page, start + 3, y, shown, _REGULAR, shown_size)


def _ring(page, x: float, y: float, text: str, font: str, size: float) -> None:
    """An oval around ``text`` written at ``(x, y)`` - the office circles the choice that holds."""
    width = _width(text, font, size)
    page.draw_oval(
        pymupdf.Rect(x - 4, y - size * 0.95, x + width + 4, y + size * 0.35),
        color=_BLACK,
        width=_LINE_W,
    )


def _radio(page, x: float, y: float, filled: bool) -> None:
    """The small round box in front of a party or a billing code."""
    page.draw_circle(
        (x + 4.5, y - 3.8), 4.2, color=_BLACK, fill=_BLACK if filled else None, width=_LINE_W
    )


def _draw_top(page, sheet: Sheet) -> None:
    _text(page, (_PAGE_W - _width(REVIEWED_BY, _BOLD, 16)) / 2, 26, REVIEWED_BY, _BOLD, 16)

    doctor, size = _fitted(doctor_name(sheet.doctor), _BOLD, 22, _DOCTOR_ROOM)
    _text(page, 20.8, 58, doctor, _BOLD, size)
    _field(page, 448, 56, "DOS:", sheet.dos, 592)
    _field(page, 356, 76, "PREVIOUS OFFICE VISIT:", sheet.previous_visit, 592)

    _field(page, 18, 106, "Patient Name:", sheet.patient_name, 346)
    _field(page, 352, 106, "Acct:", sheet.patient_id, 444)
    _field(page, 449, 106, "Location:", sheet.location, 592)

    _field(page, 18, 135, "Claim Number:", sheet.claim_numbers, 262)
    _field(page, 266, 135, "Type of Visit:", sheet.visit_type, 404)
    x = _text(page, 410, 135, "INTERPRETER:", _BOLD) + 8
    for word, value in (("YES", True), ("NO", False)):
        if sheet.interpreter is value:
            _ring(page, x, 135, word, _BOLD, 12)
        x = _text(page, x, 135, word, _BOLD) + 14


def _number(value: int | None) -> str:
    return "" if value is None else str(value)


def _draw_grid(page) -> None:
    """The party table's lines and its column headings."""
    bottom = _HEADER_BOTTOM + _ROW_H * len(_PARTY_ROWS)
    for x in _COLS:
        page.draw_line((x, _TABLE_TOP), (x, bottom), color=_BLACK, width=_LINE_W)
    rows = [_HEADER_BOTTOM + _ROW_H * (i + 1) for i in range(len(_PARTY_ROWS))]
    for y in [_TABLE_TOP, _HEADER_BOTTOM, *rows]:
        page.draw_line((_COLS[0], y), (_COLS[-1], y), color=_BLACK, width=_LINE_W)
    for column, lines in enumerate(_HEADERS, start=1):
        for line_no, line in enumerate(lines):
            _text(page, _COLS[column] + 4, _TABLE_TOP + 12.5 + 14.2 * line_no, line, _BOLD, 10)


def _fill_row(page, y: float, sheet: Sheet) -> None:
    """The sender's row: the declared count, the deposition pages, Sub-rosa (none comes with the
    records), the office count, the reason the counts differ and the date received. Whether the
    declaration is valid is left for a person to circle."""
    values = (
        _number(sheet.declared_pages),
        _number(sheet.depo_pages),
        "None",
        _number(sheet.office_count),
        sheet.discrepancy_reason,
    )
    for column, value in enumerate(values, start=1):
        room = _COLS[column + 1] - _COLS[column] - 8
        shown, size = _fitted(value, _REGULAR, 11, room)
        _text(page, _COLS[column] + 4, y, shown, _REGULAR, size)
    _text(page, _COLS[7] + 4, y, sheet.date_received, _REGULAR, 11)


def _draw_table(page, sheet: Sheet) -> None:
    _draw_grid(page)
    for index, (party, label) in enumerate(_PARTY_ROWS):
        y = _HEADER_BOTTOM + _ROW_H * index + 11.5
        mine = party == sheet.records_from
        _radio(page, _COLS[0] + 5, y, mine)
        _text(page, _COLS[0] + 16, y, label, _BOLD, 10.5)
        _text(page, _COLS[6] + 8, y, "YES", _BOLD, 11)
        _text(page, _COLS[6] + 46, y, "NO", _BOLD, 11)
        if mine:
            _fill_row(page, y, sheet)


def _price(page, x: float, y: float, price: str) -> None:
    """A billing code's price as the form prints it: the amount in bold, what it includes
    ("-includes 200 pages of records") in italics."""
    amount, dash, includes = price.partition("-")
    x = _text(page, x, y, amount, _BOLD, 9)
    if dash:
        _text(page, x, y, dash + includes, _ITALIC, 9)


def _draw_billing(page) -> None:
    y = 285.0
    _text(page, 90, y, "Billing Code:", _BOLD, 11)
    _text(page, 198, y, "Description:", _BOLD, 11)
    _text(page, 378, y, "Price:", _BOLD, 11)
    y += 14.7
    for heading, codes in _BILLING:
        if heading:
            y += 13.4
            _text(page, 90, y, heading, _BOLD_ITALIC, 10)
            y += 13.4
        for code, description, price in codes:
            _radio(page, 90, y, False)
            _text(page, 101, y, code, _BOLD, 10)
            _text(page, 198, y, description, _REGULAR, 9)
            _price(page, 378, y, price)
            y += 13.4
    _text(
        page, 36, 648, "TOTAL BILL: ________________________________   Plus Xray if Any:", _BOLD, 11
    )
    x = _text(page, 36, 691, "APPROVED FOR REVIEW", _BOLD, 12) + 10
    for word in ("YES", "NO"):
        _radio(page, x, 691, False)
        x = _text(page, x + 11, 691, word, _BOLD, 11) + 10
    _text(page, x + 6, 691, "BY: ______________________   Date: ______________", _BOLD, 11)
    _text(page, 36, 720, "Note to Biller:", _BOLD, 12)


def render(sheet: Sheet) -> bytes:
    """The sheet as a one-page PDF."""
    with pymupdf.open() as document:
        page = document.new_page(width=_PAGE_W, height=_PAGE_H)
        _draw_top(page, sheet)
        _draw_table(page, sheet)
        _draw_billing(page)
        buffer = io.BytesIO()
        document.save(buffer, garbage=3, deflate=True)
        return buffer.getvalue()


# `pymupdf.PDF_ENCRYPT_KEEP`, missing from the package's type stubs; a test keeps the two equal.
_KEEP_ENCRYPTION = 0


def put_on_top(
    sheet_pdf: bytes, record_path: str | os.PathLike, out_path: str | os.PathLike
) -> None:
    """Write ``out_path``: the billing sheet ``sheet_pdf`` as page 1, then every page of the record
    at ``record_path``, which is left exactly as it is.

    The record is copied and the sheet added to the copy as an incremental update, so the copy
    keeps every byte of the record as received and nothing is held in memory: an 882 MB record took
    about 50 MB and 8 seconds on a laptop (2026-11-23), half of it the copy. A file a PDF reader had
    to repair cannot take an incremental update and is written whole instead, which needs more
    memory; a record joined from received files is written by this app and never needs it."""
    shutil.copyfile(record_path, out_path)
    whole = f"{os.fspath(out_path)}.whole"
    with pymupdf.open(stream=sheet_pdf, filetype="pdf") as sheet, pymupdf.open(out_path) as record:
        record.insert_pdf(sheet, start_at=0)
        if record.can_save_incrementally():
            # The same bytes for the same sheet each time: no new file id.
            record.save(
                out_path,
                incremental=True,
                encryption=_KEEP_ENCRYPTION,
                no_new_id=True,
            )
            return
        record.save(whole)
    os.replace(whole, out_path)
