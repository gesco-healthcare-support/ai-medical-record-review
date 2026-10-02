"""replaced_review_rows: keep the reviewer rows a re-segment replaces

Revision ID: a3d6f0b81e57
Revises: f5c8d2a19e47
Create Date: 2026-10-02 10:00:00.000000

`segment_document` deletes a document's `review_rows` and writes fresh model output in their place.
#217 made that leave an audit COUNT of the corrections it destroyed; the corrections themselves were
still lost. They are the only segmentation and categorization ground truth this project has and the
training data for the self-hosted model, so a re-run now copies the old rows here first, tagged with
the segment job that replaced them.

Schema only, a new table: nothing existing changes and nothing is backfilled - rows replaced before
this revision are gone and cannot be reconstructed. `job_id` is a plain integer, not a foreign key,
like the rest of a job's provenance. `document_id` is a NO ACTION foreign key like every other table
that references `documents`; `Document.replaced_review_rows` carries the ORM delete-orphan cascade.

Downgrade drops the table and with it every kept row; that data cannot be restored.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d6f0b81e57"
down_revision: Union[str, Sequence[str], None] = "f5c8d2a19e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "replaced_review_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("replaced_at", sa.DateTime(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("date", sa.String(length=16), nullable=False),
        sa.Column("injury_date", sa.Text(), nullable=False),
        sa.Column("flag", sa.String(length=4), nullable=False),
        sa.Column("include", sa.Boolean(), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_replaced_review_rows_document_id", "replaced_review_rows", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_replaced_review_rows_document_id", table_name="replaced_review_rows")
    op.drop_table("replaced_review_rows")
