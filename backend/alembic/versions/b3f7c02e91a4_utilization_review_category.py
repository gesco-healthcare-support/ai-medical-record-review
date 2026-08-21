"""catalog: add category 15, utilization review and independent medical review

Revision ID: b3f7c02e91a4
Revises: c5d81f6a3b70
Create Date: 2026-08-21 10:40:00.000000

Adding a category to `taxonomy.py` alone reaches NOTHING on a deployed box. `seed_catalog()` returns
early the moment any `Category` row exists, and the classifier reads the DB catalog first
(`catalog.get_categories`) with the constants only as an unseeded fallback - so on every box that was
ever seeded, a new constant is invisible. This carries the row in.

WHY THE CATEGORY EXISTS. A utilization review letter is a reviewing physician's determination on
whether requested treatment is medically necessary - certified, modified, or denied. Measured over
every row on the box, that one document type was being answered FOUR different ways: 10 twelve
times, 100 four times, 3 three times and 5 twice. On the single reviewed copy a human put four
identical documents into three different categories. Category 10 (Request For Authorization) is the
most understandable of those answers and still wrong: 10 is the treating physician ASKING, and this
is the answer coming back. Confirmed with Adam 2026-08-21.

NO PROMPT ROW IS INSERTED, deliberately. f1a83b5c60d2 exists to DELETE seeded prompt rows because
they shadow `prompts.py` forever - a prompt row for 15 would recreate that bug for the new category
on the day it is created. `catalog.get_prompt` resolves this category's own row, then its CODE
prompt, so `prompts.py["category_15"]` is picked up with no row at all.

Guarded like the rest of the catalog migrations: the insert is `ON CONFLICT DO NOTHING`, so a box
where an admin already created id 15 by hand keeps their row untouched rather than being overwritten.

Downgrade REFUSES to delete the row while any review row still carries the category.
`rows.validate_rows` only accepts ACTIVE categories, so removing a category that live rows reference
would make every document holding one of those rows unsaveable - the same trap c8b1d4e70f92 recorded
for category 6.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7c02e91a4"
down_revision: Union[str, Sequence[str], None] = "c5d81f6a3b70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CATEGORY_ID = "15"

# Kept byte-identical to taxonomy.CATEGORIES["15"]; test_catalog asserts the two agree, so editing
# one without the other fails the suite rather than drifting silently.
_NAME = "Utilization review and independent medical review (UR/IMR)"
_DESCRIPTION = (
    "A determination on whether requested treatment is medically necessary, written by a "
    "reviewing physician for the claims administrator rather than by the treating physician: "
    "utilization review (UR) decisions certifying, modifying, or denying a request, and "
    "independent medical review (IMR) determinations deciding an appeal against a UR denial. "
    "The treating physician's own REQUEST for that treatment is a Request For Authorization and "
    "belongs to that category, not here - this category holds the ANSWER to it."
)
_EXAMPLES = [
    "Utilization Review Determination",
    "Utilization Review Letter",
    "Utilization Review - Non-Certification",
    "Utilization Review - Modification",
    "Independent Medical Review Determination",
    "IMR Final Determination Letter",
]


def _bump_revision() -> None:
    """Force the classifier + worker caches (keyed on the catalog revision) to reload.

    Upsert, not UPDATE: an unseeded catalog has no meta row, so an UPDATE would be a silent no-op
    (mirrors catalog.bump_revision).
    """
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def upgrade() -> None:
    bind = op.get_bind()
    # ONLY touch a catalog that is already seeded, and this guard is the whole reason this migration
    # is not a bare INSERT.
    #
    # `catalog.get_categories` falls back to `taxonomy.py` when the `categories` table is EMPTY, and
    # an empty table is the normal state for a fresh box, a local dev database and CI - seed_catalog()
    # is called nowhere in the app. Inserting a row unconditionally flips such a table from "empty, so
    # use all fifteen constants" to "one row, so use only that one", and the catalog collapses to
    # category 15 alone. Measured, not reasoned about: running this migration against the test
    # database did exactly that, and `llm_classify("Progress Report")` started returning None because
    # "1" was no longer an allowed id.
    #
    # An unseeded catalog needs no row - `taxonomy.CATEGORIES["15"]` is already in the fallback - so
    # doing nothing there is both safe and correct.
    seeded = bind.execute(
        sa.text("SELECT count(*) FROM categories WHERE id <> :cid"), {"cid": CATEGORY_ID}
    ).scalar()
    if not seeded:
        print(
            f"categories table is unseeded - category {CATEGORY_ID} NOT inserted. The catalog "
            "falls back to taxonomy.py, which already carries it; inserting one row here would "
            "make that fallback stop and collapse the catalog to this category alone."
        )
        _bump_revision()
        return
    result = bind.execute(
        sa.text(
            "INSERT INTO categories "
            "(id, name, description, examples, active, auto_assign, summarize_default, updated_at) "
            "VALUES (:cid, :name, :description, CAST(:examples AS json), true, true, true, now()) "
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
