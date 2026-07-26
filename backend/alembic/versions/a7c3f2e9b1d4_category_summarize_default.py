"""category summarize_default flag

Revision ID: a7c3f2e9b1d4
Revises: f1b8d3c60a29
Create Date: 2026-07-26 12:00:00.000000

Adds `categories.summarize_default` (whether a category is checked for summarization by default).
Additive + reversible. Data backfill: General (100) and Depositions (9) seed OFF, and existing
review_rows in those categories are set include=false so the default applies retroactively (the
data updates are one-way; downgrade only drops the column).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3f2e9b1d4"
down_revision: Union[str, Sequence[str], None] = "f1b8d3c60a29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column(
            "summarize_default", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    # General + Depositions are rarely summarized -> default them OFF, and unselect existing rows.
    op.execute("UPDATE categories SET summarize_default = false WHERE id IN ('9', '100')")
    op.execute("UPDATE review_rows SET include = false WHERE category IN ('9', '100')")


def downgrade() -> None:
    op.drop_column("categories", "summarize_default")
