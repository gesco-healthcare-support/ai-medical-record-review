"""job cancel requested

Revision ID: e7b4c1a92d58
Revises: d8c2f5b71e43
Create Date: 2026-07-31 15:20:00.000000

The cooperative half of the stop button: the API sets this, and the worker's report() raises
JobCancelled when it sees it. A Redis key carries the same signal to the retry backoff (where a
DB session is not available), but the column is the durable record - it survives a Redis flush and
is what an operator can read to see that a stop was actually requested.

`server_default=false` because `jobs` is populated (the LAN box has runs going back to July), and a
NOT NULL column without one would fail the ALTER. Mirrors a4f2c9e81b53 / d8c2f5b71e43 in shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7b4c1a92d58"
down_revision: Union[str, Sequence[str], None] = "d8c2f5b71e43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("jobs", "cancel_requested")
