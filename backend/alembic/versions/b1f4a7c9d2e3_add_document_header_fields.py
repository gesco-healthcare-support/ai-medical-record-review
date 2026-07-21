"""add document header fields

Revision ID: b1f4a7c9d2e3
Revises: 009991f2eda1
Create Date: 2026-07-21 00:00:00.000000

Adds the reviewer report-header columns to documents (backlog item 1): patient first/last name,
DOB, and law firm. All nullable + additive; auto-populated on identify and editable in Review.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b1f4a7c9d2e3"
down_revision: Union[str, Sequence[str], None] = "009991f2eda1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("patient_first_name", sa.String(length=255), nullable=True)
    )
    op.add_column("documents", sa.Column("patient_last_name", sa.String(length=255), nullable=True))
    op.add_column("documents", sa.Column("patient_dob", sa.String(length=32), nullable=True))
    op.add_column("documents", sa.Column("law_firm", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "law_firm")
    op.drop_column("documents", "patient_dob")
    op.drop_column("documents", "patient_last_name")
    op.drop_column("documents", "patient_first_name")
