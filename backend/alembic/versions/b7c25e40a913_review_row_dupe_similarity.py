"""store each duplicate cluster's similarity score on its rows

Revision ID: b7c25e40a913
Revises: f1a83b5c60d2
Create Date: 2026-07-28 12:05:00.000000

The clusterer already computes how alike a candidate cluster's members are, and nothing kept it. The
score separates real re-scans from a recurring form series (measured on real records: 1.000 for three
copies of one report, 0.219 for a 6-visit form series), so the Duplicates review can show WHY rows
clustered instead of asking the reviewer to guess.

Additive and nullable: existing rows keep NULL until the next dedup run rewrites their group.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c25e40a913"
down_revision: Union[str, Sequence[str], None] = "f1a83b5c60d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("review_rows", sa.Column("dupe_similarity", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_rows", "dupe_similarity")
