"""backend provenance

Revision ID: b3e9f0c47a15
Revises: d7c1a9e34b28
Create Date: 2026-09-11 00:00:00.000000

WHICH BACKEND answered, alongside WHICH MODEL did. A model name alone stopped being sufficient the
moment a second backend could serve one: "Qwen/Qwen3.6-35B-A3B-FP8" is not a fact about where a
record went, because that served name is what every pod of ours loads.

ONE COLUMN, NOT A TRIPLE, and this was very nearly the other way. The first draft mirrored the model
triple beside it (`model` / `title_model` / `audit_model`) on the reasoning that per-stage overrides
let one job span two backends. That reasoning is wrong here: body, title and audit all resolve from
the SAME `summarize` stage, so three columns would have held one identical value. The split-job
argument is about different STAGES - segment against classify - and those live on separate job rows.

The other two would also have been derivable rather than recorded: `title_backend` and
`audit_backend` are recoverable from this column plus the existing `title_model` / `audit_model`,
which already encode whether each of those calls ran at all. Three columns that always agree are
worse than one, because a reader reasonably infers they can differ and eventually writes a query
that assumes it.

NULLABLE, AND NOT BACKFILLED. Both tables are populated (the LAN box has runs going back to July),
and every existing row was in fact written by Gemini - but writing that in would be inference
presented as record, which both tables' own comments already refuse to do for the model columns.
NULL here means "written before this column existed", which is a different fact from "Gemini".

String(16) is deliberate: this holds a backend NAME (gemini, openai, vllm), never a model name.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e9f0c47a15"
down_revision: Union[str, Sequence[str], None] = "d7c1a9e34b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("jobs", "summaries")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("backend", sa.String(length=16), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "backend")
