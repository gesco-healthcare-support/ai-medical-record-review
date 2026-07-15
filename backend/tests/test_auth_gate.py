"""Unit tests for the deny-by-default authentication gate (P2b).

enforce_auth is the app-level dependency that protects every route (existing and future) unless
its path is explicitly public. It is tested directly (fake Request + injected user) so the gate's
branching is verified without a live server or DB: public-allow, unauthenticated-deny (401 JSON
vs 302 for browser navigations), and the admin-path superuser check.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.deps import AuthRedirect, enforce_auth


def _req(path: str, accept: str = "application/json") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"accept", accept.encode())],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1234),
        }
    )


class _User:
    def __init__(self, is_superuser: bool) -> None:
        self.is_superuser = is_superuser


async def test_public_path_allows_anonymous():
    assert await enforce_auth(_req("/health"), user=None) is None


async def test_protected_path_anonymous_json_401():
    with pytest.raises(HTTPException) as exc:
        await enforce_auth(_req("/api/documents"), user=None)
    assert exc.value.status_code == 401


async def test_protected_path_anonymous_browser_redirects():
    with pytest.raises(AuthRedirect):
        await enforce_auth(_req("/api/documents", accept="text/html"), user=None)


async def test_admin_path_non_admin_forbidden():
    with pytest.raises(HTTPException) as exc:
        await enforce_auth(_req("/api/admin/categories"), user=_User(is_superuser=False))
    assert exc.value.status_code == 403


async def test_admin_path_admin_allowed():
    assert await enforce_auth(_req("/api/admin/categories"), user=_User(is_superuser=True)) is None


async def test_protected_path_authenticated_allowed():
    assert await enforce_auth(_req("/api/documents"), user=_User(is_superuser=False)) is None
