"""The classifier reads its category set from the DB catalog and reloads on an edit.

A category edit bumps CatalogMeta.revision; the classifier must pick up the new set (and
rebuild its embedding matrix) on the next call, and a deactivated category must drop out of
the assignable set entirely.
"""

from mrr_ai.services import classification as clf
from mrr_ai.taxonomy import CATEGORIES


def _bump_revision(db):
    from mrr_ai.models import CatalogMeta

    meta = db.session.get(CatalogMeta, 1)
    meta.revision += 1
    db.session.commit()


def test_allowed_ids_match_auto_assignable_categories(app):
    with app.app_context():
        clf.reset_catalog_cache()
        assert clf._allowed_ids() == list(CATEGORIES.keys())  # taxonomy set, id 6 excluded
        assert "6" not in clf._allowed_ids()


def test_catalog_text_reflects_a_name_edit(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import Category

    with app.app_context():
        clf.reset_catalog_cache()
        assert "RENAMED DIAGNOSTICS" not in clf._catalog_text()
        cat = db.session.get(Category, "3")
        cat.name = "RENAMED DIAGNOSTICS"
        db.session.commit()
        _bump_revision(db)
        assert "RENAMED DIAGNOSTICS" in clf._catalog_text()


def test_deactivating_a_category_drops_it_from_the_classifier(app):
    from mrr_ai.extensions import db
    from mrr_ai.models import Category

    with app.app_context():
        clf.reset_catalog_cache()
        assert "9" in clf._allowed_ids()
        db.session.get(Category, "9").active = False
        db.session.commit()
        _bump_revision(db)
        assert "9" not in clf._allowed_ids()


def test_embedding_matrix_rebuilds_only_on_revision_change(app, monkeypatch):
    import numpy as np

    from mrr_ai.extensions import db

    calls = {"n": 0}

    def counting_encode(texts):
        calls["n"] += 1
        return np.eye(len(list(texts)))

    with app.app_context():
        clf.reset_catalog_cache()
        monkeypatch.setattr(clf, "_encode", counting_encode)

        clf._category_vectors()
        clf._category_vectors()
        assert calls["n"] == 1  # built once, reused while the revision is stable

        _bump_revision(db)
        clf._category_vectors()
        assert calls["n"] == 2  # a revision bump forces exactly one rebuild
