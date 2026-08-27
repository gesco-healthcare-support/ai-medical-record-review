"""catalog: widen category 4 from GI-only to non-orthopedic outpatient procedures

Revision ID: e4c8a1f70b93
Revises: a1e6f4d20c93
Create Date: 2026-08-27 20:40:00.000000

Issue #175. The reviewer was asked whether the bucket should stay gastroenterology-only and said no:
"you should make that a bucket for any non-orthopedic outpatient similar type procedure, but really
that is not something worth worrying about to a significant degree because it's non-orthopedic and
for the most part non-orthopedic records are of little to no concern."

WHY A MIGRATION. `catalog.get_categories` reads the DB catalog and falls back to `taxonomy.py` only
while `categories` is EMPTY, so editing the constants alone changes NOTHING on a box that was ever
seeded - which the deployed one was. This carries the same edit into the live row. Same reason
c8b1d4e70f92 exists for the modality/specimen split, and #138 for category 15.

NO EMPTINESS GUARD IS NEEDED, unlike b3f7c02e91a4. That migration INSERTED a row, which on an
unseeded catalog would have ended the all-or-nothing constants fallback and collapsed the catalog to
that row alone. This one only UPDATEs: on an unseeded catalog the UPDATE matches nothing and the
fallback keeps serving the widened text straight from `taxonomy.py`. Both directions are therefore
correct with no guard, and the revision is still bumped so a seeded box reloads its caches.

Guarded on the EXPECTED text, in the c8b1d4e70f92 style: a description or name an admin rewrote in
the admin UI is left exactly as they left it, and the skip is PRINTED rather than silent - a catalog
that quietly keeps its old text is how a deployed edit appears to have shipped when it did not.

The example title is added ELEMENT-WISE for the same reason that file gives: a wholesale array swap
guarded on the seeded value silently skips a category an admin has added a title to.

Deliberately NOT changed here: the `gi outpatient|outpatient procedure h ?& ?p` rule in
classification.py. Widening a keyword rule is a behaviour change that needs its own blast-radius
measurement, and the issue records this whole item as low priority on the reviewer's own framing -
so the taxonomy says what the bucket MEANS, and the rule keeps answering exactly what it answered
before.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4c8a1f70b93"
down_revision: Union[str, Sequence[str], None] = "a1e6f4d20c93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORY_ID = "4"

_OLD_NAME = "GI outpatient procedure H&P"
_OLD_DESCRIPTION = "Gastrointestinal outpatient procedure history and physical."

# Kept byte-identical to taxonomy.CATEGORIES["4"]; test_catalog asserts the two agree, so editing one
# without the other fails the suite rather than drifting silently.
_NEW_NAME = "Non-orthopedic outpatient procedure H&P"
_NEW_DESCRIPTION = (
    "History and physical for a NON-ORTHOPEDIC outpatient procedure - gastrointestinal "
    "endoscopy and colonoscopy, and similar same-day procedures in other non-orthopedic "
    "specialties. An orthopedic or spinal procedure's history and physical is a treating or "
    "medico-legal report and belongs to its own category, not here."
)
_ADDED_EXAMPLE = "Outpatient Procedure History and Physical"


def _bump_revision() -> None:
    """Force the classifier + worker caches (keyed on the catalog revision) to reload.

    Upsert, not UPDATE: an unseeded catalog has no meta row, so an UPDATE would be a silent no-op
    (mirrors catalog.bump_revision).
    """
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def _set(column: str, new: str, expected: str) -> int:
    """Rewrite one column only while it still holds ``expected``; -> rows changed."""
    result = op.get_bind().execute(
        sa.text(
            f"UPDATE categories SET {column} = :new WHERE id = :cid AND {column} = :expected"  # noqa: S608
        ),
        {"cid": CATEGORY_ID, "new": new, "expected": expected},
    )
    return result.rowcount


# CAST(x AS y) throughout, never the `x::y` shorthand. In a SQLAlchemy `text()` a colon opens a bind
# parameter, so `:title::text` does not parse as "the title parameter, cast to text" - the statement
# reaches the driver with the parameter unbound and fails. b3f7c02e91a4 uses CAST for the same
# reason. `examples` is a JSON column (models.py), so the jsonb operators need it cast explicitly.


def _ensure_example(title: str) -> None:
    """Append one example title if it is not already there (idempotent, element-wise).

    Element-wise rather than a wholesale array swap, following c8b1d4e70f92: a swap guarded on the
    seeded array silently skips a category an admin has added a title to, which is the exact failure
    mode that file exists to avoid.
    """
    op.get_bind().execute(
        sa.text(
            "UPDATE categories "
            "SET examples = CAST(CAST(examples AS jsonb) || to_jsonb(CAST(:title AS text)) AS json) "
            "WHERE id = :cid "
            "AND NOT (CAST(examples AS jsonb) @> to_jsonb(CAST(:title AS text)))"
        ),
        {"cid": CATEGORY_ID, "title": title},
    )


def _remove_example(title: str) -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE categories SET examples = CAST(("
            "  SELECT COALESCE(jsonb_agg(value), CAST('[]' AS jsonb))"
            "  FROM jsonb_array_elements(CAST(examples AS jsonb))"
            "  WHERE value <> to_jsonb(CAST(:title AS text))"
            ") AS json) "
            "WHERE id = :cid AND CAST(examples AS jsonb) @> to_jsonb(CAST(:title AS text))"
        ),
        {"cid": CATEGORY_ID, "title": title},
    )


def _apply(name: str, description: str, old_name: str, old_description: str, add: bool) -> None:
    renamed = _set("name", name, old_name)
    described = _set("description", description, old_description)
    if add:
        _ensure_example(_ADDED_EXAMPLE)
    else:
        _remove_example(_ADDED_EXAMPLE)
    # Report both outcomes. A guarded UPDATE that matched nothing is the normal case on an unseeded
    # catalog AND the signal that an admin has edited the row - and a skip nobody sees is how a
    # catalog keeps its old text while the deploy looks successful.
    print(
        f"category {CATEGORY_ID}: name {'rewritten' if renamed else 'left as it was'}, "
        f"description {'rewritten' if described else 'left as it was'}"
    )
    _bump_revision()


def upgrade() -> None:
    _apply(_NEW_NAME, _NEW_DESCRIPTION, _OLD_NAME, _OLD_DESCRIPTION, add=True)


def downgrade() -> None:
    _apply(_OLD_NAME, _OLD_DESCRIPTION, _NEW_NAME, _NEW_DESCRIPTION, add=False)
