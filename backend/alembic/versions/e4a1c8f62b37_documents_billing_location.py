"""documents: the billing sheet's Location

Revision ID: e4a1c8f62b37
Revises: c7f3a9e2d5b1
Create Date: 2026-11-20 16:00:00.000000

The office's first use of the billing sheet (2026-11-20) asked for its Location box to be typed
like the other fields (`app/services/billing_sheet.py`), at upload (`POST /api/documents/combine`)
and on the billing sheet route:

- `location`: where the evaluation takes place, as typed.

Nullable, NO backfill: a record uploaded before this, or as one PDF, has none, and its sheet leaves
the box blank as before. Downgrade drops it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4a1c8f62b37"
down_revision: Union[str, Sequence[str], None] = "c7f3a9e2d5b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("location", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "location")
