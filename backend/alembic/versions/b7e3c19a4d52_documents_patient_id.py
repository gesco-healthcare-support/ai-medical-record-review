"""documents: the patient's ID, filled from every stored record's file name

Revision ID: b7e3c19a4d52
Revises: f2c7a4e81b39
Create Date: 2026-11-12 12:00:00.000000

The office names a record file after the patient, with the patient's ID (the office's account number
for the patient) in it. The column finds a patient's other records, in any reviewer's account, so a
new record for a patient reviewed before can say so (and, next, mark what was already summarized).

- `patient_id`: nullable, indexed. NULL means nobody has given it.
- This revision fills it once for the records already stored, from `original_filename`, with the
  same rule as `app/services/patient_ids.py` (frozen below; a test holds the two together). A name
  with no ID, or with two candidates, stays NULL. Measured read-only on the live box (2026-11-12):
  92% of its records get one.

Downgrade drops the column; upgrading again fills it again from the file names, but an ID a
reviewer typed by hand is lost.
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3c19a4d52"
down_revision: Union[str, Sequence[str], None] = "f2c7a4e81b39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen here, not imported from the app: app/services/patient_ids.py `from_filename` as of this
# revision. `tests/test_patient_ids.py` checks the two give the same answer.
_TOKEN = re.compile(r"[A-Za-z0-9]+")
_ID_SHAPE = re.compile(r"^(?:[A-Za-z]{1,2}\d{4,5}|\d{6,7})$")
_MMDDYY = re.compile(r"^(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d\d$")


def patient_id_from_filename(name: str | None) -> str | None:
    """The one patient ID in a file name, or None when it has none or more than one."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", (name or "").strip())
    found: list[str] = []
    for token in _TOKEN.findall(stem):
        if not _ID_SHAPE.match(token):
            continue
        if token.isdigit() and len(token) == 6 and _MMDDYY.match(token):
            continue
        token = token.upper()
        if token not in found:
            found.append(token)
    return found[0] if len(found) == 1 else None


def fill_patient_ids(bind) -> int:
    """Every document without an ID gets the one its file name carries. Returns how many did. A
    function of its own so a test can run it against synthetic rows."""
    rows = bind.execute(
        sa.text("SELECT id, original_filename FROM documents WHERE patient_id IS NULL")
    ).all()
    filled = 0
    for doc_id, name in rows:
        pid = patient_id_from_filename(name)
        if pid:
            bind.execute(
                sa.text("UPDATE documents SET patient_id = :pid WHERE id = :id"),
                {"pid": pid, "id": doc_id},
            )
            filled += 1
    return filled


def upgrade() -> None:
    op.add_column("documents", sa.Column("patient_id", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_documents_patient_id"), "documents", ["patient_id"], unique=False)
    filled = fill_patient_ids(op.get_bind())
    # A count only: the file names and the IDs are patient data.
    print(f"documents.patient_id: filled from the file name on {filled} record(s)")


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_patient_id"), table_name="documents")
    op.drop_column("documents", "patient_id")
