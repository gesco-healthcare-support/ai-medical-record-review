# backend - MRR AI API, workers and pipeline

The Python half of MRR AI: the FastAPI app the browser talks to, the RQ workers that run the
pipeline jobs, the pipeline services they share, the database models and migrations, and the
backend test suite. One codebase builds two images: `mrr-backend-web` (the `api` and
`summarize-worker` services) and `mrr-backend-classifier` (the `segment-worker`, which adds the
torch-based categorizer).

How it fits together: [Architecture](../docs/explanation/architecture.md) and
[Pipeline and jobs](../docs/explanation/pipeline-and-jobs.md).

## Layout

| path | what it is |
| --- | --- |
| [`app/`](app/README.md) | The application package: settings, models, the FastAPI app, routers, services, workers. |
| [`alembic/`](alembic/README.md), `alembic.ini` | Database migrations. |
| [`scripts/`](scripts/README.md) | Operator, maintenance, dev and evaluation scripts. |
| [`tests/`](tests/README.md) | The pytest suite (runs against a real Postgres and Redis). |
| `Dockerfile` | Both backend images; `UV_EXTRAS` selects the tier and `GIT_SHA` stamps the build. |
| `pyproject.toml`, `uv.lock` | Dependencies and ruff config. |
| `.env.example` | Settings for running the backend on your machine against the test stack. |
| `run_dev.py` | Runs the API locally on Windows, where psycopg's async driver needs a selector event loop. |

## Dependency tiers

| command | installs | used by |
| --- | --- | --- |
| `uv sync --extra docs` | Core plus PDF, OCR, Word and model SDKs. | `api`, `summarize-worker`, CI, local development. |
| `uv sync --extra docs --extra classifier` | The above plus sentence-transformers (torch). | `segment-worker` only. |

A bare `uv sync` omits the `docs` extra and the app will not import. The `dev` dependency group
(pytest, ruff and the rest) is installed by default.

## Run the API on your machine

The app normally runs in Docker ([Run the app locally](../docs/how-to/run-the-app-locally.md)). To
run the API from source against the test stack:

```bash
# from the repo root: the test database and Redis
docker compose -p mrrtest -f docker-compose.dev.yml up -d postgres redis

cd backend
uv sync --extra docs
cp .env.example .env            # once; fill in SECRET_KEY and SECURITY_PASSWORD_SALT
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000     # Windows: uv run python run_dev.py
```

The API answers `GET http://127.0.0.1:8000/health`. A worker runs with
`uv run python -m app.worker segment` or `... summarize` (the segment worker needs the
`classifier` extra). Settings are read from the `.env` in the directory you run from, so from
`backend/` that is `backend/.env`, not the repo root's.

## Tests

```bash
cd backend
uv run ruff check . && uv run ruff format --check .    # both are CI gates
uvx pyright==1.1.414                                   # type check of app/, a CI gate too
uv run pytest -q                                        # do not export DATABASE_URL
```

The suite needs the test stack above and refuses to run against the app database. Full
instructions, the coverage floor and the test layout: [Run the tests](../docs/how-to/run-the-tests.md)
and [`tests/README.md`](tests/README.md).

<!-- reviewed: 2026-09-30 -->
