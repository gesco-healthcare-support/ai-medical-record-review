"""job build sha

Revision ID: c5d81f6a3b70
Revises: f0f4d21dbb53
Create Date: 2026-08-11 23:40:00.000000

Completes the 2026-07-31 provenance plan. The fingerprint half shipped in b6d19f4c30a7 and
covers prompt TEXT; this covers the CODE, which nothing else records: house_style and the
per-row context blocks summarize_engine appends after the fingerprint is computed.

Nullable with no server default and no backfill, both deliberate. Jobs created before this
migration have no truthful value, and a default would assert one - the same reasoning that
kept prompt_version's historical rows honest rather than rewriting them.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c5d81f6a3b70"
down_revision: Union[str, Sequence[str], None] = "f0f4d21dbb53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("jobs", sa.Column("build_sha", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "build_sha")
