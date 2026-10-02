"""catalog: add category 18, illegible document

Revision ID: b7e4c1a9d203
Revises: a3d6f0b81e57
Create Date: 2026-10-02 12:00:00.000000

Adding a category to the constants alone reaches NOTHING on a deployed box. `seed_catalog()` returns
early the moment any `Category` row exists, and the catalog is read from the DB first
(`catalog.get_categories`) with the constants only as an unseeded fallback - so on every box that was
ever seeded, a new constant is invisible. This carries the row in.

WHY THE CATEGORY EXISTS. Reviewer request, 2026-10-02: a handwritten, illegible or incomplete note
should not simply be ignored; the review should record that it was there and that it could not be
read. A reviewer assigns this category on the row, and its summary is the single line "Document was
illegible." - built in code, no model call, because a model asked to summarize a page nobody can read
is the shape that invents content.

auto_assign FALSE, like category 6: the classifier never assigns it. Telling a scan the classifier
reads badly from a document nobody can read is a judgement about the page, and getting it wrong in
this direction replaces a real summary with the line above. So only a reviewer sets it.

NO PROMPT ROW, and none is needed: `summarize_row` returns the fixed line before any prompt is read.

Guarded like the rest of the catalog migrations: the insert happens only on a SEEDED catalog and is
`ON CONFLICT DO NOTHING`, so a box where an admin already created id 18 by hand keeps their row.
Downgrade REFUSES to delete the row while any review row still carries the category.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "b7e4c1a9d203"
down_revision: Union[str, Sequence[str], None] = "a3d6f0b81e57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CATEGORY_ID = "18"

# Kept byte-identical to seed_catalog._ILLEGIBLE; test_catalog asserts the two agree, so editing one
# without the other fails the suite rather than drifting silently.
_NAME = "Illegible document"
_DESCRIPTION = (
    "A document that is in the record but cannot be read - handwritten, illegible, or incomplete. "
    "Chosen by a reviewer; its summary states only that the document was illegible."
)
_EXAMPLES: list[str] = []


def _bump_revision() -> None:
    """Force the classifier + worker caches (keyed on the catalog revision) to reload.

    Upsert, not UPDATE: an unseeded catalog has no meta row, so an UPDATE would be a silent no-op
    (mirrors catalog.bump_revision).
    """
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    if context.is_offline_mode():
        # `alembic upgrade --sql` (the Squawk lint in CI) has no database to read, so the seeded-catalog
        # guard below cannot run as Python. The same guard as ONE statement: insert only when another
        # category row exists, and never over an existing id 18.
        op.execute(
            "INSERT INTO categories "
            "(id, name, description, examples, active, auto_assign, summarize_default, updated_at) "
            f"SELECT {_quote(CATEGORY_ID)}, {_quote(_NAME)}, {_quote(_DESCRIPTION)}, "
            f"CAST({_quote(json.dumps(_EXAMPLES))} AS json), true, false, true, now() "
            f"WHERE EXISTS (SELECT 1 FROM categories WHERE id <> {_quote(CATEGORY_ID)}) "
            "ON CONFLICT (id) DO NOTHING"
        )
        _bump_revision()
        return
    bind = op.get_bind()
    # ONLY touch a catalog that is already seeded. An empty `categories` table is the normal state
    # for a fresh box, local dev and CI, and `catalog.get_categories` falls back to the constants
    # only while it is EMPTY: one inserted row would end that fallback and collapse the catalog to
    # this category alone (measured when b3f7c02e91a4 did it for category 15). The constants already
    # carry category 18, so doing nothing on an unseeded catalog is both safe and correct.
    seeded = bind.execute(
        sa.text("SELECT count(*) FROM categories WHERE id <> :cid"), {"cid": CATEGORY_ID}
    ).scalar()
    if not seeded:
        print(
            f"categories table is unseeded - category {CATEGORY_ID} NOT inserted. The catalog "
            "falls back to the constants, which already carry it; inserting one row here would "
            "make that fallback stop and collapse the catalog to this category alone."
        )
        _bump_revision()
        return
    result = bind.execute(
        sa.text(
            "INSERT INTO categories "
            "(id, name, description, examples, active, auto_assign, summarize_default, updated_at) "
            "VALUES (:cid, :name, :description, CAST(:examples AS json), true, false, true, now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "cid": CATEGORY_ID,
            "name": _NAME,
            "description": _DESCRIPTION,
            "examples": json.dumps(_EXAMPLES),
        },
    )
    if result.rowcount:
        print(f"category {CATEGORY_ID} inserted ({_NAME})")
    else:
        print(f"category {CATEGORY_ID} already present - left exactly as it is")
    _bump_revision()


def downgrade() -> None:
    """Remove the category, but only while nothing references it."""
    if context.is_offline_mode():
        op.execute(
            f"DELETE FROM categories WHERE id = {_quote(CATEGORY_ID)} AND NOT EXISTS "
            f"(SELECT 1 FROM review_rows WHERE category = {_quote(CATEGORY_ID)})"
        )
        _bump_revision()
        return
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT count(*) FROM review_rows WHERE category = :cid"),
        {"cid": CATEGORY_ID},
    ).scalar()
    if in_use:
        print(
            f"category {CATEGORY_ID} kept: {in_use} review row(s) still carry it, and "
            "validate_rows accepts ACTIVE categories only - deleting it would make every "
            "document holding one of those rows unsaveable"
        )
        return
    bind.execute(sa.text("DELETE FROM categories WHERE id = :cid"), {"cid": CATEGORY_ID})
    print(f"category {CATEGORY_ID} removed")
    _bump_revision()
