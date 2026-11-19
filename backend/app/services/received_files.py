"""A record put together from the files it arrived in.

Records reach the office as several files - the advocacy letter, the declaration, the medical index,
the medical records and depositions, the proof of service, the internal email they came with - and
the office used to join them in Adobe Acrobat before uploading one PDF. How they are split is not
consistent: sometimes everything is one PDF, sometimes one file per source, sometimes only the
letter or the depositions are apart. ``POST /api/documents/combine`` takes the
files instead and joins them here, in the order the uploader confirmed. The joined file is the raw
record: it is stored as the record's PDF, read and identified like any other upload, and can be
downloaded as it is for the office's backup.

The order the files are offered in (``KINDS``) is the office's own guideline (2026-11-18, confirmed
2026-11-19): advocacy letter, declaration, medical index (the sender's list of the records in the
file), medical records with their depositions, proof of service, internal email; an envelope last.
The finished records already on the live box were put together differently (the office's cover
sheet first, then the email; measured 2026-11-18): the cover sheet is no longer used and the
guideline replaces that order. The uploader can still move any file; the server joins the files in
the order it is sent.

The uploader also says who sent the records (``PARTIES``, one party per record) and the date of the
appointment: neither is reliably on the letter, and sometimes there is no letter.

The record's file name is built the way the office names its files - ``Last_First-ID-MMDDYYYY`` (the
date the records were received), 647 of 703 live file names - so ``patient_ids.from_filename`` reads
the ID back from it.

Nothing here logs a file name or a patient field: they are patient data.
"""

import re
from dataclasses import dataclass
from datetime import date

from pypdf import PdfReader, PdfWriter

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

# A name part keeps letters, digits, apostrophes and hyphens; anything else separates words, which
# the file name joins with "_" as the office does.
_NAME_WORD = re.compile(r"[A-Za-z0-9'-]+")


class UnreadableFile(Exception):
    """One of the files is not a PDF with pages. ``position`` is its place in the upload, from 1."""

    def __init__(self, position: int):
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


def combine(sources: list[tuple[str, str]], out_path: str) -> list[Part]:
    """Join ``[(pdf_path, kind)]`` into one PDF at ``out_path``, in the order given.

    Returns each file's place in the joined PDF. Raises ``UnreadableFile`` - writing nothing - for
    the first file that is not a readable PDF with at least one page: a record silently missing one
    of the files it arrived in is worse than an upload refused (the old split upload skipped
    them)."""
    writer = PdfWriter()
    parts: list[Part] = []
    page = 1
    for position, (path, kind) in enumerate(sources, start=1):
        try:
            pages = list(PdfReader(path).pages)
            for each in pages:
                writer.add_page(each)
        except Exception:
            # pypdf raises many types for a damaged file, and a page can fail only when copied.
            raise UnreadableFile(position) from None
        if not pages:
            raise UnreadableFile(position)
        count = len(pages)
        parts.append(Part(kind, page, page + count - 1))
        page += count
    with open(out_path, "wb") as out:
        writer.write(out)
    return parts
