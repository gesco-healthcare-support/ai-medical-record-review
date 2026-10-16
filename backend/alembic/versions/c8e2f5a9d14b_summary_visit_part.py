"""summaries: one summary per visit when a row's pages hold several dated visits

Revision ID: c8e2f5a9d14b
Revises: b7e4c1a9d203
Create Date: 2026-10-16 12:00:00.000000

A page can hold several separate visits with the same heading and their own dates - a sheet of daily
therapy or acupuncture notes - and the reviewers asked for each to be its own entry in the report
(2026-10-15). Identification still makes the page ONE row; summarization now writes one summary per
visit for it, all carrying the row's page range and category. `part` says which visit of its row a
summary is: 0 for the first, and for every row that holds a single visit.

Additive and server-defaulted, so it is safe on a populated table: every existing summary reads 0,
which is exactly what it is - the only summary of its row.

WHY A COLUMN. Summaries are matched to their row by `(row_start, row_end, row_category)`, and the
summarize worker keeps ONE summary per row on a resume and deletes the rest as stale duplicates.
Several visits share that identity, so without a part number a resume could not tell the second
visit from a duplicate and would delete it.

Downgrade drops the column. A row's extra visits then look like duplicates to the old worker, which
deletes all but one of them on the next summarize run.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2f5a9d14b"
down_revision: Union[str, Sequence[str], None] = "b7e4c1a9d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "summaries",
        sa.Column("part", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("summaries", "part")
