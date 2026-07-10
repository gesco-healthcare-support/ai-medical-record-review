"""Admin category + prompt CRUD API (/api/admin). Gated by is_admin via the app-level gate.

Covers validation (numeric non-colliding ids, non-empty name/text, immutable id), that edits
bump the catalog revision and write an audit row, and that a deactivation drops a category from
the editor set.
"""

from types import SimpleNamespace

import pytest
from flask_security import hash_password

from mrr_ai import catalog
from mrr_ai.extensions import db


def _login(client, user):
    return client.post(
        "/login", data={"email": user.email, "password": user.password}, follow_redirects=True
    )


@pytest.fixture
def admin_client(app):
    with app.app_context():
        datastore = app.extensions["security"].datastore
        record = datastore.create_user(
            email="admin@example.com", password=hash_password("password123")
        )
        record.is_admin = True
        db.session.commit()
        admin = SimpleNamespace(email="admin@example.com", password="password123")
    test_client = app.test_client()
    _login(test_client, admin)
    return test_client


def test_list_categories_includes_inactive(admin_client, app):
    from mrr_ai.models import Category

    with app.app_context():
        db.session.get(Category, "9").active = False
        db.session.commit()
    body = admin_client.get("/api/admin/categories").get_json()
    ids = {c["id"] for c in body}
    assert "9" in ids  # inactive still listed for the admin
    nine = next(c for c in body if c["id"] == "9")
    assert nine["active"] is False


def test_create_category_appears_and_bumps_revision(admin_client, app):
    with app.app_context():
        before = catalog.catalog_version()
    resp = admin_client.post(
        "/api/admin/categories",
        json={"id": "42", "name": "New Category", "description": "d", "examples": ["x"]},
    )
    assert resp.status_code == 201
    with app.app_context():
        assert catalog.catalog_version() == before + 1
        assert "42" in catalog.get_category_ids(active_only=True)


def test_create_rejects_colliding_id(admin_client):
    resp = admin_client.post("/api/admin/categories", json={"id": "3", "name": "dup"})
    assert resp.status_code == 400


def test_create_rejects_non_numeric_id(admin_client):
    resp = admin_client.post("/api/admin/categories", json={"id": "6a", "name": "bad"})
    assert resp.status_code == 400


def test_create_rejects_empty_name(admin_client):
    resp = admin_client.post("/api/admin/categories", json={"id": "43", "name": "  "})
    assert resp.status_code == 400


def test_patch_updates_name_and_audits(admin_client, app):
    resp = admin_client.patch("/api/admin/categories/3", json={"name": "Imaging Studies"})
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "Imaging Studies"
    with app.app_context():
        from mrr_ai.models import AuditLog, Category

        assert db.session.get(Category, "3").name == "Imaging Studies"
        assert AuditLog.query.filter_by(action="category.update").count() >= 1


def test_patch_cannot_change_id(admin_client, app):
    resp = admin_client.patch("/api/admin/categories/3", json={"id": "999", "name": "x"})
    assert resp.status_code == 200  # body id is ignored, not honored
    with app.app_context():
        from mrr_ai.models import Category

        assert db.session.get(Category, "3") is not None
        assert db.session.get(Category, "999") is None


def test_patch_deactivate_drops_from_editor_set(admin_client, app):
    assert admin_client.patch("/api/admin/categories/9", json={"active": False}).status_code == 200
    with app.app_context():
        assert "9" not in catalog.get_category_ids(active_only=True)


def test_patch_unknown_category_404(admin_client):
    assert admin_client.patch("/api/admin/categories/777", json={"name": "x"}).status_code == 404


def test_put_prompt_creates_row_for_category_without_one(admin_client, app):
    # Category 11 ships with no summary prompt (inherits general); an admin can author one.
    resp = admin_client.put("/api/admin/prompts/11", json={"text": "Summarize interval history."})
    assert resp.status_code == 200
    with app.app_context():
        assert catalog.get_prompt("summary", "11") == "Summarize interval history."


def test_put_prompt_rejects_empty_text(admin_client):
    assert admin_client.put("/api/admin/prompts/1", json={"text": "  "}).status_code == 400


def test_put_prompt_unknown_category_404(admin_client):
    assert admin_client.put("/api/admin/prompts/777", json={"text": "x"}).status_code == 404
