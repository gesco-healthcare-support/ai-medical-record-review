"""The office's billing sheet: services/billing_sheet.py, the billing fields typed at upload
(POST /api/documents/combine), the doctors list (GET /api/documents/combine/doctors) and the sheet
itself (GET/PUT /api/documents/{id}/billing, GET /api/documents/{id}/billing-sheet).

Synthetic data only: blank PDF pages, made-up page text for the deposition counter, made-up names.
Uploads go to a per-test tmp dir; no model, no OCR.
"""

from datetime import date
from pathlib import Path

import pymupdf
import pytest
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, PageText
from app.services import billing_sheet, received_files
from app.services.reporting import DOCTOR_FONTS, DOCTOR_NAMES, DOCTORS
from app.worker.queues import OCR_QUEUE, get_redis
from tests.conftest import lanes
from tests.test_received_files import (
    _FIELDS,
    _combine,
    _document,
    _login,
    _pdf,
    _taken,
    _user,
)


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_reading(monkeypatch):
    """No OCR and no model in a test: a staged file's first page reads as nothing."""
    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", lambda *a, **k: "")

    def off(stage):
        raise ConnectionError("no model in tests")

    monkeypatch.setattr(received_files, "provider_for_stage", off)


@pytest.fixture(autouse=True)
def _empty_queues():
    queue = Queue(OCR_QUEUE, connection=get_redis())
    queue.empty()
    yield
    queue.empty()
    lanes("segment").empty()


def _testimony(lines: int = 6) -> str:
    """A transcript page of questions and answers, as OCR reads one: numbered lines."""
    out = []
    for n in range(1, lines + 1):
        out.append(f"{n} {'Q.' if n % 2 else 'A.'} Synthetic line number {n}.")
    return "\n".join(out)


_PROSE = "A synthetic report page.\nIt has ordinary lines.\nNone of them is a question."


# --- services/billing_sheet.py: what is counted --------------------------------------------------


def test_a_record_from_one_pdf_counts_every_page():
    assert billing_sheet.counted_pages(None, 4) == [1, 2, 3, 4]
    assert billing_sheet.counted_pages([], 2) == [1, 2]


def test_the_internal_email_and_the_envelope_are_not_counted():
    """DEMONSTRATES the office's answer: everything is counted but the email thread (a guide for
    the reviewer, not part of the records) and the envelope."""
    parts = [
        {"kind": "advocacy_letter", "start": 1, "end": 2},
        {"kind": "records", "start": 3, "end": 5},
        {"kind": "email", "start": 6, "end": 7},
        {"kind": "envelope", "start": 8, "end": 8},
    ]

    assert billing_sheet.counted_pages(parts, 8) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("text", "testimony"),
    [
        (_testimony(6), True),
        (_testimony(4), True),
        (_testimony(3), False),
        ("Q. One question.\nA. One answer.\nQ. Another.\nA. Yes.", True),
        (_PROSE, False),
        ("", False),
    ],
)
def test_a_page_of_testimony_has_four_question_or_answer_lines(text, testimony):
    assert billing_sheet.is_testimony(text) is testimony


def _texts(spec: str) -> dict[int, str]:
    """Page texts from a string: ``Q`` a page of testimony, ``.`` a page of prose, page 1 first."""
    return {i: (_testimony() if c == "Q" else _PROSE) for i, c in enumerate(spec, start=1)}


def test_testimony_runs_from_the_first_question_to_the_last():
    """DEMONSTRATES: the cover, appearances and index before and the certificate after are left
    out; colloquy pages inside the transcript are counted."""
    texts = _texts("....QQQ.QQQ...")

    assert billing_sheet.testimony_runs(texts, texts) == [(5, 11)]


def test_two_transcripts_apart_by_more_than_the_gap_are_two_runs():
    gap = "." * (billing_sheet._RUN_GAP + 1)
    texts = _texts("QQQQQ" + gap + "QQQQQ")

    assert billing_sheet.testimony_runs(texts, texts) == [(1, 5), (13, 17)]


def test_a_gap_of_the_allowed_size_stays_one_transcript():
    texts = _texts("QQQ" + "." * billing_sheet._RUN_GAP + "QQ")

    assert billing_sheet.testimony_runs(texts, texts) == [(1, 11)]


def test_a_few_quoted_questions_are_not_a_transcript():
    """GUARD: a report quoting a deposition (fewer than five pages of questions) is not counted."""
    texts = _texts("..QQQQ..")

    assert billing_sheet.testimony_runs(texts, texts) == []


def test_the_office_count_and_the_deposition_pages():
    parts = [
        {"kind": "advocacy_letter", "start": 1, "end": 2},
        {"kind": "records", "start": 3, "end": 14},
        {"kind": "email", "start": 15, "end": 15},
    ]
    texts = _texts("....QQQQQQ.....")

    counts = billing_sheet.count_pages(parts, 15, texts, lambda first, last: False)

    assert counts == billing_sheet.Counts(office=14, depo=6)


def test_a_condensed_transcript_counts_four_pages_a_sheet_in_both_counts():
    """DEMONSTRATES the office's rule: a condensed sheet prints four transcript pages and counts
    four, in the office count as well as the deposition pages."""
    texts = _texts("..QQQQQ..")
    asked = []

    def condensed(first, last):
        asked.append((first, last))
        return True

    counts = billing_sheet.count_pages(None, 9, texts, condensed)

    assert asked == [(3, 7)]
    assert counts == billing_sheet.Counts(office=9 + 5 * 3, depo=20)


def test_testimony_inside_the_email_is_not_counted():
    parts = [
        {"kind": "records", "start": 1, "end": 3},
        {"kind": "email", "start": 4, "end": 9},
    ]
    texts = _texts("...QQQQQQ")

    counts = billing_sheet.count_pages(parts, 9, texts, lambda first, last: False)

    assert counts == billing_sheet.Counts(office=3, depo=0)


def test_while_a_page_could_still_be_testimony_the_deposition_pages_are_not_known():
    """DEMONSTRATES: with scans not read yet the deposition pages are None - never a short count -
    and the office count stands."""
    counts = billing_sheet.counts_of(
        list(range(1, 10)), [3, 4, 5, 6, 7], lambda first, last: False, complete=False
    )

    assert counts == billing_sheet.Counts(office=9, depo=None)


# --- services/billing_sheet.py: what is printed ----------------------------------------------------


def _doc(**fields) -> Document:
    base = {
        "id": "00000000-0000-0000-0000-000000000000",
        "user_id": 1,
        "original_filename": "x.pdf",
        "stored_path": "x.pdf",
        "patient_first_name": "Pat",
        "patient_last_name": "Sample",
        "patient_id": "A1D5",
        "doctor": DOCTORS[0],
        "visit_type": "QME",
        "appointment_date": date(2026, 12, 3),
        "interpreter": True,
        "records_from": "insurance",
        "received_on": date(2026, 11, 19),
    }
    return Document(**{**base, **fields})


def test_the_sheet_carries_the_typed_fields_and_the_counts():
    sheet = billing_sheet.sheet_for(_doc(declared_pages=40), billing_sheet.Counts(42, 7))

    assert sheet.dos == "12/03/2026"
    assert sheet.patient_name == "Sample, Pat"
    assert sheet.date_received == "11/19/2026"
    assert (sheet.declared_pages, sheet.office_count, sheet.depo_pages) == (40, 42, "7")


def test_a_count_typed_on_the_sheet_wins_over_the_worked_out_one():
    sheet = billing_sheet.sheet_for(
        _doc(office_count=50, depo_pages="0"), billing_sheet.Counts(42, 7)
    )

    assert (sheet.office_count, sheet.depo_pages) == (50, "0")


def test_several_deposition_counts_typed_are_printed_as_typed():
    """DEMONSTRATES the office's answer (2026-11-25): one count per deposition, separated by
    commas, printed on the sheet as typed instead of the worked-out total."""
    sheet = billing_sheet.sheet_for(_doc(depo_pages="120, 85, 4"), billing_sheet.Counts(42, 7))

    assert sheet.depo_pages == "120, 85, 4"
    _, text = _rendered_text(sheet)
    assert "120, 85, 4" in text


def test_while_the_pages_are_read_the_counts_are_blank():
    sheet = billing_sheet.sheet_for(_doc(), None)

    assert (sheet.office_count, sheet.depo_pages) == (None, "")


def test_a_deposition_count_not_known_yet_prints_blank_beside_the_office_count():
    sheet = billing_sheet.sheet_for(_doc(), billing_sheet.Counts(42, None))

    assert (sheet.office_count, sheet.depo_pages) == (42, "")


@pytest.mark.parametrize(("visit", "printed"), [("SUPP", "SUPP"), ("LATE", "LATE MEDS")])
def test_a_visit_with_no_appointment_prints_its_kind_for_its_date_of_service(visit, printed):
    """DEMONSTRATES the office's answers: a supplemental report (2026-11-19) and late meds
    (2026-11-25) have no appointment; the DOS and the type of visit say SUPP or LATE MEDS, even
    when a date was stored before the type of visit changed."""
    sheet = billing_sheet.sheet_for(_doc(visit_type=visit), None)

    assert sheet.dos == printed
    assert sheet.visit_type == printed


def test_late_meds_is_offered_last_and_takes_no_appointment():
    """GUARD: the dropdown's order, and LATE is the stored code (the column holds 8 characters)."""
    assert billing_sheet.VISIT_TYPES == ("AME", "QME", "PQME", "IME", "SUPP", "LATE")
    assert billing_sheet.NO_APPOINTMENT == ("SUPP", "LATE")
    assert all(len(visit) <= 8 for visit in billing_sheet.VISIT_TYPES)
    assert billing_sheet.visit_label("LATE") == "LATE MEDS"
    assert [billing_sheet.visit_label(v) for v in ("AME", "SUPP", "", None)] == [
        "AME",
        "SUPP",
        "",
        "",
    ]


def test_late_meds_is_written_on_the_sheet():
    _, text = _rendered_text(billing_sheet.sheet_for(_doc(visit_type="LATE"), None))

    assert text.count("LATE MEDS") == 2  # DOS and type of visit
    assert "12/03/2026" not in text


def _rendered_text(sheet) -> tuple[int, str]:
    with pymupdf.open(stream=billing_sheet.render(sheet), filetype="pdf") as pdf:
        return pdf.page_count, pdf[0].get_text()


def test_the_sheet_is_one_page_with_its_fields_written_on_it():
    sheet = billing_sheet.sheet_for(
        _doc(
            claim_numbers="ADJ0000001",
            location="Main office",
            declared_pages=40,
            discrepancy_reason="Two missing",
        ),
        billing_sheet.Counts(42, 7),
    )

    pages, text = _rendered_text(sheet)

    assert pages == 1
    for written in (
        DOCTOR_NAMES[DOCTORS[0]],
        "12/03/2026",
        "Sample, Pat",
        "A1D5",
        "ADJ0000001",
        "Main office",
        "QME",
        "40",
        "42",
        "7",
        "None",
        "Two missing",
        "11/19/2026",
        "IN-HOUSE",
        "Billing Code:",
    ):
        assert written in text, written


def test_every_doctor_on_the_list_has_the_name_the_sheet_prints():
    # GUARD: one list, two halves - a doctor added for the header's font must not print a blank
    # (or a surname) at the top of the billing sheet, nor a name be kept for a doctor removed.
    assert set(DOCTOR_NAMES) == set(DOCTOR_FONTS)
    for surname, name in DOCTOR_NAMES.items():
        assert surname.upper() in name, surname
        assert name == name.upper(), surname


@pytest.mark.parametrize("surname", DOCTORS)
def test_the_sheet_is_headed_by_the_doctors_full_name_at_full_size(surname):
    # DEMONSTRATES: the office's form names its doctor in full at the top (2026-11-20).
    sheet = billing_sheet.Sheet(doctor=surname)

    with pymupdf.open(stream=billing_sheet.render(sheet), filetype="pdf") as pdf:
        spans = [
            span
            for block in pdf[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip() == DOCTOR_NAMES[surname]
        ]

    assert len(spans) == 1
    assert round(spans[0]["size"]) == 22


def test_a_doctor_no_longer_on_the_list_prints_as_stored():
    assert billing_sheet.doctor_name("Retired") == "Dr. Retired"
    assert billing_sheet.doctor_name("  ") == ""
    assert billing_sheet.doctor_name(None) == ""


def test_a_price_prints_its_amount_in_bold_and_what_it_includes_in_italics():
    # The office's form: "$2,015.00" bold, "-includes 200 pages of records" in italics.
    with pymupdf.open(stream=billing_sheet.render(billing_sheet.Sheet()), filetype="pdf") as pdf:
        fonts = {
            span["text"].strip(): span["font"]
            for block in pdf[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
        }

    assert "Bold" in fonts["$2,015.00"]
    assert "Oblique" in fonts["-includes 200 pages of records"]
    assert "Bold" in fonts["$503.75"]


@pytest.mark.parametrize("stored", [None, "edata", "in_house", "leonardo"])
def test_the_sheet_always_says_in_house_at_the_top(stored):
    """DEMONSTRATES the office's answer of 2026-11-20: every record is reviewed in house, so the
    title says IN-HOUSE alone - whatever a record from before the answer stored."""
    _, text = _rendered_text(billing_sheet.sheet_for(_doc(review_source=stored), None))

    assert "IN-HOUSE" in text
    assert "E-DATA" not in text
    assert "LEONARDO" not in text


def test_the_location_box_is_blank_when_none_was_typed():
    """GUARD: a record from before the location was asked for keeps an empty box."""
    sheet = billing_sheet.sheet_for(_doc(), None)

    assert sheet.location == ""
    assert "Location:" in _rendered_text(sheet)[1]


def test_a_long_value_is_made_to_fit_its_space():
    sheet = billing_sheet.sheet_for(_doc(claim_numbers="ADJ0000001, " * 30), None)

    _, text = _rendered_text(sheet)

    assert "ADJ0000001" in text
    assert "..." in text


def test_a_blank_sheet_still_renders():
    pages, text = _rendered_text(billing_sheet.Sheet())

    assert pages == 1
    assert "Patient Name:" in text


# --- services/billing_sheet.py: the sheet on top of the raw download ------------------------------


def _record_pdf(path, pages: int) -> bytes:
    """A record of ``pages`` pages written to ``path``; returns its bytes."""
    with pymupdf.open() as doc:
        for number in range(pages):
            doc.new_page(width=200, height=200).insert_text((20, 40), f"record page {number + 1}")
        doc.save(path)
    return path.read_bytes()


def _a_sheet() -> bytes:
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "SYNTHETIC SHEET")
        return doc.tobytes()


def _page_texts(path) -> list[str]:
    with pymupdf.open(path) as pdf:
        return [page.get_text().strip() for page in pdf]


_ON_TOP = ["SYNTHETIC SHEET", "record page 1", "record page 2", "record page 3"]


def test_the_sheet_goes_on_top_of_a_copy_and_the_record_is_left_as_it_was(tmp_path):
    """DEMONSTRATES: page 1 is the sheet, then the record page for page. The record's own file is
    not touched, and the copy keeps every byte of it - the sheet is an update after them."""
    record = tmp_path / "record.pdf"
    original = _record_pdf(record, 3)
    out = tmp_path / "out.pdf"

    billing_sheet.put_on_top(_a_sheet(), record, out)

    assert record.read_bytes() == original
    assert out.read_bytes().startswith(original)
    assert _page_texts(out) == _ON_TOP


def test_the_copy_keeps_the_records_encryption_as_pymupdf_names_it():
    """GUARD: the constant stands in for one the package's type stubs do not export."""
    assert billing_sheet._KEEP_ENCRYPTION == pymupdf.PDF_ENCRYPT_KEEP


def test_the_same_sheet_on_the_same_record_gives_the_same_bytes(tmp_path):
    """GUARD: a second download of an unchanged sheet is the same file (a resumed download splices
    two of them)."""
    record = tmp_path / "record.pdf"
    _record_pdf(record, 2)

    billing_sheet.put_on_top(_a_sheet(), record, tmp_path / "a.pdf")
    billing_sheet.put_on_top(_a_sheet(), record, tmp_path / "b.pdf")

    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_a_record_a_reader_must_repair_is_written_whole_with_the_sheet_on_top(tmp_path):
    """DEMONSTRATES the fallback: a file without its cross-reference table cannot take an update,
    so the copy is written whole - still the sheet first, the record left as it was, no scrap
    file."""
    good = _record_pdf(tmp_path / "good.pdf", 3)
    damaged = good[: good.rindex(b"endobj") + len(b"endobj")] + b"\n%%EOF\n"
    record = tmp_path / "record.pdf"
    record.write_bytes(damaged)
    out = tmp_path / "out.pdf"

    billing_sheet.put_on_top(_a_sheet(), record, out)

    assert record.read_bytes() == damaged
    assert _page_texts(out) == _ON_TOP
    assert sorted(p.name for p in tmp_path.iterdir()) == ["good.pdf", "out.pdf", "record.pdf"]


# --- POST /api/documents/combine: the billing fields ----------------------------------------------


_BILLING = {
    "visit_type": "AME",
    "doctor": DOCTORS[1],
    "interpreter": "yes",
    "claim_numbers": "ADJ0000001, ADJ0000002",
    "location": "Main office",
    "previous_visit": "2026-06-14",
    "declared_pages": "6",
}


async def test_the_billing_fields_typed_at_upload_are_stored(client):
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client, **_BILLING)

    assert resp.status_code == 201, resp.text
    document = _document(resp.json()["id"])
    assert document.visit_type == "AME"
    assert document.doctor == DOCTORS[1]
    assert document.review_source is None
    assert document.interpreter is True
    assert document.claim_numbers == "ADJ0000001, ADJ0000002"
    assert document.location == "Main office"
    assert document.previous_visit == date(2026, 6, 14)
    assert document.declared_pages == 6
    assert document.appointment_date == date(2026, 12, 1)


@pytest.mark.parametrize("visit", ["SUPP", "LATE"])
async def test_a_visit_with_no_appointment_needs_no_appointment_date(client, visit):
    """DEMONSTRATES: SUPP and late meds have no appointment - the date is not asked for, and one
    sent is dropped."""
    _, email = _user()
    await _login(client, email)

    without = await _combine(client, visit_type=visit, appointment_date=None)
    with_one = await _combine(client, visit_type=visit)

    assert without.status_code == 201, without.text
    assert with_one.status_code == 201, with_one.text
    assert _document(without.json()["id"]).appointment_date is None
    assert _document(with_one.json()["id"]).appointment_date is None


async def test_the_optional_billing_fields_may_be_left_blank(client):
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client)

    assert resp.status_code == 201, resp.text
    document = _document(resp.json()["id"])
    assert document.doctor is None
    assert document.claim_numbers is None
    assert document.location is None
    assert document.previous_visit is None
    assert document.declared_pages is None
    assert document.interpreter is False


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"visit_type": None}, "Choose the type of visit."),
        ({"visit_type": "visit"}, "Choose the type of visit."),
        ({"interpreter": None}, "Choose whether an interpreter is needed."),
        ({"interpreter": "maybe"}, "Choose whether an interpreter is needed."),
        ({"doctor": "Nobody"}, "Choose the doctor from the list."),
        ({"previous_visit": "06/14/2026"}, "Enter the previous office visit as a date."),
        ({"declared_pages": "six"}, "Enter the pages the declaration states as a whole number."),
        ({"declared_pages": "-3"}, "Enter the pages the declaration states as a whole number."),
        ({"claim_numbers": "A" * 256}, "The claim numbers are at most 255 characters."),
        ({"location": "A" * 256}, "The location is at most 255 characters."),
    ],
)
async def test_a_billing_field_the_sheet_cannot_hold_is_refused(client, overrides, detail):
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client, **overrides)

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    with get_sessionmaker()() as session:
        assert session.scalars(select(Document).where(Document.user_id == user_id)).all() == []


async def test_who_reviews_the_records_is_not_asked(client):
    """DEMONSTRATES: the upload needs no reviewer of the records, and a page left open from before
    the change that still sends one is not refused for it."""
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client, review_source="outside")

    assert resp.status_code == 201, resp.text
    assert _document(resp.json()["id"]).review_source is None
    got = (await client.get(f"/api/documents/{resp.json()['id']}/billing")).json()
    assert "review_source" not in got


async def test_the_upload_dialog_is_offered_the_headers_doctors(client):
    _, email = _user()
    await _login(client, email)

    resp = await client.get("/api/documents/combine/doctors")

    assert resp.status_code == 200
    assert resp.json() == {"doctors": list(DOCTORS)}


# --- GET/PUT /api/documents/{id}/billing, GET .../billing-sheet ----------------------------------


def _read_pages(doc_id: str, spec: str) -> None:
    """Store page text as the upload's reading would: ``Q`` testimony, ``.`` prose."""
    with get_sessionmaker()() as session:
        for page, char in enumerate(spec, start=1):
            session.add(
                PageText(
                    document_id=doc_id, page=page, text=_testimony() if char == "Q" else _PROSE
                )
            )
        session.commit()


# Three received files: the email (1 page), the letter (2 pages), the records (3 pages).
_SPEC = "." + ".." + "..."


async def _record(client, **overrides) -> str:
    _, email = _user()
    await _login(client, email)
    resp = await _combine(client, **{**_BILLING, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_the_office_count_is_known_at_once_and_the_deposition_pages_when_read(client):
    """DEMONSTRATES: right after the upload, before any page is read, the sheet has its office
    count; only the deposition pages wait for the reading, known once the last page is read."""
    doc_id = await _record(client)

    before = (await client.get(f"/api/documents/{doc_id}/billing")).json()
    _read_pages(doc_id, _SPEC[:4])
    partway = (await client.get(f"/api/documents/{doc_id}/billing")).json()
    _read_pages_from(doc_id, 5, _SPEC[4:])
    after = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert before["counted"] == {"office": 5, "depo": None}
    assert (before["pages_counted"], before["pages_unread"]) == (5, 5)
    assert before["doctors"] == list(DOCTORS)
    assert partway["counted"] == {"office": 5, "depo": None}
    assert partway["pages_unread"] == 2
    assert after["counted"] == {"office": 5, "depo": 0}
    assert after["pages_unread"] == 0


def _read_pages_from(doc_id: str, first: int, spec: str) -> None:
    """Store page text from page ``first`` on, as the reading goes on."""
    with get_sessionmaker()() as session:
        for page, char in enumerate(spec, start=first):
            session.add(
                PageText(
                    document_id=doc_id, page=page, text=_testimony() if char == "Q" else _PROSE
                )
            )
        session.commit()


# Three received files: the email (1 page), the letter (2 pages), and records of 7 pages holding a
# transcript of five pages of testimony after a cover page.
_DEPO_FILES = [
    ("email", _pdf(1, width=111)),
    ("advocacy_letter", _pdf(2, width=112)),
    ("records", _pdf(7, width=113)),
]
_DEPO_SPEC = "." + ".." + ".QQQQQ."


async def test_a_transcripts_condensed_check_is_done_once(client, monkeypatch):
    """GUARD: the condensed check draws the transcript's first sheets; the dialog reads the sheet
    every few seconds, so the answer is kept rather than drawn again each time."""
    _, email = _user()
    await _login(client, email)
    resp = await _combine(client, files=_DEPO_FILES, **_BILLING)
    doc_id = resp.json()["id"]
    _read_pages(doc_id, _DEPO_SPEC)
    asked = []

    def condensed(path, first, last, max_sheets=6):
        asked.append((first, last))
        return True

    monkeypatch.setattr(billing_sheet, "has_condensed_sheets", condensed)

    first = (await client.get(f"/api/documents/{doc_id}/billing")).json()
    second = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert asked == [(5, 9)]
    assert first["counted"] == second["counted"] == {"office": 9 + 5 * 3, "depo": 20}


async def test_without_redis_the_sheet_is_still_counted(client, monkeypatch):
    """GUARD: the kept condensed answer is a shortcut only; with Redis out of reach the sheet still
    answers, checking again."""
    _, email = _user()
    await _login(client, email)
    resp = await _combine(client, files=_DEPO_FILES, **_BILLING)
    doc_id = resp.json()["id"]
    _read_pages(doc_id, _DEPO_SPEC)

    def no_redis():
        raise RedisError("unreachable")

    monkeypatch.setattr(billing_sheet, "get_redis", no_redis)
    monkeypatch.setattr(billing_sheet, "has_condensed_sheets", lambda *a, **k: False)

    got = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert got["counted"] == {"office": 9, "depo": 5}


async def test_once_read_the_sheet_has_its_counts(client):
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)

    got = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert got["counted"] == {"office": 5, "depo": 0}
    assert got["pages_unread"] == 0
    assert got["visit_type"] == "AME"
    assert got["appointment_date"] == "2026-12-01"
    assert got["interpreter"] == "yes"
    assert got["declared_pages"] == 6
    assert got["location"] == "Main office"
    assert got["office_count"] == ""
    assert got["records_from"] == "applicant"
    assert got["received_on"] == "2026-11-03"


async def test_the_sheet_is_edited_and_the_change_audited_by_name(client):
    doc_id = await _record(client)
    payload = {
        **{key: "" for key in ("doctor", "claim_numbers", "previous_visit")},
        "location": "Second office",
        "visit_type": "QME",
        "appointment_date": "2026-12-09",
        "interpreter": "no",
        "declared_pages": "5",
        "discrepancy_reason": "One page more than declared",
        "records_from": "defense",
        "office_count": "",
        "depo_pages": "3",
    }

    resp = await client.put(f"/api/documents/{doc_id}/billing", json=payload)

    assert resp.status_code == 200, resp.text
    document = _document(doc_id)
    assert document.visit_type == "QME"
    assert document.appointment_date == date(2026, 12, 9)
    assert document.doctor is None
    assert document.interpreter is False
    assert document.discrepancy_reason == "One page more than declared"
    assert document.records_from == "defense"
    assert document.location == "Second office"
    assert (document.office_count, document.depo_pages) == (None, "3")
    assert resp.json()["depo_pages"] == "3"
    with get_sessionmaker()() as session:
        details = session.scalars(
            select(AuditLog.detail).where(
                AuditLog.document_id == doc_id, AuditLog.action == "billing.edit"
            )
        ).all()
    assert details == [
        "changed=appointment_date,claim_numbers,declared_pages,depo_pages,discrepancy_reason,"
        "doctor,interpreter,location,previous_visit,records_from,visit_type"
    ]


@pytest.mark.parametrize("visit", ["SUPP", "LATE"])
async def test_a_visit_with_no_appointment_on_the_sheet_drops_the_date(client, visit):
    doc_id = await _record(client)

    resp = await client.put(
        f"/api/documents/{doc_id}/billing",
        json={"visit_type": visit, "appointment_date": "2026-12-01"},
    )

    assert resp.status_code == 200, resp.text
    assert _document(doc_id).appointment_date is None
    assert resp.json()["appointment_date"] == ""


async def test_a_doctor_stored_before_the_list_changed_is_kept(client):
    doc_id = await _record(client)
    with get_sessionmaker()() as session:
        session.get(Document, doc_id).doctor = "Retired"
        session.commit()

    kept = await client.put(f"/api/documents/{doc_id}/billing", json={"doctor": "Retired"})
    other = await client.put(f"/api/documents/{doc_id}/billing", json={"doctor": "Nobody"})

    assert kept.status_code == 200, kept.text
    assert other.status_code == 400
    assert other.json()["detail"] == "Choose the doctor from the list."


_DEPO_DETAIL = (
    "Enter the deposition pages as whole numbers, separated by commas when there are several."
)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("120, 85", "120, 85"),
        ("120,85", "120, 85"),
        (" 120 ,  85 , ", "120, 85"),
        ("007", "7"),
        ("0", "0"),
        (",", None),
        ("", None),
    ],
)
async def test_several_deposition_counts_are_kept_as_the_sheet_prints_them(client, typed, stored):
    """DEMONSTRATES the office's answer (2026-11-25): one count per deposition, separated by
    commas; stored and answered the way the sheet prints them. Blank goes back to the
    worked-out count."""
    doc_id = await _record(client)

    resp = await client.put(f"/api/documents/{doc_id}/billing", json={"depo_pages": typed})

    assert resp.status_code == 200, resp.text
    assert _document(doc_id).depo_pages == stored
    assert resp.json()["depo_pages"] == (stored or "")


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("office_count", "many", "Enter the office count as a whole number."),
        ("depo_pages", "1.5", _DEPO_DETAIL),
        ("depo_pages", "120 and 85", _DEPO_DETAIL),
        ("depo_pages", "120, -5", _DEPO_DETAIL),
        ("depo_pages", ", ".join(["100"] * 60), _DEPO_DETAIL),  # over the sheet's 255
        (
            "discrepancy_reason",
            "x" * 256,
            "The reason for the discrepancy is at most 255 characters.",
        ),
        ("records_from", "attorney", "Choose who sent the records."),
        ("location", "x" * 256, "The location is at most 255 characters."),
        ("appointment_date", "12/01/2026", "Enter the appointment date."),
    ],
)
async def test_an_edit_the_sheet_cannot_hold_is_refused_and_nothing_changes(
    client, field, value, detail
):
    doc_id = await _record(client)

    resp = await client.put(
        f"/api/documents/{doc_id}/billing", json={"visit_type": "IME", field: value}
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert _document(doc_id).visit_type == "AME"


def _printed(monkeypatch) -> list[billing_sheet.Sheet]:
    """The sheets the routes draw, as handed to ``render``."""
    drawn: list[billing_sheet.Sheet] = []
    real = billing_sheet.render
    monkeypatch.setattr(billing_sheet, "render", lambda sheet: drawn.append(sheet) or real(sheet))
    return drawn


async def test_the_sheet_downloads_at_once_with_a_deposition_count_not_known_left_blank(
    client, monkeypatch
):
    """DEMONSTRATES: the sheet never waits for the record's pages to be read. While scans are still
    being read the deposition pages print blank; the office count is printed."""
    doc_id = await _record(client)
    drawn = _printed(monkeypatch)

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert (drawn[0].office_count, drawn[0].depo_pages) == (5, "")


async def test_both_counts_typed_let_the_sheet_download_before_the_reading_ends(client):
    doc_id = await _record(client)
    await client.put(
        f"/api/documents/{doc_id}/billing",
        json={"visit_type": "AME", "office_count": "9", "depo_pages": "0"},
    )

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 200, resp.text


async def test_the_sheet_downloads_as_a_pdf_named_after_the_record(client):
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["cache-control"] == "no-store"
    # The name has spaces, so it goes in the RFC 5987 form, as Starlette's own file downloads do.
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=utf-8''Synthetic_Patient-801234-11032026%20Billing%20Sheet.pdf"
    )
    with pymupdf.open(stream=resp.content, filetype="pdf") as pdf:
        text = pdf[0].get_text()
    assert "Synthetic, Patient" in text
    assert "12/01/2026" in text
    with get_sessionmaker()() as session:
        actions = session.scalars(
            select(AuditLog.action).where(AuditLog.document_id == doc_id)
        ).all()
    assert "download_billing" in actions
    assert _document(doc_id).downloaded_at is None


async def test_another_account_cannot_read_or_change_the_sheet(client):
    """GUARD: once its reviewer has taken it out of the team queue, the record is theirs."""
    doc_id = await _record(client)
    _taken(doc_id)
    _, other = _user()
    await _login(client, other)

    assert (await client.get(f"/api/documents/{doc_id}/billing")).status_code == 404
    assert (
        await client.put(f"/api/documents/{doc_id}/billing", json={"visit_type": "IME"})
    ).status_code == 404
    assert (await client.get(f"/api/documents/{doc_id}/billing-sheet")).status_code == 404


# --- GET /api/documents/{id}/raw: the billing sheet on top ----------------------------------------


def _built_files() -> list[str]:
    return [p.name for p in Path(get_settings().upload_folder).glob("*/downloads/*")]


def _actions(doc_id: str) -> list[str]:
    with get_sessionmaker()() as session:
        return list(
            session.scalars(select(AuditLog.action).where(AuditLog.document_id == doc_id)).all()
        )


async def test_the_raw_download_starts_with_the_billing_sheet(client):
    """DEMONSTRATES the office's ask (2026-11-23): page 1 of the raw download is the billing sheet,
    then the record as received, byte for byte. The stored record never holds the sheet - Identify
    and the summaries never see it - and the copy built for the download is gone once sent."""
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)
    stored_path = _document(doc_id).stored_path
    with open(stored_path, "rb") as fh:
        stored = fh.read()

    resp = await client.get(f"/api/documents/{doc_id}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(stored)
    with open(stored_path, "rb") as fh:
        assert fh.read() == stored
    with pymupdf.open(stream=resp.content, filetype="pdf") as pdf:
        pages = pdf.page_count
        sheet_text = pdf[0].get_text()
    assert pages == len(_SPEC) + 1
    assert "Synthetic, Patient" in sheet_text
    assert "12/01/2026" in sheet_text
    assert resp.headers["content-type"] == "application/pdf"
    assert 'filename="Synthetic_Patient-801234-11032026.pdf"' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "no-store"
    assert _built_files() == []
    assert "download_raw" in _actions(doc_id)
    assert _document(doc_id).downloaded_at is None


async def test_the_raw_download_carries_the_sheet_as_it_stands_now(client):
    """DEMONSTRATES: the sheet on top is built at the download, so an edit made after the upload is
    on it - as on the sheet's own download."""
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)
    await client.put(
        f"/api/documents/{doc_id}/billing",
        json={"visit_type": "IME", "location": "Synthetic Second Office"},
    )

    resp = await client.get(f"/api/documents/{doc_id}/raw")

    with pymupdf.open(stream=resp.content, filetype="pdf") as pdf:
        sheet_text = pdf[0].get_text()
    assert "Synthetic Second Office" in sheet_text


async def test_the_raw_download_never_waits_for_the_pages_to_be_read(client, monkeypatch):
    """DEMONSTRATES the fix for the uploader's report (2026-11-24): right after the upload, with no
    page read, the raw PDF downloads with the sheet on top - its deposition pages blank."""
    doc_id = await _record(client)
    stored_path = _document(doc_id).stored_path
    with open(stored_path, "rb") as fh:
        stored = fh.read()
    drawn = _printed(monkeypatch)

    resp = await client.get(f"/api/documents/{doc_id}/raw")

    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(stored)
    assert (drawn[0].office_count, drawn[0].depo_pages) == (5, "")
    assert _built_files() == []
    assert "download_raw" in _actions(doc_id)


async def test_a_raw_download_that_fails_to_build_leaves_no_file(client, monkeypatch):
    """GUARD: patient data at rest is not left behind by a build that fails part way."""
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)

    def half_written(sheet_pdf, record_path, out_path):
        Path(out_path).write_bytes(b"%PDF-1.7 half")
        raise OSError("disk full")

    monkeypatch.setattr(billing_sheet, "put_on_top", half_written)

    with pytest.raises(OSError):
        await client.get(f"/api/documents/{doc_id}/raw")

    assert _built_files() == []
    assert "download_raw" not in _actions(doc_id)


def test_the_fields_the_upload_requires_are_in_the_helper():
    """GUARD for the other test module: its upload form carries every required billing field."""
    for field in ("visit_type", "interpreter"):
        assert _FIELDS[field]


# --- migration a9c4e7b2d815: a count typed before the list is kept ----------------------------------


def test_the_migration_keeps_every_deposition_count_already_typed():
    """DEMONSTRATES the copy in `a9c4e7b2d815`: a single count typed before 2026-11-25 is carried
    into the new text column as it was, a record with none stays empty, and the sheet prints the
    same number. Synthetic rows, rolled back."""
    import importlib.util

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "a9c4e7b2d815_documents_depo_page_counts.py"
    )
    spec = importlib.util.spec_from_file_location("depo_counts_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with get_sessionmaker()() as session:
        owner_id, _ = _user()

        def doc(n: int, typed: int | None) -> Document:
            return Document(
                id=f"depocopy{n}-{owner_id}",
                user_id=owner_id,
                original_filename="x.pdf",
                stored_path="x.pdf",
                sha256="0" * 64,
                page_count=1,
                depo_pages_number=typed,
            )

        typed, zero, untyped = doc(1, 120), doc(2, 0), doc(3, None)
        session.add_all([typed, zero, untyped])
        session.flush()

        session.connection().exec_driver_sql(migration.COPY_TYPED_COUNTS)
        session.expire_all()

        assert session.get(Document, typed.id).depo_pages == "120"
        assert session.get(Document, zero.id).depo_pages == "0"
        assert session.get(Document, untyped.id).depo_pages is None
        sheet = billing_sheet.sheet_for(session.get(Document, typed.id), billing_sheet.Counts(5, 7))
        assert sheet.depo_pages == "120"
        session.rollback()
