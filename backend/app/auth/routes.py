"""FastAPI-Users routers, mounted under /api/auth and /api/users (P2c).

Assembles the standard router set from the shared `fastapi_users` instance (app/auth/deps.py)
and the cookie `auth_backend`:

- auth:           POST /api/auth/login, POST /api/auth/logout    (opaque DB-session cookie)
- register:       POST /api/auth/register                        (required `name`, non-confirmable)
- reset-password: POST /api/auth/forgot-password, /reset-password (email delivery deferred)
- users:          GET/PATCH /api/users/me, plus FastAPI-Users' superuser-gated /api/users/{id}

No verification router is mounted: registration is non-confirmable (see the P2 plan). The
login/register/forgot/reset paths are on the deny-by-default public allowlist (app/auth/deps.py);
logout and every /api/users route require an authenticated session, enforced by the app-level gate.
"""

from fastapi import APIRouter

from app.auth.backend import auth_backend
from app.auth.deps import fastapi_users
from app.auth.schemas import UserCreate, UserRead, UserUpdate

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
auth_router.include_router(fastapi_users.get_auth_router(auth_backend))
auth_router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))
auth_router.include_router(fastapi_users.get_reset_password_router())

users_router = APIRouter(prefix="/api/users", tags=["users"])
users_router.include_router(fastapi_users.get_users_router(UserRead, UserUpdate))
