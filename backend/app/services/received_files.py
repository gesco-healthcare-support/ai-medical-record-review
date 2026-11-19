"""A record put together from the files it arrived in.

Records reach the office as several files - the advocacy letter, the declaration, the medical index,
the medical records and depositions, the proof of service, the internal email they came with - and
the office used to join them in Adobe Acrobat before uploading one PDF. How they are split is not
consistent: sometimes everything is one PDF, sometimes one file per source, sometimes only the
letter or the depositions are apart.

The web app now does that step. Each file is sent on its own as the uploader adds it
(``POST /api/documents/combine/files``): it is kept for a while in the uploader's staging folder
(``stage``), its pages are counted, and its first page is read - the text the PDF carries, else one
OCR pass - to tell what kind of file it is (``guess_kind``). The list is then kept in the office's
order whatever order the files were added in, so the order does not depend on the uploader. Once the
patient's details are given, ``POST /api/documents/combine`` joins the staged files (``combine``)
into the record's PDF. That joined file is the raw record: read and identified like any other
upload, and downloadable as it is for the office's backup.

The order (``KINDS``) is the office's own guideline (2026-11-18, confirmed 2026-11-19): advocacy
letter, declaration, medical index (the sender's list of the records in the file), medical records
with their depositions, proof of service, internal email; an envelope last. The finished records
already on the live box were put together differently (the office's cover sheet first, then the
email; measured 2026-11-18): the cover sheet is no longer used and the guideline replaces that
order. The uploader can still change a kind or move a file; the server joins the files in the order
it is sent.

The uploader also says who sent the records (``PARTIES``, one party per record) and the date of the
appointment: neither is reliably on the letter, and sometimes there is no letter.

The record's file name is built the way the office names its files - ``Last_First-ID-MMDDYYYY`` (the
date the records were received), 647 of 703 live file names - so ``patient_ids.from_filename`` reads
the ID back from it.

STAGED FILES ARE PATIENT DATA AT REST: ``<upload folder>/<account>/staged/<token>.pdf``, named by a
random token, never by the patient. A file is deleted when its record is put together, when the
uploader removes it or closes the dialog, and otherwise by ``sweep`` after ``STAGED_HOURS``.

Nothing here logs a file name, a patient field or page text: they are patient data.
"""

import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pymupdf

from app.services.ocr import extract_text_from_selected_pages

# The kinds of file, in the order a record is put together. The labels are the web app's.
KINDS: tuple[str, ...] = (
    "advocacy_letter",
    "declaration",
    "medical_index",
    "records",
    "proof_of_service",
    "email",
    "envelope",
)
KIND_LABELS: dict[str, str] = {
    "advocacy_letter": "Advocacy letter",
    "declaration": "Declaration",
    "medical_index": "Medical index",
    "records": "Medical records",
    "proof_of_service": "Proof of service",
    "email": "Internal email",
    "envelope": "Envelope",
}

# Who sent the records: the four rows of the office's billing sheet, one per record.
PARTIES: tuple[str, ...] = ("defense", "applicant", "insurance", "copy_service")
PARTY_LABELS: dict[str, str] = {
    "defense": "Defense",
    "applicant": "Applicant",
    "insurance": "Insurance",
    "copy_service": "Copy service",
}

# How long a staged file waits for its record to be put together before ``sweep`` deletes it.
STAGED_HOURS = 6

# A name part keeps letters, digits, apostrophes and hyphens; anything else separates words, which
# the file name joins with "_" as the office does.
_NAME_WORD = re.compile(r"[A-Za-z0-9'-]+")

# `secrets.token_urlsafe(24)` is always 32 characters from this alphabet. Anything else is refused
# before it goes near a path.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32}")

# Fewer characters than this in the PDF's own text on page 1 means a scan, read by OCR instead.
_ENOUGH_TEXT = 40

# The first-page signs of each kind. Lower-case text; checked in `guess_kind`'s order.
_PROOF_OF_SERVICE = re.compile(
    r"proof of (electronic )?service|declaration of service|certificate of service"
)
_EMAIL_HEADER = re.compile(r"(?m)^\W{0,3}(from|sent|to|subject|cc|date)\s*:")
_MEDICAL_INDEX = re.compile(
    r"previously sent|attention\s*:?\s*medical providers|index of (the )?(medical )?records"
    r"|records? index|medical index"
)
_DECLARATION = re.compile(
    r"declaration|affidavit|attest|custodian of records|certification of records"
)
_DECLARATION_BODY = re.compile(
    r"custodian|penalty of perjury|true (and correct )?cop|4062\.3|records"
)
_LETTER = re.compile(
    r"dear (dr|doctor)\b|\badvocacy\b|\bre\s*:.{0,400}?\b(qme|ame|evaluat|examination)", re.S
)
_ENVELOPE = re.compile(
    r"\b(usps|postage|priority mail|first[- ]class|presorted|return service requested"
    r"|certified mail)\b"
)
# The office's main declaration is a few pages; a long file that opens with a custodian's
# declaration is a copy service's records, whose declarations the office ignores (2026-11-19).
_DECLARATION_MAX_PAGES = 5
_ENVELOPE_MAX_PAGES = 2


class UnreadableFile(Exception):
    """One of the files is not a PDF with pages. ``position`` is its place in the upload, from 1."""

    def __init__(self, position: int = 1):
        super().__init__(position)
        self.position = position


@dataclass(frozen=True)
class Part:
    """One file of a combined record: what it is, and the pages it fills (from 1, inclusive)."""

    kind: str
    start: int
    end: int

    def as_dict(self) -> dict:
        return {"kind": self.kind, "start": self.start, "end": self.end}


def _name_part(value: str) -> str:
    return "_".join(_NAME_WORD.findall(value))


def record_filename(last_name: str, first_name: str, patient_id: str, received: date) -> str:
    """``Last_First-ID-MMDDYYYY.pdf``, the office's own way of naming a record's file.

    A name of several words keeps every word (``De_La_Cruz_Maria-...``); a character a file name
    cannot carry separates words instead."""
    name = "_".join(p for p in (_name_part(last_name), _name_part(first_name)) if p)
    return f"{name}-{patient_id}-{received:%m%d%Y}.pdf"


# --- staging --------------------------------------------------------------------------------------


def staging_folder(upload_folder: str, user_id: int) -> Path:
    return Path(upload_folder) / str(user_id) / "staged"


def new_token() -> str:
    return secrets.token_urlsafe(24)


def staged_path(upload_folder: str, user_id: int, token: str) -> Path | None:
    """Where ``user_id``'s staged file ``token`` lives, or None for a token that is not one of ours.

    A file is only ever looked up in the caller's own folder, so no account can name another's."""
    if not _TOKEN.fullmatch(token or ""):
        return None
    return staging_folder(upload_folder, user_id) / f"{token}.pdf"


def sweep(upload_folder: str, now: float | None = None) -> int:
    """Delete every account's staged files older than ``STAGED_HOURS``; return how many went.

    A file that cannot be deleted is left for the next sweep."""
    cutoff = (time.time() if now is None else now) - STAGED_HOURS * 3600
    removed = 0
    for path in Path(upload_folder).glob("*/staged/*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def remove_staged(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --- reading a file -------------------------------------------------------------------------------


def page_count(path: str | Path) -> int:
    """How many pages the PDF at ``path`` has. Raises ``UnreadableFile`` for anything that is not a
    PDF with at least one page."""
    # A PDF says so in its first kilobyte. Checked first so that a file of another kind is refused
    # without being handed to PyMuPDF at all.
    try:
        with open(path, "rb") as handle:
            looks_like_a_pdf = b"%PDF-" in handle.read(1024)
    except OSError:
        raise UnreadableFile() from None
    if not looks_like_a_pdf:
        raise UnreadableFile()
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            count = doc.page_count
    except Exception:
        # PyMuPDF raises several types for an empty or damaged file.
        raise UnreadableFile() from None
    if count <= 0:
        raise UnreadableFile()
    return count


def first_page_text(path: str | Path) -> str:
    """The first page's text: what the PDF itself carries, or one OCR pass of the page for a scan.

    Best effort: an OCR failure (or no Tesseract) gives whatever text the PDF had, maybe none - the
    kind falls back to the file's name in the web app, and the uploader can change it."""
    text = ""
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.page_count:
                text = str(doc[0].get_text("text"))
    except Exception:
        return ""
    if len(text.strip()) >= _ENOUGH_TEXT:
        return text
    try:
        return extract_text_from_selected_pages(str(path), [1]) or text
    except Exception:
        return text


def _is_email(low: str) -> bool:
    headers = {match.group(1) for match in _EMAIL_HEADER.finditer(low[:2000])}
    return "from" in headers and bool({"subject", "sent"} & headers) and len(headers) >= 3


def guess_kind(text: str, pages: int) -> str:
    """What kind of file a first page of ``text`` opens (one of ``KINDS``); ``records`` when nothing
    says otherwise - most files are records. A guess: the uploader can change it."""
    low = text.lower()
    if _PROOF_OF_SERVICE.search(low):
        return "proof_of_service"
    if _is_email(low):
        return "email"
    if _MEDICAL_INDEX.search(low[:1500]):
        return "medical_index"
    if (
        pages <= _DECLARATION_MAX_PAGES
        and _DECLARATION.search(low)
        and _DECLARATION_BODY.search(low)
    ):
        return "declaration"
    if _LETTER.search(low[:3000]):
        return "advocacy_letter"
    if pages <= _ENVELOPE_MAX_PAGES and _ENVELOPE.search(low):
        return "envelope"
    return "records"


# --- joining --------------------------------------------------------------------------------------


def combine(sources: list[tuple[str | Path, str]], out_path: str) -> list[Part]:
    """Join ``[(pdf_path, kind)]`` into one PDF at ``out_path``, in the order given.

    Returns each file's place in the joined PDF. Raises ``UnreadableFile`` - writing nothing - for
    the first file that is not a readable PDF with at least one page: a record silently missing one
    of the files it arrived in is worse than an upload refused (the old split upload skipped
    them). PyMuPDF copies the pages as they are; measured on 2,000 scan-sized pages it needs about
    the joined file's size in memory, half what pypdf needed."""
    out = pymupdf.open()
    parts: list[Part] = []
    page = 1
    try:
        for position, (path, kind) in enumerate(sources, start=1):
            try:
                with pymupdf.open(path, filetype="pdf") as source:
                    count = source.page_count
                    if count > 0:
                        out.insert_pdf(source)
            except Exception:
                raise UnreadableFile(position) from None
            if count <= 0:
                raise UnreadableFile(position)
            parts.append(Part(kind, page, page + count - 1))
            page += count
        out.save(out_path)
    finally:
        out.close()
    return parts
