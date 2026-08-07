"""per-call-type models and prompt provenance

Revision ID: b6d19f4c30a7
Revises: e7b4c1a92d58
Create Date: 2026-08-06 12:40:00.000000

Two related gaps, one migration, because they land together and both stamp the same rows.

1. PER-CALL-TYPE MODELS. Summarize makes three model calls per row (body, title, audit) and they now
   run on different models - body on 2.5-pro, title and audit on 2.5-flash. `jobs.model` is a single
   column and cannot describe three, so `jobs.title_model` / `jobs.audit_model` record the other two.
   Resolved once at job creation, which is what stops a job resumed after a config change from
   switching models mid-document.

2. PROMPT PROVENANCE. `jobs.prompt_version` is a hand-maintained constant that went unbumped through
   roughly a dozen prompt PRs, so every job from all of them is stamped the same. The fingerprints
   hash the prompt text AS RESOLVED (DB-first, code fallback), so they move on their own - including
   when a prompt is edited in the admin console, which a hash over prompts.py alone would miss.
   `summaries` gets its own copies because a job spans many categories, so one job-level hash cannot
   describe any individual summary.

ALL NULLABLE, no server_default, and NO BACKFILL - deliberately. Both tables are populated (runs go
back to July). Rows written before this migration have no truthful value here: for those jobs the
single `model` column IS what all three calls used, so read `title_model or model`. Inventing a value
would later be indistinguishable from a recorded one, which is the failure this whole change exists
to fix. Anyone analysing older rows must date them against deploy history and know that is what they
are doing.

Shape mirrors a4f2c9e81b53 / e7b4c1a92d58 (plain add_column / drop_column, no data migration).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d19f4c30a7"
down_revision: Union[str, Sequence[str], None] = "e7b4c1a92d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, length). String lengths match the model definitions: 64 is comfortable for a model
# id like "gemini-2.5-flash-lite"; 16 holds a 12-character fingerprint with room to widen it.
_COLUMNS = (
    ("jobs", "title_model", 64),
    ("jobs", "audit_model", 64),
    ("jobs", "prompt_fingerprint", 16),
    ("summaries", "model", 64),
    ("summaries", "title_model", 64),
    ("summaries", "audit_model", 64),
    ("summaries", "prompt_fingerprint", 16),
    ("summaries", "audit_fingerprint", 16),
)


def upgrade() -> None:
    for table, column, length in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.String(length=length), nullable=True))


def downgrade() -> None:
    for table, column, _length in reversed(_COLUMNS):
        op.drop_column(table, column)
