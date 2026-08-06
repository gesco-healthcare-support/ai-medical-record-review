"""summarize depositions by default

Revision ID: a9c4e13f70b2
Revises: e7b4c1a92d58
Create Date: 2026-08-06 14:10:00.000000

MIGRATION ORDERING. This forks from main's head, as PR #79's b6d19f4c30a7 and PR #77's f0f4d21dbb53 both
do. Three migrations on one parent means whichever merges SECOND and THIRD must have `down_revision`
re-pointed at its predecessor before merging - two alembic heads make `upgrade head` fail outright. It
is deliberately NOT pre-chained on #79: that would make this branch impossible to test on its own and
impossible to merge first. Chaining is a merge-time decision, and it is called out in every affected PR.

Category 9 (Depositions) was seeded `summarize_default = false`, so a reviewer had to remember a switch
before any deposition was summarized at all. Adrian turned it on (2026-08-06), alongside the rewritten
category-9 prompt - without this the prompt work reaches almost no delivered output.

WHY A MIGRATION AND NOT JUST THE CONSTANT. `seed_catalog()` returns early the moment any Category row
exists ("already seeded (or edited)"), so editing `_SUMMARIZE_OFF_BY_DEFAULT` affects only a brand-new
database. Every existing box - the server, each developer stack - keeps the old value. This is the same
trap as f1a83b5c60d2, which exists precisely because a prompt edit in code changed nothing on a box
that had already been seeded.

HONEST LIMITATION. `categories.summarize_default` is admin-editable, and nothing distinguishes "false
because it was seeded that way" from "false because somebody deliberately turned it off". This
migration cannot tell them apart and will overwrite either. For category 9 the risk is low - it was
seeded off and there is no record of anyone choosing that - but the next person applying this pattern
to a category someone HAS configured would silently discard their choice. Do not copy it blindly.

Guarded by id and by the current value, so re-running is a no-op and a box where category 9 is already
on is untouched. Depositions are the most expensive documents in the pipeline (the human summaries run
to roughly 45 dense paragraphs), so this deliberately lands after the model-tiering work that pays for
them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e13f70b2"
down_revision: Union[str, Sequence[str], None] = "e7b4c1a92d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE categories SET summarize_default = true "
            "WHERE id = '9' AND summarize_default = false"
        )
    )


def downgrade() -> None:
    # Restores the seeded value. Same limitation in reverse: a reviewer who turned depositions on
    # deliberately after this migration ran would have that choice reverted.
    op.execute(
        sa.text(
            "UPDATE categories SET summarize_default = false "
            "WHERE id = '9' AND summarize_default = true"
        )
    )
