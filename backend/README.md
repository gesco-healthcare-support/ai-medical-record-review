# MRR AI backend (FastAPI)

The live backend. The Next.js + FastAPI re-platform
(`docs/plans/2026-07-14-nextjs-fastapi-rewrite.md`) shipped and is deployed; the pre-rewrite Flask
app moved to `../legacy/` in #92 and nothing in it runs. See `../legacy/README.md`.

## Dev setup

```bash
# 1. Local Postgres + Redis (from the repo root):
docker compose -f ../docker-compose.dev.yml up -d

# 2. Backend deps + env:
cd backend
uv sync --extra docs         # web/summarize tier: OCR, docx, google-genai. Torch-FREE, and what CI
                             # runs. Bare `uv sync` omits these and the app will not import.
                             # Add --extra classifier ONLY for the segment worker (pulls torch).
cp .env.example .env         # fill SECRET_KEY + SECURITY_PASSWORD_SALT (carry the salt from the Flask .env)

# 3. Run the API:
uv run uvicorn app.main:app --reload --port 8000
# health check: http://localhost:8000/health
```

## Running the tests

The suite drives the real ASGI app against a real Postgres, so it needs a database. Use a
SEPARATE compose project - the app stack in `docker-compose.yml` also defines `postgres`, so
bringing the dev file up under the default project would recreate the app's database container
against the dev volume and point the running app at an empty DB:

```bash
# from the repo root - isolated project, own volume, publishes 5432
docker compose -p mrrtest -f docker-compose.dev.yml up -d postgres redis

cd backend
uv run alembic upgrade head
uv run pytest -q
```

Notes, each of which has cost someone hours:

- **`.env` lives at the REPO ROOT**, not in `backend/`. Values containing backslashes or spaces
  (e.g. `TESSERACT_CMD` on Windows) must be wrapped in SINGLE quotes - double quotes make
  python-dotenv interpret `\t` as a tab and silently corrupt the path.
- **The app stack publishes Postgres on 5433 with a generated password**, while the test defaults
  target 5432 with the throwaway `mrr_dev_only`. Pointing the suite at the app's database fails
  with `password authentication failed`, which is a credential problem, not a networking one.
- **A missing database used to hang the suite forever** with no output, because psycopg blocks on
  connect and pytest buffers. `tests/conftest.py` now sets `connect_timeout=5`, so this fails in
  seconds with a legible `OperationalError` instead.
- **Both CI gates must pass**: `ruff check .` AND `ruff format --check .`. The lint alone is not
  the gate.
- A run that dies part-way (wrong credentials, Ctrl-C) leaves its `pytest-auth-%` accounts behind,
  and the next run's first tests can fail with `ForeignKeyViolation` on `access_token` before the
  autouse cleanup catches up. Just run it again - a clean second pass is green. Do not go hunting
  for a product bug on the strength of a first-run failure burst.

Green run for reference: **313 passed** on this recipe (2026-07-30).

## Layout
- `app/config.py` - settings (pydantic-settings), Vertex-only, fail-fast in prod.
- `app/db.py` - lazy engine/session + declarative `Base`.
- `app/models.py` - SQLAlchemy models (fsqla auth schema mirrored + 9 domain tables).
- `app/main.py` - FastAPI app (routers added in later phases).

## Status
Phase 1a (foundation) - scaffold + models. Next: Alembic baseline + SQLite->Postgres migration
(P1b), then auth (P2), documents API (P3), the RQ job pipeline (P4), admin (P5), parity (P6).
