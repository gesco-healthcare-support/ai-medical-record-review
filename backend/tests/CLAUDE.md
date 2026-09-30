# backend/tests/ - agent instructions

The backend pytest suite. Full docs: `docs/how-to/run-the-tests.md`,
`docs/reference/ci-and-merge-gates.md`. File list: `README.md` in this folder.

## Commands

Run from `backend/` unless noted. The test stack must be up first (from the repo root):

```bash
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
```

```bash
uv sync --extra docs
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
uv run pytest -q tests/test_<area>.py
uv run pytest -q -n 3 --dist loadfile
uv run pytest -n 3 --dist loadfile --cov=app --cov-branch --cov-report=xml --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
```

CI fails on any test failure, either ruff command, or backend coverage below 90% (lines plus
branches, one total).

## Rules

- Synthetic data only. No real names, dates of birth, record content or PDFs. Emails come from
  `unique_test_email()` in `conftest.py` (`example.com`, per-worker prefix). An account created
  any other way is never cleaned up.
- Never call a real model, the Tesseract or Poppler binaries, or any network service. Stub the
  provider, the `genai` client and OCR the way the neighbouring tests in the same file do. CI has
  no model credentials, and the CI backend job does not install Tesseract or Poppler. The test
  stack's Postgres and Redis are real and are meant to be used.
- Never set `DATABASE_URL` for pytest, and never point the suite at port 5433 (the app database).
  `conftest.py` derives the test URL from the Compose files and raises if it would use the app
  stack. `MRR_ALLOW_APP_DATABASE=1` overrides that guard; do not use it.
- Do not add a `backend/.env` expecting tests to ignore it: the settings read `.env` from the
  working directory, so it changes what tests see. CI has none.
- Put new tests in this folder, named `test_*.py`. `testpaths = ["tests"]` in
  `backend/pyproject.toml`; files elsewhere (for example `scripts/`) are not collected.
- `asyncio_mode = "auto"`: write `async def test_...` with no marker.
- Keep imports of `app.*` inside test modules normal; `conftest.py` sets the environment before the
  app is first imported. Do not import `app.*` from a new conftest or plugin that loads earlier.

## Fixtures and helpers (conftest.py)

- `client`: `httpx.AsyncClient` on the ASGI app, base URL `http://test`.
- `seeded_user`: `(email, password)` for an account inserted directly with the real password
  scheme.
- `_clean_test_users` (autouse): deletes every account with this worker's prefix, and everything it
  owns, before and after each test. It queries the database, so EVERY test needs Postgres up.
- `lanes(kind)`: all RQ queues for a job kind (base plus per-user lanes) with `.count`, `.jobs`,
  `.empty()`. Assert "a job was enqueued" through it, not through one lane name.
- `PRODUCTION_HASHER`: the real argon2 hasher. Everything else hashes at the cheapest cost; only
  `test_password.py` should pin the production cost.

## Parallel runs (pytest-xdist)

- Worker `gwN` gets database `mrr_gwN` (cloned from `mrr` with `CREATE DATABASE ... TEMPLATE`
  before workers start), Redis database N+1, and email prefix `pytest-auth-gwN-`.
- The clone fails if anything is connected to `mrr` (psql, another pytest run, a host-run API).
  Close it or run serially.
- A test that reads a whole table or a whole queue is safe only because of this isolation. Do not
  hardcode database names, Redis database numbers or the email prefix.

## Tests that pin files outside this folder

- `test_compose_passthrough.py` and `test_pool_wiring.py` read `docker-compose.yml`,
  `.env.example` and `app/config.py`. Adding or renaming a setting that operators tune means:
  field and comment in `app/config.py`, `NAME: ${NAME:-<same default>}` in the compose
  `x-backend-env` block, and a line in `.env.example`. `TESSERACT_CMD` must stay out of compose.
- `test_conftest_db_selection.py` parses both Compose files. Changing the Postgres port, password
  form or Redis port there breaks it and the suite's database discovery.
- Other files cover `scripts/` (`test_backfill_doi.py`, `test_repair_dedup_status.py`,
  `test_job_health.py`, the eval-script tests). They load the script by file path
  (`importlib.util.spec_from_file_location` or a `sys.path` insert), so moving or renaming a
  script breaks its test.

## Traps that have bitten before

- A schema one migration behind makes most database tests fail naming a missing column. The session
  start prints `[conftest] The test database is at X, but the migrations head is Y`. Migrate.
- A missing test database used to hang the suite silently; `connect_timeout=5` in the derived URL
  prevents it. Keep it if you touch URL building.
- Another program's Redis on 6379 is used silently by the queue tests. The test stack's Redis must
  own that port.
- A test never seen failing proves nothing: break the behaviour once, watch the test fail for the
  right reason, restore.
