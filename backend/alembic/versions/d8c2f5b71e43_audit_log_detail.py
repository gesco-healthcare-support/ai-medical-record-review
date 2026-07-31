"""audit log detail

Revision ID: d8c2f5b71e43
Revises: c8b1d4e70f92
Create Date: 2026-07-31 13:20:00.000000

A reviewer can now change one summary's category, and that change has to be traceable to the id it
came FROM as well as the one it went to. `action` is String(32), so encoding both ids into it (e.g.
"summary.category:1->3") would overload a column nothing parses and break any later grouping by
action. Additive and nullable - safe on a populated table and reverses cleanly. Mirrors
a4f2c9e81b53, which added summaries.verified_title the same way.

Note for whoever needs it next: nothing READS audit_log today (app/services/audit.py only writes
it). This column makes the record correct and is the prerequisite for any forensics UI, but it
surfaces nowhere yet.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8c2f5b71e43"
down_revision: Union[str, Sequence[str], None] = "c8b1d4e70f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("detail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "detail")
