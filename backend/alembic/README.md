# backend/alembic

The Alembic migration environment for the backend's Postgres database. Every change to the schema,
and every change to catalog rows (`categories`, `prompts`, `catalog_meta`) that deployed databases
already hold, is a revision file in `versions/`. The chain has one head; `alembic upgrade head`
applies whatever a database is missing.

| Path | What it is |
| --- | --- |
| `env.py` | Wires Alembic to the app: imports `app.models` so every table is on `Base.metadata`, and takes the database URL from `Settings.database_url` (so `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT` must be set). Online runs use one transaction for all pending revisions. |
| `script.py.mako` | The template `alembic revision` fills in for a new file. |
| `versions/` | One file per revision, named `<revision>_<slug>.py`. 31 on 2026-10-02; head `a3d6f0b81e57` (`uv run alembic heads` prints the current one). |
| `README` | Alembic's stock one-line description of the template. Left as generated. |
| `README.md` | This file. |
| `CLAUDE.md` | Rules for AI coding agents working in this folder. |
| `../alembic.ini` | Alembic's config: `script_location`, `prepend_sys_path = .`, a placeholder URL that `env.py` replaces, logging. |

## Use

Run Alembic from `backend/`, where `alembic.ini` lives:

```bash
cd backend
uv run alembic current        # the revision the database is at
uv run alembic heads          # must print exactly one revision
uv run alembic upgrade head   # apply every pending revision
uv run alembic revision --autogenerate -m "short description"   # new schema revision
```

In the compose stack the same commands run inside the `api` container, for example
`docker compose exec -T api alembic upgrade head`.

## Tests

- CI's `backend` job runs `uv run alembic upgrade head` on an empty Postgres before the suite, and
  the `e2e` job runs `docker compose run --rm api alembic upgrade head`
  (`.github/workflows/ci.yml`). Both start from an empty database.
- Parity tests load a catalog revision by path and check its text against the constants:
  `backend/tests/test_catalog.py` (`c8b1d4e70f92`, `e4c8a1f70b93`, `e4b7a2c91d05`) and
  `backend/tests/test_classification.py` (`b3f7c02e91a4`).
- `backend/tests/conftest.py` `warn_if_the_schema_is_behind()` prints a warning when the test
  database is not at the head.

```bash
cd backend
uv run pytest -q tests/test_catalog.py tests/test_classification.py -k migration
```

## Documentation

- [Migrations reference](../../docs/reference/migrations.md): every revision in chain order.
- [How to create a database migration](../../docs/how-to/create-a-database-migration.md).
- [Data model reference](../../docs/reference/data-model.md): every table and column.
