# How to run the tests

> **[LEGACY DOC]** **The paths here are wrong.** The current suite is:
>
> ```bash
> docker compose -f docker-compose.dev.yml up -d postgres   # test DB on port 5432
> cd backend && uv run alembic upgrade head && uv run pytest -q
> ```
>
> Two things that will waste your time otherwise: a fresh test database needs `alembic upgrade head` or every DB test errors with `relation "user" does not exist`; and **do not set `DATABASE_URL`** - `conftest.py` finds the right database itself and refuses the app database on 5433, because the fixtures insert and delete rows. The frontend suite is `cd frontend && pnpm test`.


Tests use pytest with the Flask test client; external services (OpenAI, Gemini, OCR) are
mocked, so no API keys or network are needed.

```bash
uv sync                       # installs dev deps incl. pytest
uv run pytest                 # run the suite
uv run pytest -q tests/unit   # just unit tests
uv run pytest --cov=mrr_ai --cov-report=term-missing   # coverage
```

## Conventions

- `tests/conftest.py` provides the `app`/`client` fixtures (built via `create_app()` with
  dummy env) and patches the LLM clients + OCR.
- `tests/unit/` - pure functions in `mrr_ai/services/`.
- `tests/integration/` - routes via the test client, externals mocked.
- Never use real patient data; build tiny synthetic PDFs/CSVs in the test.

Coverage target is ~90%; CI fails under ~85% (see ADR-0005 / pyproject `[tool.coverage]`).
