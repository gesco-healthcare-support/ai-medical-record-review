"""catalog: add category 17, job description

Revision ID: e4b7a2c91d05
Revises: c2f1a7d94e63
Create Date: 2026-09-24 12:00:00.000000

Adding a category to `taxonomy.py` alone reaches NOTHING on a deployed box. `seed_catalog()` returns
early the moment any `Category` row exists, and the classifier reads the DB catalog first
(`catalog.get_categories`) with the constants only as an unseeded fallback - so on every box that was
ever seeded, a new constant is invisible. This carries the row in.

WHY THE CATEGORY EXISTS. A job description is the employer's written description of a position -
its duties and its physical demands. It had no rule and no category, and measured over every row on
the live box every one of them (22 rows, 49 pages) landed in 100, with five ticked for summary by
hand. Asked on 2026-09-24 and answered by the senior reviewer: a job description is summarized, the
points are the position title, employer, duties, physical demands and hours, and the length is
detailed - the example he gave runs to the whole list of duties.

NO PROMPT ROW IS INSERTED, deliberately, for the reason d7c1a9e34b28 gives: a seeded prompt row
shadows `prompts.py` forever. `catalog.get_prompt` resolves this category's own row, then its CODE
prompt, so `prompts.py["category_17"]` is picked up with no row at all.

Guarded like the rest of the catalog migrations: the insert happens only on a SEEDED catalog and is
`ON CONFLICT DO NOTHING`, so a box where an admin already created id 17 by hand keeps their row.
Downgrade REFUSES to delete the row while any review row still carries the category.
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7a2c91d05"
down_revision: Union[str, Sequence[str], None] = "c2f1a7d94e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CATEGORY_ID = "17"

# Kept byte-identical to taxonomy.CATEGORIES["17"]; test_catalog asserts the two agree, so editing
# one without the other fails the suite rather than drifting silently.
_NAME = "Job description"
_DESCRIPTION = (
    "The employer's written description of a job: the position title, its duties and "
    "responsibilities, and its physical demands and working environment - how much is lifted, "
    "how long the worker stands, sits or bends, the hours worked, and the equipment used. It "
    "describes the job, not the patient, so a medical report that discusses the job belongs "
    "to that report's own category."
)
_EXAMPLES = [
    "Job Description",
    "Description of Employee's Job Duties",
    "Job Requirements",
    "Essential Functions Job Description",
    "Position Description",
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
    # this category alone. Measured, not reasoned about, when b3f7c02e91a4 did it for category 15:
    # running that migration against the test database collapsed the catalog, and
    # `llm_classify("Progress Report")` started returning None because "1" was no longer an allowed
    # id. Same guard here, same reason.
    #
    # An unseeded catalog needs no row - `taxonomy.CATEGORIES["17"]` is already in the fallback - so
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
