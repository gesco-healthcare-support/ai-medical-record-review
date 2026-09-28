# backend/app/auth

Authentication for the FastAPI backend, built on FastAPI-Users: the session cookie, the password
scheme carried over from the earlier Flask-Security app, the user manager, and the deny-by-default
gate that protects every route. Per-record ownership is not here; it is `get_owned_document` in
`../api/deps.py`.

| File | Purpose |
| --- | --- |
| `__init__.py` | Package docstring. |
| `backend.py` | `auth_backend`: an HttpOnly, SameSite=Lax cookie `mrr_session` paired with a database session strategy (`access_token` table); `SESSION_LIFETIME_SECONDS` is 12 hours. |
| `db.py` | The async SQLAlchemy adapters FastAPI-Users needs for users and access tokens. |
| `deps.py` | The `fastapi_users` instance, `current_active_user`, `current_superuser`, the public allowlist and `enforce_auth`, the app-level gate. |
| `password.py` | `MrrPasswordHelper`: base64 HMAC-SHA512 keyed by `SECURITY_PASSWORD_SALT`, then argon2id; never rehashes. |
| `routes.py` | Mounts the FastAPI-Users routers: `/api/auth` (login, logout, register, forgot-password, reset-password) and `/api/users` (`me`, and admin-only `{id}`). |
| `schemas.py` | `UserRead`, `UserCreate` (requires `name`), `UserUpdate`. |
| `users.py` | `UserManager`: the password rule, the reset-token secret (`SECRET_KEY`), and the forgot-password hook, which only logs the user id. |

The admin bootstrap CLI is `../cli.py` (`python -m app.cli admin grant <email>`, also `revoke` and
`list`).

## How it is used

- `backend/app/main.py` attaches `enforce_auth` to the whole app and includes `auth_router` and
  `users_router`.
- Routes in `../api/` depend on `current_active_user` or `current_superuser` from `deps.py`.
- Importing `backend.py` builds the cookie transport, which reads the settings, so importing
  `app.main` needs `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT` in the environment or
  in `.env`.

## Tests

From `backend/`, with the test Postgres and Redis running and migrated:

```bash
uv run pytest tests/test_auth_gate.py tests/test_auth_integration.py tests/test_password.py tests/test_user_manager.py tests/test_cli.py -q
```

| Test file | Covers |
| --- | --- |
| `tests/test_auth_gate.py` | Every branch of `enforce_auth` with a fake request. |
| `tests/test_auth_integration.py` | Login cookie flags, logout revoking the session, registration rules, forgot and reset password. |
| `tests/test_password.py` | Compatibility with the migrated hash construction, and the production cost parameters. |
| `tests/test_user_manager.py` | The password rule. |
| `tests/test_cli.py` | `admin grant`, `revoke` and `list`. |

## Documentation

- [Authentication and access](../../../docs/explanation/auth-and-access.md)
- [How to manage users and admins](../../../docs/how-to/manage-users-and-admins.md)
- [HTTP API reference](../../../docs/reference/http-api.md)
- [Errors and messages reference](../../../docs/reference/errors-and-messages.md)
