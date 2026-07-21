"""Authentication dependencies + the global deny-by-default gate.

`fastapi_users` ties the UserManager to the cookie backend and yields the current-user
dependencies. `enforce_auth` is attached at the app level (see main.py) so EVERY route -- existing
and any added later -- requires an authenticated session unless its path is explicitly public.
The admin area additionally requires a superuser (== our is_admin). Unauthenticated requests get
a 401 (JSON/fetch clients) or a 302 to /login (browser navigations), matching the Flask behavior.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi_users import FastAPIUsers

from app.auth.backend import auth_backend
from app.auth.users import get_user_manager
from app.models import User

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
_current_user_optional = fastapi_users.current_user(active=True, optional=True)

# Reachable without a session. Everything else is denied by default.
_PUBLIC_EXACT = {
    "/",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
}
_PUBLIC_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")
_ADMIN_PREFIXES = ("/api/admin",)


class AuthRedirect(Exception):
    """Signals an unauthenticated browser navigation; main.py converts it to a 302 -> /login.
    JSON/fetch clients get a 401 instead (content negotiation)."""


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _is_admin_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _ADMIN_PREFIXES)


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


async def enforce_auth(
    request: Request, user: User | None = Depends(_current_user_optional)
) -> None:
    if _is_public(request.url.path):
        return
    if user is None:
        if _wants_html(request):
            raise AuthRedirect
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if _is_admin_path(request.url.path) and not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
