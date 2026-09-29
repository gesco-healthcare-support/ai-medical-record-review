# backend - agent instructions

Python 3.12, FastAPI, SQLAlchemy 2, Alembic, RQ on Redis, uv. Folder-specific rules are in the
`CLAUDE.md` of `app/`, `app/api/`, `app/auth/`, `app/schemas/`, `app/services/`,
`app/services/llm/`, `app/worker/`, `alembic/`, `scripts/` and `tests/`.

## Rules

- Run everything from `backend/`, never the repo root (the root `pyproject.toml` is the retired
  Flask app).
- `uv sync --extra docs` is the environment for development and CI. Add `--extra classifier` only
  to exercise the segment worker's categorizer (it pulls torch).
- The CI gates must pass: `uv run ruff check .`, `uv run ruff format --check .` AND
  `uvx pyright==1.1.414` (type check of `app/`). After any
  scripted rewrite, run `uv run ruff format .` - the formatter is a separate gate from the linter.
- Services (`app/services/`) must not import FastAPI. Routes call services; services take an
  explicit SQLAlchemy `Session` where they need one.
- The app is multi-process: `api`, `segment-worker` and `summarize-worker` are separate
  containers. Nothing may rely on in-process state surviving between requests or between stages.
  State is Postgres and Redis.
- A SQLAlchemy `Session` is not thread-safe. Resolve database reads before entering a
  `ThreadPoolExecutor`, or open a short-lived session inside the worker thread.
- Never log patient content: filenames, header fields, page text, prompts, model responses.
  Log ids, counts and error types.
- A `backend/.env` is read by the settings whenever you run from `backend/` - including pytest -
  and CI has none. If a test passes in CI but not locally, move it aside first.
- A schema change needs an Alembic migration in the same PR (`alembic/CLAUDE.md`), and a new
  setting, route, table or migration needs its row on the matching reference page (the docs drift
  test fails otherwise).

## Commands

```bash
docker compose -p mrrtest -f ../docker-compose.dev.yml up -d --wait postgres redis   # test stack
uv sync --extra docs
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check .
uvx pyright==1.1.414                            # type check of app/
uv run pytest -q                                # full suite; CI adds -n 3 --dist loadfile
uv run pytest -q tests/test_jobs.py -k cancel   # a subset
```

Rebuild before expecting a container to see a change - both images when the segment worker's code
changed:

```bash
docker compose build api segment-worker summarize-worker
docker compose up -d --force-recreate api segment-worker summarize-worker
```

Docs: `docs/explanation/architecture.md`, `docs/how-to/run-the-tests.md`,
`docs/reference/configuration.md`.
