"""documents: the team queue

Revision ID: f6b2d8e41a93
Revises: e4a1c8f62b37
Create Date: 2026-11-25 12:00:00.000000

The office's answer (2026-11-25): a record put together from the files it arrived in belongs to
nobody until someone starts working on it. Every reviewer sees it in the team queue, listed by
when it is due; the reviewer who starts Identify on it takes it into their own records.

- `team_queue_since`: when the record went into the team queue; NULL once someone took it, and
  for every record that never was in it (`POST /api/documents/combine` puts a record there unless
  an admin uploads it into a named reviewer's account).

Nullable, NO backfill: every record already stored has an owner who is working on it. Downgrade
drops it, and the records waiting in the queue then stay with whoever uploaded them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6b2d8e41a93"
down_revision: Union[str, Sequence[str], None] = "e4a1c8f62b37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("team_queue_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "team_queue_since")
