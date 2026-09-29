"""jobs: record who started the job

Revision ID: f5c8d2a19e47
Revises: e4b7a2c91d05
Create Date: 2026-09-29 20:00:00.000000

An admin can now open and fix a record another reviewer owns. Every route already audits the ACTING
user, but the one audit row the WORKER writes - a re-segment replacing a record's rows - had no
requester to name and used the document's owner, on the stated assumption that only the owner could
start it. That stopped being true, so the job carries who asked.

Additive and nullable: existing rows read NULL, which the worker treats as "the owner" - what every
one of those jobs actually was. No backfill, because an inferred value would later be
indistinguishable from a recorded one. A plain integer rather than a foreign key, like the rest of
the job's provenance columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5c8d2a19e47"
down_revision: Union[str, Sequence[str], None] = "e4b7a2c91d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("requested_by", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "requested_by")
