"""Admin authorization: the ``is_admin`` flag, the /admin + /api/admin gate, and the CLI.

The gate is admin-specific behavior layered on top of the existing deny-by-default auth
gate: authenticated-but-not-admin must get 403 inside the admin area, while every existing
non-admin route and the anonymous deny path must behave exactly as before.
"""

from types import SimpleNamespace

import pytest
from flask_security import hash_password

from mrr_ai.extensions import db


def _login(client, user):
    return client.post(
        "/login", data={"email": user.email, "password": user.password}, follow_redirects=True
    )


@pytest.fixture
def admin_user(app):
    """A registered user with is_admin set."""
    with app.app_context():
        datastore = app.extensions["security"].datastore
        record = datastore.create_user(
            email="admin@example.com", password=hash_password("password123")
        )
        record.is_admin = True
        db.session.commit()
        return SimpleNamespace(id=record.id, email="admin@example.com", password="password123")


@pytest.fixture
def admin_client(app, admin_user):
    test_client = app.test_client()
    _login(test_client, admin_user)
    return test_client


# ---------- the gate ----------


def test_admin_api_blocks_authenticated_non_admin(client):
    """A normal signed-in user is forbidden from the admin API (403, not 404/redirect)."""
    assert client.get("/api/admin/whoami").status_code == 403


def test_admin_api_allows_admin(admin_client):
    resp = admin_client.get("/api/admin/whoami")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["is_admin"] is True
    assert body["email"] == "admin@example.com"


def test_admin_api_denies_anonymous(anon_client):
    """No session at all: the base deny-by-default gate still applies (never 200/403-with-data)."""
    resp = anon_client.get("/api/admin/whoami")
    assert resp.status_code in (301, 302, 401)


def test_non_admin_route_unaffected_for_normal_user(client):
    """The admin gate must not over-block: a normal user still reaches ordinary routes."""
    assert client.get("/api/documents").status_code == 200


# ---------- the CLI ----------


def test_cli_grant_sets_is_admin(app, user):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "grant", user.email])
    assert result.exit_code == 0
    with app.app_context():
        from mrr_ai.models import User

        assert User.query.filter_by(email=user.email).first().is_admin is True


def test_cli_revoke_clears_is_admin(app, admin_user):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "revoke", admin_user.email])
    assert result.exit_code == 0
    with app.app_context():
        from mrr_ai.models import User

        assert User.query.filter_by(email=admin_user.email).first().is_admin is False


def test_cli_grant_unknown_email_fails(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "grant", "nobody@example.com"])
    assert result.exit_code != 0


def test_cli_list_shows_admins(app, admin_user):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["admin", "list"])
    assert result.exit_code == 0
    assert admin_user.email in result.output
