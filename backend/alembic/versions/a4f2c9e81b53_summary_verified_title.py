"""summary verified title

Revision ID: a4f2c9e81b53
Revises: b7c25e40a913
Create Date: 2026-07-29 12:40:00.000000

The faithfulness verify pass now audits the TITLE as well as the body, so a corrected title needs
somewhere to live that is not the immutable `title` column. Additive and nullable - safe on a
populated table (709 summaries on the LAN box) and reverses cleanly. Mirrors e3a9c7b21d84, which
added the body-side verify columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f2c9e81b53"
down_revision: Union[str, Sequence[str], None] = "b7c25e40a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("summaries", sa.Column("verified_title", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "verified_title")
