"""one active job per document index

Revision ID: 009991f2eda1
Revises: a0725d467a48
Create Date: 2026-07-15 14:38:01.685547

Enforces the one-active-job-per-document invariant across RQ worker processes (P4): a partial
unique index on jobs(document_id) covering only queued/running rows. A racing second enqueue
violates it -> IntegrityError -> the 409 (app/services/jobs.py). Finished jobs (done/error/
interrupted) are excluded, so a document can accumulate its full job history.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "009991f2eda1"
down_revision: Union[str, Sequence[str], None] = "a0725d467a48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_one_active_job_per_document",
        "jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_active_job_per_document", table_name="jobs")
