"""T3: registration (confirm password), login, and the deny-by-default gate.

The gate matters more than the happy path here: every pre-existing route carries PHI,
so the tests probe a SAMPLE of every blueprint anonymously and assert nothing serves.
"""


def _register(
    client,
    email="new-user@example.com",
    password="register-test-password-1!",
    confirm=None,
    name="Sample Reviewer",
):
    data = {
        "email": email,
        "password": password,
        "password_confirm": confirm if confirm is not None else password,
    }
    if name is not None:
        data["name"] = name
    return client.post("/register", data=data, follow_redirects=False)


def test_anonymous_html_requests_redirect_to_login(anon_client):
    for path in ("/", "/review", "/pages", "/checkCSV", "/IndividualMRR"):
        response = anon_client.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path


def test_anonymous_json_requests_get_401(anon_client):
    response = anon_client.get("/api/segment/status", headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_anonymous_unsafe_methods_denied_on_legacy_routes(anon_client):
    for path in ("/upload", "/getPages", "/exportresultstoword", "/reset"):
        response = anon_client.post(path)
        assert response.status_code in (302, 401), path


def test_login_page_public(anon_client):
    assert anon_client.get("/login").status_code == 200
    assert anon_client.get("/register").status_code == 200


def test_register_rejects_mismatched_confirm(app, anon_client):
    response = _register(anon_client, confirm="different-Passw0rd")
    assert response.status_code == 200  # form re-rendered with errors, no redirect

    from mrr_ai.models import User

    with app.app_context():
        assert User.query.filter_by(email="new-user@example.com").count() == 0


def test_register_login_roundtrip(app, anon_client):
    response = _register(anon_client)
    assert response.status_code == 302  # registered -> redirect

    from mrr_ai.models import User

    with app.app_context():
        user = User.query.filter_by(email="new-user@example.com").one()
        assert user.name == "Sample Reviewer"  # the Name field is persisted

    fresh = app.test_client()
    login = fresh.post(
        "/login",
        data={"email": "new-user@example.com", "password": "register-test-password-1!"},
        follow_redirects=False,
    )
    assert login.status_code == 302
    assert fresh.get("/").status_code == 200


def test_register_requires_name(app, anon_client):
    """Name is required at the form level (the column stays nullable for legacy rows)."""
    response = _register(anon_client, email="noname@example.com", name=None)
    assert response.status_code == 200  # re-rendered with errors, no redirect

    from mrr_ai.models import User

    with app.app_context():
        assert User.query.filter_by(email="noname@example.com").count() == 0


def test_register_rejects_password_missing_number(app, anon_client):
    """Server-side twin of the registration checklist: a direct POST (bypassing all
    client-side gating) must be rejected by the same rules the UI shows."""
    response = _register(anon_client, email="np@example.com", password="letters-only-here!")
    assert response.status_code == 200  # form re-rendered with errors
    assert b"number" in response.data.lower()

    from mrr_ai.models import User

    with app.app_context():
        assert User.query.filter_by(email="np@example.com").count() == 0


def test_register_rejects_password_missing_symbol(app, anon_client):
    response = _register(anon_client, email="ns@example.com", password="password1111")
    assert response.status_code == 200
    assert b"symbol" in response.data.lower()

    from mrr_ai.models import User

    with app.app_context():
        assert User.query.filter_by(email="ns@example.com").count() == 0


def test_authenticated_client_reaches_protected_routes(client):
    assert client.get("/").status_code == 200  # landing page
    assert client.get("/classic").status_code == 200
    assert client.get("/review/some-doc-id").status_code == 200  # shell; data is API-gated
    assert client.get("/api/segment/status").status_code == 200


def test_csrf_enforced_when_enabled(tmp_path):
    """One app with real CSRF: an unsafe request without a token must 400."""
    from mrr_ai import create_app

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "csrf.db"),
            "SECURITY_PASSWORD_HASH": "plaintext",
        }
    )
    response = app.test_client().post(
        "/login", data={"email": "x@example.com", "password": "irrelevant"}
    )
    assert response.status_code == 400
