"""Integration tests for the auth routers (P2c), driving the ASGI app against docker Postgres.

Covers the P2c acceptance: login sets the HttpOnly SameSite=Lax cookie; a protected route succeeds
only with the session; wrong credentials are rejected; logout revokes the session; register
enforces the password rule + required name and round-trips to a login; the forgot/reset-password
endpoints exist (email delivery deferred, so we assert the contract, not a delivered token).
"""

from httpx import AsyncClient

from tests.conftest import unique_test_email


async def test_login_sets_cookie_and_protected_route_works(
    client: AsyncClient, seeded_user: tuple[str, str]
) -> None:
    email, password = seeded_user

    # No session yet -> the app-level gate denies /api/users/me (JSON client -> 401).
    assert (await client.get("/api/users/me")).status_code == 401

    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    set_cookie = " ".join(resp.headers.get_list("set-cookie")).lower()
    assert "mrr_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    me = await client.get("/api/users/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


async def test_wrong_password_rejected(client: AsyncClient, seeded_user: tuple[str, str]) -> None:
    email, _ = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": "Wr0ng#pw"})
    assert resp.status_code == 400  # FastAPI-Users LOGIN_BAD_CREDENTIALS
    assert (await client.get("/api/users/me")).status_code == 401


async def test_logout_revokes_session(client: AsyncClient, seeded_user: tuple[str, str]) -> None:
    email, password = seeded_user
    await client.post("/api/auth/login", data={"username": email, "password": password})
    assert (await client.get("/api/users/me")).status_code == 200

    out = await client.post("/api/auth/logout")
    assert out.status_code == 204
    assert (await client.get("/api/users/me")).status_code == 401


async def test_register_enforces_password_rule(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": unique_test_email(), "password": "weak", "name": "New User"},
    )
    assert resp.status_code == 400  # InvalidPasswordException -> REGISTER_INVALID_PASSWORD


async def test_register_requires_name(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": unique_test_email(), "password": "Str0ng#pw1"},
    )
    assert resp.status_code == 422  # missing required `name`


async def test_register_then_login(client: AsyncClient) -> None:
    email = unique_test_email()
    password = "Str0ng#pw1"
    reg = await client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": "New User"},
    )
    assert reg.status_code == 201
    body = reg.json()
    assert body["email"] == email
    assert body["is_active"] is True
    assert body["is_superuser"] is False

    login = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert login.status_code == 204


async def test_forgot_password_accepts_and_reset_validates(
    client: AsyncClient, seeded_user: tuple[str, str]
) -> None:
    email, _ = seeded_user

    forgot = await client.post("/api/auth/forgot-password", json={"email": email})
    assert forgot.status_code == 202  # accepted; email delivery deferred

    # An unknown email must ALSO return 202 (no account enumeration).
    unknown = await client.post(
        "/api/auth/forgot-password", json={"email": "pytest-auth-nobody@example.com"}
    )
    assert unknown.status_code == 202

    # The reset endpoint exists and rejects a bogus token.
    bad = await client.post(
        "/api/auth/reset-password",
        json={"token": "not-a-valid-token", "password": "Str0ng#pw1"},
    )
    assert bad.status_code == 400
