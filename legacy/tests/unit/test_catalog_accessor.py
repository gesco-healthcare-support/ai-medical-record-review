"""The DB-first catalog accessor: category sets, prompt resolution, and versioning.

These guarantee behavior parity with the pre-DB constants (the seeded DB must reproduce the
old category sets and prompt lookups) and the fallbacks the wiring relies on.
"""

from mrr_ai.prompts import prompts as PROMPT_TEXTS
from mrr_ai.taxonomy import CATEGORIES


def test_editor_set_is_active_categories_including_six(app):
    from mrr_ai import catalog

    with app.app_context():
        ids = catalog.get_category_ids(active_only=True)
        # Matches the old EDITABLE_CATEGORIES: taxonomy ids + 6, sorted by int.
        assert ids == sorted({*CATEGORIES.keys(), "6"}, key=int)


def test_classifier_set_excludes_six(app):
    from mrr_ai import catalog

    with app.app_context():
        ids = catalog.get_category_ids(auto_assign=True)
        # The classifier's assignable set is exactly the taxonomy ids (id 6 is not auto-assigned).
        assert ids == sorted(CATEGORIES.keys(), key=int)
        assert "6" not in ids


def test_get_prompt_returns_db_text(app):
    from mrr_ai import catalog

    with app.app_context():
        assert catalog.get_prompt("summary", "1") == PROMPT_TEXTS["category_01"]
        assert catalog.get_prompt("summary", "100") == PROMPT_TEXTS["category_100"]


def test_get_prompt_missing_category_falls_back_to_general(app):
    from mrr_ai import catalog

    with app.app_context():
        # Category 11 has no prompt row; it must resolve to the general (100) prompt.
        assert catalog.get_prompt("summary", "11") == PROMPT_TEXTS["category_100"]


def test_get_prompt_reflects_an_edit(app):
    from mrr_ai import catalog
    from mrr_ai.extensions import db
    from mrr_ai.models import Prompt

    with app.app_context():
        row = Prompt.query.filter_by(role="summary", category_id="1").first()
        row.text = "EDITED PROMPT BODY"
        db.session.commit()
        assert catalog.get_prompt("summary", "1") == "EDITED PROMPT BODY"


def test_deactivating_a_category_drops_it_from_both_sets(app):
    from mrr_ai import catalog
    from mrr_ai.extensions import db
    from mrr_ai.models import Category

    with app.app_context():
        cat = db.session.get(Category, "9")
        cat.active = False
        db.session.commit()
        assert "9" not in catalog.get_category_ids(active_only=True)
        assert "9" not in catalog.get_category_ids(auto_assign=True)


def test_catalog_version_is_readable(app):
    from mrr_ai import catalog

    with app.app_context():
        assert catalog.catalog_version() == 1
