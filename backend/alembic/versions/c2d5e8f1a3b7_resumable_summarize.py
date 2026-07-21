"""resumable summarize

Revision ID: c2d5e8f1a3b7
Revises: b1f4a7c9d2e3
Create Date: 2026-07-21 13:30:00.000000

Item 7 (resumable summarize): add the job columns the pause/resume state machine needs and widen
the one-active-job partial-unique index to treat `paused` as in-flight (a paused summarize run
awaiting its delayed resume must still block a second job for the same document). The index
predicate change is the only structural edit; the 3 new columns are nullable/defaulted, so the
migration is safe on a populated table and reverses cleanly.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c2d5e8f1a3b7"
down_revision: Union[str, Sequence[str], None] = "b1f4a7c9d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("rq_job_id", sa.String(length=64), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("jobs", sa.Column("attention", sa.JSON(), nullable=True))
    # Rebuild the partial-unique index so `paused` is also treated as an active (blocking) state.
    op.drop_index("uq_one_active_job_per_document", table_name="jobs")
    op.create_index(
        "uq_one_active_job_per_document",
        "jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running', 'paused')"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_active_job_per_document", table_name="jobs")
    op.create_index(
        "uq_one_active_job_per_document",
        "jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.drop_column("jobs", "attention")
    op.drop_column("jobs", "attempts")
    op.drop_column("jobs", "rq_job_id")
