# backend/app/auth - agent instructions

FastAPI-Users wiring: cookie sessions, the Flask-Security-compatible password scheme, the user
manager and the app-level auth gate. Full explanation: `docs/explanation/auth-and-access.md`.

## Rules that must hold

- `MrrPasswordHelper.verify_and_update` returns `(verified, None)`. Never return a new hash: the
  migrated accounts' hashes must stay byte-identical or those users are locked out.
- Do not change the pre-hash (`base64(HMAC-SHA512(SECURITY_PASSWORD_SALT, password))`) or the
  hashing of existing accounts. The argon2 parameters (t=3, m=65536, p=4) only govern NEW hashes;
  verification reads each hash's own parameters.
- `SECURITY_PASSWORD_SALT` keys every password hash; changing it makes every stored password fail.
  `SECRET_KEY` signs only the reset-password and verification JWTs, not the session cookie.
- `enforce_auth` stays attached to the whole app in `app/main.py`. Make a path public only by adding
  its exact path to `_PUBLIC_EXACT`. `_PUBLIC_PREFIXES` is matched with `startswith`: never add a
  protected route whose path starts with `/health`, `/docs`, `/redoc` or `/openapi.json`, and do
  not add prefixes.
- Admin protection is two checks: `_ADMIN_PREFIXES` in the gate and `current_superuser` on the
  admin router (`app/api/admin.py`). Keep both.
- `is_superuser`, `is_active` and `hashed_password` are SQLAlchemy synonyms for the `is_admin`,
  `active` and `password` columns (`app/models.py`). Do not rename those columns.
- `SESSION_LIFETIME_SECONDS` sets both the cookie `Max-Age` and the database token lifetime. Change
  the constant, not one side.
- The cookie is `Secure` only when `ENVIRONMENT == "prod"`. Production must be served over HTTPS or
  the browser drops the cookie and sign-in silently fails.
- Registration uses `safe=True`: new accounts are never admin. Registration is non-confirmable;
  nothing may depend on `is_verified`.
- The password rule in `UserManager.validate_password` is mirrored by `passwordRules` in
  `frontend/components/auth/password-checklist.tsx`. Change both together.
- Log user ids only. Never log a session token, a reset token, a password or an email address.
- `UserManager.__init__` reads `SECRET_KEY` at construction so importing `users.py` needs no
  environment. `backend.py` builds the cookie transport at import and DOES read the settings, so
  importing `app.main` needs `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT`.

## Traps that have bitten before

- FastAPI-Users adapters are async-only (`db.py`, `get_async_db`). Domain routes use the sync
  session; only the user id crosses between the two.
- The gate resolves the user with `current_user(active=True, optional=True)`: an inactive user is
  treated as anonymous (401), and login refuses them with `LOGIN_BAD_CREDENTIALS`.
- `on_after_forgot_password` only logs; there is no mail transport. Do not claim otherwise in code
  comments or UI copy you write.
- A new user's jobs go to a per-user queue lane that running workers learn about only at start
  (`app/worker/__main__.py`); the workers must be restarted after a user registers.
- The admin CLI (`app/cli.py`) matches the email case-sensitively; FastAPI-Users' own lookup is
  case-insensitive.

## Commands

From `backend/`, with the test Postgres and Redis up and migrated (`docs/how-to/run-the-tests.md`):

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest tests/test_auth_gate.py tests/test_auth_integration.py tests/test_password.py tests/test_user_manager.py tests/test_cli.py -q
```

After touching the gate or the admin checks, also run:

```bash
uv run pytest tests/test_documents_api.py -k idor -q
uv run pytest tests/test_admin_api.py -q
```

## Docs to update with a change here

`docs/explanation/auth-and-access.md`, `docs/how-to/manage-users-and-admins.md`, and the auth and
users tables in `docs/reference/http-api.md`.
