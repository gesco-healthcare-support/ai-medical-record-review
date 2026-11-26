"""documents: several deposition page counts on the billing sheet

Revision ID: a9c4e7b2d815
Revises: f6b2d8e41a93
Create Date: 2026-11-25 16:00:00.000000

The office's answer (2026-11-25): the deposition pages typed on the billing sheet may be several
numbers - one per deposition - and the sheet prints them as typed, separated by commas.

- `depo_page_counts`: the deposition pages typed over the worked-out count, as the sheet prints them
  (`120, 85`); NULL uses the worked-out count.

`documents.depo_pages` (one whole number) held that before. Every count already typed there is copied
across as text, so no sheet changes; the old column is no longer written or read. Downgrade drops the
new column - the copies' source is still in `depo_pages`, but a list typed since would be lost.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e7b2d815"
down_revision: Union[str, Sequence[str], None] = "f6b2d8e41a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# One statement, no rows read here, so the migration renders offline (`alembic upgrade --sql`).
# Named so a test runs exactly this (tests/test_billing_sheet.py).
COPY_TYPED_COUNTS = (
    "UPDATE documents SET depo_page_counts = CAST(depo_pages AS VARCHAR) "
    "WHERE depo_pages IS NOT NULL"
)


def upgrade() -> None:
    op.add_column("documents", sa.Column("depo_page_counts", sa.String(length=255), nullable=True))
    op.execute(COPY_TYPED_COUNTS)


def downgrade() -> None:
    op.drop_column("documents", "depo_page_counts")
