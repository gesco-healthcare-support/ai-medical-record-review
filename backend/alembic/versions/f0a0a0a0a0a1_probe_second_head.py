"""PROBE for #418: a second head, branching from the same parent as e4b7a2c91d05. Never merged.

Revision ID: f0a0a0a0a0a1
Revises: c2f1a7d94e63
Create Date: 2026-09-28
"""

from typing import Sequence, Union

revision: str = "f0a0a0a0a0a1"
down_revision: Union[str, Sequence[str], None] = "c2f1a7d94e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change: the probe only needs a second head."""


def downgrade() -> None:
    """Nothing to undo."""
