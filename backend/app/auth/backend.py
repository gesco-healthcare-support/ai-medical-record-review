"""The FastAPI-Users authentication backend: HttpOnly cookie transport + DB session strategy.

Cookie transport (opaque token, HttpOnly, SameSite=Lax) pairs with a DatabaseStrategy that stores
the session token in Postgres -- so sessions are server-side and revocable on logout, and no token
is exposed to JavaScript. SameSite=Lax is the CSRF protection (blocks cross-site unsafe requests
from carrying the cookie); no double-submit token for this same-origin LAN app (see the P2 plan).
"""

from fastapi import Depends
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import AccessTokenDatabase, DatabaseStrategy

from app.auth.db import get_access_token_db
from app.config import get_settings
from app.models import AccessToken

# One working day; a fresh login each morning is acceptable for staff use.
SESSION_LIFETIME_SECONDS = 60 * 60 * 12


def _cookie_transport() -> CookieTransport:
    settings = get_settings()
    return CookieTransport(
        cookie_name="mrr_session",
        cookie_max_age=SESSION_LIFETIME_SECONDS,
        # Secure (HTTPS-only) in production behind the reverse proxy; dev runs over http.
        cookie_secure=(settings.environment == "prod"),
        cookie_httponly=True,
        cookie_samesite="lax",
    )


def get_database_strategy(
    access_token_db: AccessTokenDatabase[AccessToken] = Depends(get_access_token_db),
) -> DatabaseStrategy:
    return DatabaseStrategy(access_token_db, lifetime_seconds=SESSION_LIFETIME_SECONDS)


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=_cookie_transport(),
    get_strategy=get_database_strategy,
)
