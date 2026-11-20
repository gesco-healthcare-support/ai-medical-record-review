"""documents: what the office's billing sheet needs

Revision ID: c7f3a9e2d5b1
Revises: b5e1d9c4a7f2
Create Date: 2026-11-19 18:00:00.000000

The office's answers of 2026-11-19 on the billing sheet (the "super bill"), which is made for a
record put together from the files it arrived in (`app/services/billing_sheet.py`). The uploader
types these at upload (`POST /api/documents/combine`); the billing sheet route edits them:

- `visit_type`: AME, QME, PQME, IME or SUPP (a supplemental report, which has no appointment).
- `review_source`: who reviews the records - `edata`, `in_house` or `leonardo`.
- `interpreter`: whether the evaluation needs an interpreter.
- `claim_numbers`: as typed; a case can have several.
- `previous_visit`: the patient's last office visit before this one, when there was one.
- `declared_pages`: the page count the sender's declaration states.
- `discrepancy_reason`: why that count and the office's own differ.
- `office_count`, `depo_pages`: a count typed over the one worked out from the record; NULL means
  "use the worked-out count".

All nullable, NO backfill: a record uploaded as one PDF has none of them. Downgrade drops them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7f3a9e2d5b1"
down_revision: Union[str, Sequence[str], None] = "b5e1d9c4a7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("visit_type", sa.String(length=8)),
    ("review_source", sa.String(length=16)),
    ("interpreter", sa.Boolean()),
    ("claim_numbers", sa.String(length=255)),
    ("previous_visit", sa.Date()),
    ("declared_pages", sa.Integer()),
    ("discrepancy_reason", sa.String(length=255)),
    ("office_count", sa.Integer()),
    ("depo_pages", sa.Integer()),
)


def upgrade() -> None:
    for name, kind in _COLUMNS:
        op.add_column("documents", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("documents", name)
