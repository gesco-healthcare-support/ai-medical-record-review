"""summary verify fields

Revision ID: e3a9c7b21d84
Revises: c2d5e8f1a3b7
Create Date: 2026-07-24 23:30:00.000000

Summary faithfulness verify pass (problem #3): add `verified`, `verified_text`, and
`verify_issues` to the summaries table. All additive (nullable / server-defaulted false), so the
migration is safe on a populated table and reverses cleanly.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e3a9c7b21d84"
down_revision: Union[str, Sequence[str], None] = "c2d5e8f1a3b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "summaries",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("summaries", sa.Column("verified_text", sa.Text(), nullable=True))
    op.add_column("summaries", sa.Column("verify_issues", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "verify_issues")
    op.drop_column("summaries", "verified_text")
    op.drop_column("summaries", "verified")
