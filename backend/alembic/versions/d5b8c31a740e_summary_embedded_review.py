"""summaries: flag a row whose body carries an embedded-review tag

Revision ID: d5b8c31a740e
Revises: c4a7e2b91f60
Create Date: 2026-08-26 01:20:00.000000

Additive and server-defaulted, so it is safe on a populated table: every existing summary reads
false, which is correct - none of them carries the tag.

WHY A COLUMN AND NOT JUST THE TEXT. The tag itself lives in `summaries.text`, composed in code. This
flag exists so "which delivered evaluations contain an embedded records review?" is one query rather
than a LIKE over summary bodies, which would match any summary that happened to discuss one. Same
reasoning as `summaries.unreadable` from c4a7e2b91f60, and deliberately the same shape.

Read alongside `model`, exactly as `unreadable` is: an embedded-review tag is only ever APPENDED to a
row that was really summarized, so `embedded_review` with `model IS NULL` should not occur. If it
ever does, something wrote the tag onto a notice-only row and that is a defect rather than a state.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5b8c31a740e"
down_revision: Union[str, Sequence[str], None] = "c4a7e2b91f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "summaries",
        sa.Column("embedded_review", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    # Tagged rows survive this as ordinary summaries still carrying the tag SENTENCE in their body.
    # Identify them by that text before dropping the column if a rollback ever has to tell them apart.
    op.drop_column("summaries", "embedded_review")
