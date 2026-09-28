"""PROBE for the Squawk step: dropping a column; must fail (ban-drop-column). Never merged.

Revision ID: f0a0a0a0a0c1
Revises: e4b7a2c91d05
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a0a0a0a0c1"
down_revision: Union[str, Sequence[str], None] = "e4b7a2c91d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop a column: data loss, which the Squawk step must refuse."""
    op.drop_column("categories", "summarize_default")


def downgrade() -> None:
    """Put it back."""
    op.add_column(
        "categories",
        sa.Column("summarize_default", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
