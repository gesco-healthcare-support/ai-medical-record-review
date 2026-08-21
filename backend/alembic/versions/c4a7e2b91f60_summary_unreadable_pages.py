"""summary unreadable pages

Revision ID: c4a7e2b91f60
Revises: b3f7c02e91a4
Create Date: 2026-08-21 16:20:00.000000

C9: a sub-document whose pages could not be READ now gets a delivered Summary carrying a fixed
notice instead of vanishing from the report. `unreadable` marks those rows.

A new column rather than a `summaries.model` sentinel, deliberately. That column's NULL already
means "written before 2026-08-06, unattributable, deliberately not backfilled", so it cannot also
mean "no model wrote this", and a sentinel string would overload a column documented as naming the
model that wrote the body - which the pro-vs-flash quality work groups by.

True whenever ANY of the row's pages failed extraction, so "which delivered documents lost pages?"
is one query. `model IS NULL` alongside it distinguishes the notice-only rows (nothing could be
summarized) from a row summarized off its readable pages that carries an appended notice.

Additive and server-defaulted false, so it is safe on a populated table (709+ summaries on the LAN
box) and reverses cleanly. Mirrors e3a9c7b21d84, which added the verify columns the same way.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a7e2b91f60"
down_revision: Union[str, Sequence[str], None] = "b3f7c02e91a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "summaries",
        sa.Column("unreadable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    # Notice rows survive this as ordinary summaries carrying the notice TEXT. Identify them by that
    # text before dropping the column if a rollback ever has to tell them apart.
    op.drop_column("summaries", "unreadable")
