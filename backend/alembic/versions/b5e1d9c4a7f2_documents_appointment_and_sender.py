"""documents: the appointment date and who sent the records

Revision ID: b5e1d9c4a7f2
Revises: a3d8f5c27b14
Create Date: 2026-11-19 10:00:00.000000

The office's answers of 2026-11-19 on putting a record together from the files it arrived in
(`POST /api/documents/combine`): the uploader types the date of the evaluation appointment and
picks who sent the records (defense, applicant, insurance or copy service). Neither is reliably on
the letter. Both feed the office's billing sheet.

- `appointment_date`: Date.
- `records_from`: one of `app/services/received_files.py` PARTIES.

Both nullable, NO backfill: a record uploaded as one PDF has neither. Downgrade drops them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e1d9c4a7f2"
down_revision: Union[str, Sequence[str], None] = "a3d8f5c27b14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("appointment_date", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("records_from", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "records_from")
    op.drop_column("documents", "appointment_date")
