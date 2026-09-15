"""report header: the covering letter, the doctor, and the real page count

Five nullable columns on ``documents``. Nothing is backfilled and nothing is required: every
consumer falls back to the behaviour that shipped before, so an existing record renders exactly as
it did.

``pages_received`` is the one worth explaining. It is NOT ``page_count``. The reviewers attach
their own pages to the PDF before it reaches us, so the file is reliably longer than what actually
arrived - measured against four human deliverables the gap is 2 or 3 pages every time (311/309,
293/290, 244/241, 229/226). Their cover sheet carries the true figure, and this column is where a
reviewer puts it. NULL means nobody has said, and the export keeps using ``page_count``.

Revision ID: c2f1a7d94e63
Revises: b3e9f0c47a15
Create Date: 2026-09-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2f1a7d94e63"
down_revision: str | Sequence[str] | None = "b3e9f0c47a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("attorney_name", sa.String(255)),
    ("doctor", sa.String(255)),
    ("letter_type", sa.String(32)),
    ("letter_date", sa.String(32)),
    ("pages_received", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("documents", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    # Reverse order so the downgrade reads as the mirror of the upgrade. Dropping these loses
    # reviewer-entered values; there is no way to preserve them, and that is the honest cost of
    # reversing a column addition rather than something this can work around.
    for name, _ in reversed(_COLUMNS):
        op.drop_column("documents", name)
