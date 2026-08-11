"""Boot-seed of the Category / Prompt / CatalogMeta tables from the hardcoded constants.

The seed must reproduce today's catalog exactly (so behavior is unchanged) and be
idempotent, since it runs on every boot via _create_schema.
"""

from mrr_ai.prompts import prompts as PROMPT_TEXTS
from mrr_ai.taxonomy import CATEGORIES


def test_seed_creates_all_categories(app):
    from mrr_ai.models import Category

    with app.app_context():
        # 14 taxonomy ids + the editor-only id 6.
        assert Category.query.count() == len(CATEGORIES) + 1
        ids = {c.id for c in Category.query.all()}
        assert ids == set(CATEGORIES.keys()) | {"6"}
        assert all(c.active for c in Category.query.all())


def test_taxonomy_categories_are_auto_assignable(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import Category

    with app.app_context():
        for cid in CATEGORIES:
            cat = db.session.get(Category, cid)
            assert cat is not None
            assert cat.auto_assign is True
            assert cat.name == CATEGORIES[cid].name
            assert cat.examples == list(CATEGORIES[cid].examples)


def test_id_six_seeded_but_not_auto_assignable(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import Category

    with app.app_context():
        six = db.session.get(Category, "6")
        assert six is not None
        assert six.active is True
        assert six.auto_assign is False  # selectable in the editor, never auto-assigned


def test_seed_creates_summary_prompts_except_category_11(app):
    from mrr_ai.models import Prompt

    with app.app_context():
        summaries = Prompt.query.filter_by(role="summary").all()
        by_cat = {p.category_id: p for p in summaries}
        # Every taxonomy id that has a hardcoded prompt, plus id 6; never category 11.
        assert "11" not in by_cat
        assert by_cat["1"].text == PROMPT_TEXTS["category_01"]
        assert by_cat["100"].text == PROMPT_TEXTS["category_100"]
        assert by_cat["6"].text == PROMPT_TEXTS["category_06"]
        # Count = taxonomy ids with a prompt (all but 11) + id 6.
        assert len(summaries) == (len(CATEGORIES) - 1) + 1


def test_catalog_meta_seeded_at_revision_one(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import CatalogMeta

    with app.app_context():
        meta = db.session.get(CatalogMeta, 1)
        assert meta is not None
        assert meta.revision == 1


def test_seed_is_idempotent(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import Category, Prompt
    from mrr_ai.seed_catalog import seed_catalog

    with app.app_context():
        before_cats = Category.query.count()
        before_prompts = Prompt.query.count()
        seed_catalog(db)  # second run must be a no-op
        seed_catalog(db)
        assert Category.query.count() == before_cats
        assert Prompt.query.count() == before_prompts
