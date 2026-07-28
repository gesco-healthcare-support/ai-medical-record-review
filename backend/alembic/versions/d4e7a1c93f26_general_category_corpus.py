"""richer General (100) category corpus

Revision ID: d4e7a1c93f26
Revises: a7c3f2e9b1d4
Create Date: 2026-07-27 16:00:00.000000

The classifier's embedding + LLM stages read the category's description and examples, and General
(100) said only "Documents that do not clearly fit any specific category" - nothing for an
administrative document to match on, which is why routing slips and cover letters kept landing in
clinical categories. This replaces that text with one that names what actually belongs there.

Guarded: the UPDATE only fires while the row still holds the seeded text, so a description edited in
the admin UI is never clobbered. The catalog revision is bumped so worker caches (which key off it)
reload instead of serving the old corpus until restart.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7a1c93f26"
down_revision: Union[str, Sequence[str], None] = "a7c3f2e9b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_DESCRIPTION = "Documents that do not clearly fit any specific category."
_OLD_EXAMPLES = '["General Documents", "Everything else"]'
_NEW_DESCRIPTION = (
    "Administrative, correspondence and other documents that do not fit a specific clinical "
    "category: in-house routing slips, cover letters, emails and faxes, legal declarations, "
    "proofs of service, records requests and record indexes."
)
_NEW_EXAMPLES = (
    '["Medical Records Routing Sheet", "Email - Evaluation Cover Letter", '
    '"Declaration of Compliance", "Proof of Service", "Schedule of Records", '
    '"Medical Evaluation Request"]'
)


def _swap(description: str, examples: str, expected: str) -> None:
    """Set 100's corpus, but only while it still matches `expected` (never clobber an admin edit)."""
    op.execute(
        f"UPDATE categories SET description = '{description}', examples = '{examples}'::json "  # noqa: S608
        f"WHERE id = '100' AND description = '{expected}'"
    )
    # The classifier caches the catalog + embedding matrix per revision; bump so it reloads.
    op.execute("UPDATE catalog_meta SET revision = revision + 1 WHERE id = 1")


def upgrade() -> None:
    _swap(_NEW_DESCRIPTION, _NEW_EXAMPLES, _OLD_DESCRIPTION)


def downgrade() -> None:
    _swap(_OLD_DESCRIPTION, _OLD_EXAMPLES, _NEW_DESCRIPTION)
