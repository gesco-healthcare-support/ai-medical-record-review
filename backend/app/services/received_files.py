"""A record put together from the files it arrived in.

Records reach the office as several files - the internal email they came with, the advocacy letter,
the declaration, the proof of service and the medical records themselves - and the office used to
join them in Adobe Acrobat before uploading one PDF. ``POST /api/documents/combine`` takes the
files instead and joins them here, in the order the uploader confirmed. The joined file is the raw
record: it is stored as the record's PDF, read and identified like any other upload, and can be
downloaded as it is for the office's backup.

The order the files are offered in (``KINDS``) is the one the office's finished records show,
measured read-only on the live box (2026-11-18, 703 records): the office's own cover sheet on page 1
(90%), then the internal email (pages 2-3), the advocacy letter, the declaration, the proof of
service, then the medical records with their depositions. The uploader can still move any file;
the server joins the files in the order it is sent.

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
    "cover",
    "email",
    "advocacy_letter",
    "declaration",
    "proof_of_service",
    "records",
)
KIND_LABELS: dict[str, str] = {
    "cover": "Cover sheet",
    "email": "Internal email",
    "advocacy_letter": "Advocacy letter",
    "declaration": "Declaration",
    "proof_of_service": "Proof of service",
    "records": "Medical records",
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
