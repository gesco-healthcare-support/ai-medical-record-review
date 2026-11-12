"""summaries: when an entry was already reviewed in the patient's earlier record

Revision ID: c4f8a2d61e97
Revises: b7e3c19a4d52
Create Date: 2026-11-12 18:00:00.000000

The record of a patient finished last highlights each entry an earlier-finished record of the same
patient already summarized (`app/services/previous_reviews.py`), on the Summaries tab and in the
Word letter and the linked PDF, so an editor can see it was viewed before.

- `reviewed_before`: the earlier record's upload time; NULL means not reviewed before, or not checked.
  Nullable, NO backfill: records summarized before this revision are left as they are - the office
  asked for the highlights on records done from now on (a record run again gets them).

Downgrade drops the column; the marks come back the next time each record is summarized.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f8a2d61e97"
down_revision: Union[str, Sequence[str], None] = "b7e3c19a4d52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("summaries", sa.Column("reviewed_before", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "reviewed_before")
