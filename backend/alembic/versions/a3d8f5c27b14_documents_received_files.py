"""documents: a record put together from the files it arrived in

Revision ID: a3d8f5c27b14
Revises: c4f8a2d61e97
Create Date: 2026-11-18 18:00:00.000000

The office used to join the files a record arrives in (internal email, advocacy letter, declaration,
proof of service, medical records) in Adobe Acrobat before uploading one PDF. The app now takes the
files and joins them itself (`POST /api/documents/combine`, `app/services/received_files.py`).

- `received_on`: the day the records were received, as the uploader typed it.
- `received_parts`: each file's kind and its pages in the joined PDF, in order.

Both nullable, NO backfill: a record uploaded as one PDF has neither. Downgrade drops them; the
joined PDF itself is unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d8f5c27b14"
down_revision: Union[str, Sequence[str], None] = "c4f8a2d61e97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("received_on", sa.Date(), nullable=True))
    op.add_column("documents", sa.Column("received_parts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "received_parts")
    op.drop_column("documents", "received_on")
