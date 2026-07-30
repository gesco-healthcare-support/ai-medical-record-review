"""catalog: modality vs specimen, and the misfiled example titles

Revision ID: c8b1d4e70f92
Revises: a4f2c9e81b53
Create Date: 2026-07-30 17:30:00.000000

Register defects D-01 to D-06. The classifier reads the DB catalog first (`catalog.get_categories`)
and only falls back to `taxonomy.py`, so editing the constants alone changes NOTHING on a box that
was ever seeded - which every deployed box was. This carries the same edits into the live rows.

Two guarding styles, deliberately different:

- **Descriptions and the one name change** are wholesale rewrites, so they fire only while the row
  still holds the text this migration expects. A description edited in the admin UI is left alone.
- **Example titles are edited ELEMENT-WISE**, never as a wholesale swap. The live category 5 already
  carries two titles an admin added by hand ("Occupational Therapy Daily Note" / "... Progress
  Notes"), which a swap guarded on the seeded array would have silently skipped - the exact failure
  mode this file exists to avoid. Removing or ensuring one element keeps every other title, admin
  additions included, and is idempotent.

Category 6 is deliberately NOT deactivated even though D-06 recommends merging it into 5: 15 live
review rows still carry category 6, and `rows.validate_rows` only accepts ACTIVE categories, so
deactivating it would make every document holding one of those rows unsaveable. The merge is done in
the prompts instead - category 5 gains category 6's point set for daily/SOAP notes, and category 6's
first point becomes Diagnosis (it said "Title", which its own examples contradicted).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8b1d4e70f92"
down_revision: Union[str, Sequence[str], None] = "a4f2c9e81b53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_DESCRIPTIONS = {
    "1": "Routine treating-physician progress notes, office/clinic visits, and follow-ups.",
    "3": (
        "Imaging and diagnostic studies: X-Ray, MRI, CT, EMG/NCS, laboratory reports, sleep "
        "studies, and similar."
    ),
    "5": "Physical therapy, chiropractic, and acupuncture evaluations and progress reports.",
    "12": (
        "Supplemental reports from a QME (Qualified Medical Evaluator) or AME (Agreed Medical "
        "Evaluator) - follow-ups to a prior medical-legal evaluation."
    ),
    "14": "Standalone laboratory or test result documents.",
}

# Kept byte-identical to taxonomy.CATEGORIES; test_catalog asserts the two agree, so a later edit to
# one without the other fails the suite rather than drifting silently.
_NEW_DESCRIPTIONS = {
    "1": (
        "Routine treating-physician progress notes, office/clinic visits, follow-ups, and "
        "emergency-department encounter notes. A supplemental report responding to a prior "
        "medical-legal (QME or AME) evaluation belongs to the QME/AME supplemental category, "
        "not here."
    ),
    "3": (
        "Studies performed ON THE BODY with an instrument, reported as an image or a tracing that a "
        "physician reads: X-Ray, MRI, CT, ultrasound, mammogram, EMG/NCS, ECG, sleep study, bone "
        "density, and endoscopy. A test run on a SPECIMEN taken from the body - blood, urine, or a "
        "toxicology screen - is a laboratory result and belongs to the laboratory category, not here."
    ),
    "5": (
        "Physical therapy, occupational therapy, chiropractic, and acupuncture evaluations, progress "
        "reports, and daily encounter or SOAP notes."
    ),
    "12": (
        "Supplemental reports from a QME (Qualified Medical Evaluator) or AME (Agreed Medical "
        "Evaluator) - follow-ups to a prior medical-legal evaluation. A report headed only "
        '"Supplemental Report" that responds to a prior medical-legal evaluation, an attorney letter, '
        "or newly served records belongs here."
    ),
    "14": (
        "Results of a test run on a SPECIMEN taken from the body: blood panels, urinalysis, cultures, "
        "and toxicology or drug screens. The document reports measured values, often against reference "
        "ranges, rather than an image. A study performed on the body itself - X-Ray, MRI, CT, "
        "ultrasound, EMG/NCS, ECG - is a diagnostic study and belongs to that category, not here."
    ),
}

_OLD_NAME_14 = "Laboratory and test results"
_NEW_NAME_14 = "Laboratory and specimen test results"

# Example titles this migration takes OUT of a category, per defect.
_REMOVALS = {
    "1": ["Supplemental Report"],  # D-04 -> 12
    "3": ["Laboratory Report", "Ed (Emergency Department) Provider Notes"],  # D-01 -> 14, D-03 -> 1
    "5": [
        "History of Present Illness",
        "Physical Examination",
        "Diagnosis",
    ],  # D-05 section headers
    "14": ["Results", "Test Results"],  # D-02: broad enough to attract imaging
}

# Example titles this migration ENSURES are present (added when missing, never duplicated).
_ADDITIONS = {
    "1": ["Ed (Emergency Department) Provider Notes"],
    "5": ["Occupational Therapy Daily Note", "Occupational Therapy Progress Notes"],
    "12": ["Supplemental Report"],
    "14": [
        "Laboratory Report",
        "Laboratory Test Results",
        "Blood Test Results",
        "Complete Blood Count",
        "Comprehensive Metabolic Panel",
        "Urinalysis",
        "Urine Toxicology Screen",
        "Toxicology Report",
        "Culture and Sensitivity",
    ],
}

# Titles the ADDITIONS introduced that had no prior life in that category, so a downgrade can take
# them back out. Category 5's occupational-therapy pair is absent on purpose: it was added live by an
# admin before this migration, so removing it on downgrade would destroy their edit.
_ADDED_BY_THIS_MIGRATION = {
    "1": _ADDITIONS["1"],
    "12": _ADDITIONS["12"],
    "14": _ADDITIONS["14"],
}


def _remove_example(category_id: str, title: str) -> None:
    """Drop one example title from a category, leaving every other title (admin edits included).

    ``jsonb - text`` removes matching array elements, so this is idempotent: running it against a
    category that no longer holds the title is a no-op rather than an error.
    """
    op.get_bind().execute(
        sa.text(
            "UPDATE categories SET examples = (examples::jsonb - CAST(:title AS text))::json "
            "WHERE id = :cid"
        ),
        {"cid": category_id, "title": title},
    )


def _ensure_example(category_id: str, title: str) -> None:
    """Guarantee a category holds this example title exactly once.

    Remove-then-append rather than a bare append: appending alone would duplicate the title on a
    re-run, and on category 5 the title may already be present from a live admin edit.
    """
    op.get_bind().execute(
        sa.text(
            "UPDATE categories SET examples = ((examples::jsonb - CAST(:title AS text)) "
            "|| jsonb_build_array(CAST(:title AS text)))::json WHERE id = :cid"
        ),
        {"cid": category_id, "title": title},
    )


def _set_description(category_id: str, new: str, expected: str) -> int:
    """Rewrite a category description only while it still holds ``expected``; -> rows changed."""
    result = op.get_bind().execute(
        sa.text(
            "UPDATE categories SET description = :new WHERE id = :cid AND description = :expected"
        ),
        {"cid": category_id, "new": new, "expected": expected},
    )
    return result.rowcount


def _set_name(category_id: str, new: str, expected: str) -> int:
    """Rename a category only while it still holds ``expected``; -> rows changed."""
    result = op.get_bind().execute(
        sa.text("UPDATE categories SET name = :new WHERE id = :cid AND name = :expected"),
        {"cid": category_id, "new": new, "expected": expected},
    )
    return result.rowcount


def _bump_revision() -> None:
    """Force the classifier + worker caches (keyed on the catalog revision) to reload.

    Upsert, not UPDATE: an unseeded catalog has no meta row, so an UPDATE would be a silent no-op
    (mirrors catalog.bump_revision).
    """
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def _apply(descriptions: dict, expected: dict, removals: dict, additions: dict) -> None:
    """Run one direction of the change and REPORT what landed.

    The description guard can legitimately skip a row (an admin edited it), and a skip that nobody
    sees is how a catalog silently keeps its old corpus - so print applied vs skipped per category.
    """
    applied, skipped = [], []
    for category_id, new in descriptions.items():
        changed = _set_description(category_id, new, expected[category_id])
        (applied if changed else skipped).append(category_id)
    for category_id, titles in removals.items():
        for title in titles:
            _remove_example(category_id, title)
    for category_id, titles in additions.items():
        for title in titles:
            _ensure_example(category_id, title)
    print(f"category descriptions rewritten: {applied}; left as edited: {skipped}")
    _bump_revision()


def upgrade() -> None:
    if not _set_name("14", _NEW_NAME_14, _OLD_NAME_14):
        print("category 14 name left as edited")
    _apply(_NEW_DESCRIPTIONS, _OLD_DESCRIPTIONS, _REMOVALS, _ADDITIONS)


def downgrade() -> None:
    """Restore the previous corpus: put back what was removed, take out what was introduced.

    Not a perfect inverse - an example title an admin added after the upgrade survives, and category
    5's occupational-therapy pair is left in place (it predates this migration). Both choices favour
    keeping a human edit over restoring a byte-exact history.
    """
    _set_name("14", _OLD_NAME_14, _NEW_NAME_14)
    _apply(_OLD_DESCRIPTIONS, _NEW_DESCRIPTIONS, _ADDED_BY_THIS_MIGRATION, _REMOVALS)
