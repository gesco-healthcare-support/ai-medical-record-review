"""documents: the patient's ID, filled from every stored record's file name

Revision ID: b7e3c19a4d52
Revises: f2c7a4e81b39
Create Date: 2026-11-12 12:00:00.000000

The office names a record file after the patient, with the patient's ID (the office's account number
for the patient) in it. The column finds a patient's other records, in any reviewer's account, so a
new record for a patient reviewed before can say so (and, next, mark what was already summarized).

- `patient_id`: nullable, indexed. NULL means nobody has given it.
- This revision fills it once for the records already stored, from `original_filename`, with the
  same rule as `app/services/patient_ids.py` (frozen below as one statement; a test runs it on the
  service's own cases). A name with no ID, or with two, stays NULL. Measured read-only on the live
  box (2026-11-12): 92% of its records get one.

Downgrade drops the column; upgrading again fills it again from the file names, but an ID a
reviewer typed by hand is lost.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "b7e3c19a4d52"
down_revision: Union[str, Sequence[str], None] = "f2c7a4e81b39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# app/services/patient_ids.py `from_filename` as of this revision, frozen here (not imported from the
# app) and written as ONE statement, so it runs the same against a database and in
# `alembic upgrade --sql` (the Squawk lint in CI): each run of letters and digits in the file name
# without its extension is kept when it has one of the four ID shapes and is not six digits that read
# as a date; a name with exactly one distinct such run, upper-cased, gets it. Only documents with no
# ID yet are touched.
FILL_PATIENT_IDS = r"""
UPDATE documents SET patient_id = found.pid
FROM (
    SELECT id, min(token) AS pid
    FROM (
        SELECT DISTINCT d.id, upper(m.part[1]) AS token
        FROM documents AS d,
            regexp_matches(
                regexp_replace(btrim(d.original_filename), '\.[A-Za-z0-9]{2,4}$', ''),
                '[A-Za-z0-9]+',
                'g'
            ) AS m(part)
        WHERE d.patient_id IS NULL
            AND m.part[1] ~ '^([A-Za-z]{1,2}[0-9]{4,5}|[0-9]{6,7})$'
            AND m.part[1] !~ '^(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])[0-9]{2}$'
    ) AS tokens
    GROUP BY id
    HAVING count(*) = 1
) AS found
WHERE documents.id = found.id
"""


def fill_patient_ids(bind) -> None:
    """Every document without an ID gets the one its file name carries. A function of its own so a
    test can run it against synthetic rows. Prints nothing: the IDs are patient data."""
    bind.execute(sa.text(FILL_PATIENT_IDS))


def upgrade() -> None:
    # IF NOT EXISTS on both statements: the index is built outside the transaction (below), so a run
    # that stops after the column must be able to run again.
    op.add_column(
        "documents",
        sa.Column("patient_id", sa.String(length=32), nullable=True),
        if_not_exists=True,
    )
    if context.is_offline_mode():
        op.execute(FILL_PATIENT_IDS)
    else:
        fill_patient_ids(op.get_bind())
    # CONCURRENTLY, so building it never blocks a reviewer's write to `documents`; that cannot run in
    # a transaction, hence the autocommit block (it commits the column and the fill first).
    with op.get_context().autocommit_block():
        op.create_index(
            op.f("ix_documents_patient_id"),
            "documents",
            ["patient_id"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            op.f("ix_documents_patient_id"),
            table_name="documents",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("documents", "patient_id")
