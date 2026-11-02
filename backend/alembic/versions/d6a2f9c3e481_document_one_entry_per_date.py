"""documents: the reviewer chose one entry per date for this record

Revision ID: d6a2f9c3e481
Revises: c8e2f5a9d14b
Create Date: 2026-10-30 12:00:00.000000

The senior reviewer asked (2026-10-30) that a Kaiser or hospital record keep everything from one date
as one entry, diagnostics apart. That cannot be the rule for every record - on ordinary records
reviewers keep same-date neighbours apart - so the reviewer chooses it per record on the workbench.
The action merges the neighbouring rows at once; this column remembers the choice, so the letter
also folds the same-date entries that are not neighbours.

Additive and server-defaulted, so it is safe on a populated table: every existing document reads
false, which is what it is - nobody has chosen it.

Downgrade drops the column. The merged rows stay merged; the letter stops folding the same-date
entries that are not neighbours.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6a2f9c3e481"
down_revision: Union[str, Sequence[str], None] = "c8e2f5a9d14b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "one_entry_per_date", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "one_entry_per_date")
