"""rows: record which cascade path decided each row's category

Revision ID: b9d3e5f81c47
Revises: e4c8a1f70b93
Create Date: 2026-08-28 17:20:00.000000

Issue #188, following #187. `classify()` already returns a `method` naming the path that decided a
row - `rules`, `llm+embedding`, `llm-disagree`, `embedding-only`, `llm-only`, `no-signal`, `empty` -
and every caller threw it away. It is the only thing that separates the two populations inside
category 100: a row both signals independently agreed was paperwork, and a row that is a
low-confidence guess. #187's filter cannot tell them apart, because `match_rules` answers "does a
rule call this paperwork TODAY" and not "how confident was the cascade WHEN IT RAN".

BOTH TABLES ON PURPOSE. `segment_rows` is the immutable model output and is what every measurement
on #144/#188 was taken against, precisely because a reviewer cannot have contaminated it;
`review_rows` is what the editor reads. `tasks.segment_document` already builds one `fields` dict
and feeds it to both, so this is one column each and no second code path.

NULLABLE WITH NO SERVER DEFAULT, and that is load-bearing. This cannot be backfilled: `method` is
produced by `classify()` at segment time, so recovering it for the 3,142 existing rows would mean
re-running the cascade over all of them, at model cost. NULL therefore means "unknown" and must stay
distinguishable from every real value - especially from `no-signal`, which means the opposite
("we checked, both stages failed"). A server default would erase that distinction on exactly the
rows where it matters. The review filter treats NULL as "show", so every existing row behaves as it
did before this shipped and sharpens only when its record is next segmented.

Width 32: the longest value `classify()` returns is `embedding-only` at 14, plus `timeout` written
by the categorization pool-timeout path, which never calls `classify()` at all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d3e5f81c47"
down_revision: Union[str, Sequence[str], None] = "e4c8a1f70b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("segment_rows", "review_rows")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("method", sa.String(length=32), nullable=True))


def downgrade() -> None:
    # Nothing else derives from this column, so dropping it only returns the review filter to the
    # #187 behaviour: it stops excluding the rows both signals agreed were paperwork, and shows
    # them again. No data is stranded - a re-segment reproduces every value.
    for table in _TABLES:
        op.drop_column(table, "method")
