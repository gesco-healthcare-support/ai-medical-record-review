"""review row dupe fields

Revision ID: f1b8d3c60a29
Revises: e3a9c7b21d84
Create Date: 2026-07-24 23:55:00.000000

Duplicate clustering (problem #1): add `source_text`, `dupe_group`, `dupe_primary`, and
`dupe_dismissed` to review_rows. All additive (nullable / server-defaulted false), so the
migration is safe on a populated table and reverses cleanly. `dupe_group` is indexed for the
Duplicates-view grouping query.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f1b8d3c60a29"
down_revision: Union[str, Sequence[str], None] = "e3a9c7b21d84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("review_rows", sa.Column("source_text", sa.Text(), nullable=True))
    op.add_column("review_rows", sa.Column("dupe_group", sa.Integer(), nullable=True))
    op.add_column(
        "review_rows",
        sa.Column("dupe_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "review_rows",
        sa.Column("dupe_dismissed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_review_rows_dupe_group", "review_rows", ["dupe_group"])


def downgrade() -> None:
    op.drop_index("ix_review_rows_dupe_group", table_name="review_rows")
    op.drop_column("review_rows", "dupe_dismissed")
    op.drop_column("review_rows", "dupe_primary")
    op.drop_column("review_rows", "dupe_group")
    op.drop_column("review_rows", "source_text")
