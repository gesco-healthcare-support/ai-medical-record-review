"""PROBE for the Squawk step: a benign nullable column; must pass. Never merged.

Revision ID: f0a0a0a0a0b1
Revises: e4b7a2c91d05
Create Date: 2026-09-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0a0a0a0a0b1"
down_revision: Union[str, Sequence[str], None] = "e4b7a2c91d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a nullable column, which Squawk (as tuned) accepts."""
    op.add_column("categories", sa.Column("probe_squawk_ok", sa.String(length=10), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    op.drop_column("categories", "probe_squawk_ok")
