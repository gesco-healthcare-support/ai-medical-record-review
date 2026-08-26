"""summary updated at

Revision ID: a1e6f4d20c93
Revises: d5b8c31a740e
Create Date: 2026-08-26 17:40:00.000000

Reviewer correction effort is measurable in WHAT changed but not in WHEN, because no edit in this
schema carries a timestamp. Measured on the LAN box 2026-08-26: reviewers alter 10.5% of the
model's boundaries and 92% of those alterations are merges - a real signal that can never become a
trend, or be attributed to a prompt revision or a model change, without a clock on it.

`summaries.updated_at` is the cheap half of the fix. Summary edits land in `edited_*` IN PLACE, so
the row survives its own edit and can carry a timestamp. Review rows cannot: `_store_rows` deletes
and recreates the whole set on every save, so a column there would only ever record the save that
created the row. Boundary work is instrumented as an `audit_log` event instead, in the same change.

Read it as a SECONDARY instrument. The verify pass also writes a Summary, so a fresh timestamp does
not by itself mean a human touched the row; the `summary.edit` audit event is what identifies
reviewer work. This column answers "when was this row last written at all" without scanning the
trail.

NULLABLE with NO server default and NO backfill, which is the one decision here worth stating.
Every other option invents data: a server default would stamp 2,608 existing rows with the
migration's own timestamp, and backfilling from job timestamps would produce values later
indistinguishable from recorded ones. NULL means "not written since 2026-08-26" and stays honest.
The same reasoning is already recorded on `summaries.model`, `jobs.title_model` and `jobs.build_sha`.

Additive and nullable, so it is safe on a populated table and reverses cleanly.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1e6f4d20c93"
down_revision: Union[str, Sequence[str], None] = "d5b8c31a740e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("summaries", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "updated_at")
