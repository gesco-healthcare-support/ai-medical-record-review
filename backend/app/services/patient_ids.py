"""The patient's ID, read from the upload's file name, and the other records that carry it.

The office names a record file after the patient: the name, the patient's account number (the
patient ID) and the date the file was made, in no fixed order - ``First Last-AB1234-11032026.pdf``
or ``801234 First Last.pdf``. Measured read-only on the live box (2026-11-12, 763 records): 702 file
names hold exactly one ID; every patient with two or more records carries one ID on all of them (79
of 79); a different patient almost never shares one. Where the letter's pages show it too, it is the
same number (196 of 200). It comes in four shapes: six digits starting 80, seven digits, one letter
and five digits, two letters and four digits.

A name with no ID, or with two candidates, gives None. The uploader is asked instead (the web app's
upload dialog asks before it sends the file); the ID is never guessed.

The file name is patient data: nothing here logs it, and nothing logs the ID.
"""

import re
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document

# Longest ID stored. The real ones are six or seven characters; a typed value longer than this is a
# paste of something else.
MAX_LENGTH = 32

# A date written with separators (``11-03-2026``, ``1.3.26``) is taken out of the name first, so its
# parts are never read as an ID.
_SEP_DATE = re.compile(r"(?<!\d)\d{1,2}[-._/ ]\d{1,2}[-._/ ](?:\d{4}|\d{2})(?!\d)")
_TOKEN = re.compile(r"[A-Za-z0-9]+")
# The four shapes the IDs take. A year, a page count or the eight-digit file date never matches.
_ID_SHAPE = re.compile(r"^(?:[A-Za-z]{1,2}\d{4,5}|\d{6,7})$")
_MMDDYY = re.compile(r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d\d$")


def _candidates(name: str) -> list[str]:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name.strip())
    found: list[str] = []
    for token in _TOKEN.findall(_SEP_DATE.sub(" ", stem)):
        if not _ID_SHAPE.match(token):
            continue
        # Six digits that read as a date (``110326``) are the file date, not an ID. An ID starting
        # 80 can never be one: there is no month 80.
        if token.isdigit() and len(token) == 6 and _MMDDYY.match(token):
            continue
        token = token.upper()
        if token not in found:
            found.append(token)
    return found


def from_filename(name: str | None) -> str | None:
    """The one patient ID in ``name``, or None when it has none or more than one."""
    found = _candidates(name or "")
    return found[0] if len(found) == 1 else None


def agreed(names: Iterable[str | None]) -> str | None:
    """The ID the names that carry one all agree on, or None (none of them has one, or they differ).

    For a record put together from several files: each piece is usually named after the patient."""
    ids = {pid for pid in (from_filename(n) for n in names) if pid}
    return ids.pop() if len(ids) == 1 else None


def clean(value: str | None) -> str | None:
    """A typed ID as it is stored: letters and digits only, upper-case; None for an empty box.

    Spaces and dashes are dropped so ``ab-1234`` and ``AB1234`` are one patient. Raises ValueError
    for a value longer than MAX_LENGTH, which the route turns into a 400."""
    text = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(text) > MAX_LENGTH:
        raise ValueError("patient ID too long")
    return text or None


def other_records(session: Session, document: Document) -> int:
    """How many OTHER records, in any reviewer's account, carry this record's patient ID."""
    if not document.patient_id:
        return 0
    return (
        session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.patient_id == document.patient_id, Document.id != document.id)
        )
        or 0
    )
