# backend/scripts

Command-line scripts that work on the database or the stored PDFs outside the request path:
maintenance jobs, developer proofs and measurement harnesses. None of them is imported by the
application. Each file's module docstring is its own manual; the site's scripts reference collects
them.

| Path | What it is |
| --- | --- |
| `copy_records.py` | Copies one user's documents (rows and the stored PDF) under another user. Reusable, idempotent; `--dry-run`. |
| `backfill_doi.py` | One-off, idempotent rewrite of the `**DOI**` prefix on stored summaries from an isolated model read. Explicit scope; `--dry-run` still makes the model calls. |
| `repair_dedup_clobbered_status.py` | One-off, idempotent repair of `documents.status` values a duplicate check overwrote. Explicit scope; `--dry-run`. |
| `migrate_from_sqlite.py` | One-shot copy of the retired Flask app's SQLite database into an empty, migrated Postgres. Not idempotent. |
| `dev/` | Developer proofs run by hand inside a container. See `dev/README.md`. |
| `eval/` | Measurement scripts and their helper modules. See `eval/README.md`. |
| `CLAUDE.md` | Rules for AI coding agents working in this folder. |

## Running

The backend image contains this folder at `/app/scripts` with `PYTHONPATH=/app`, so on the server
run a script inside the `api` container, from the checkout directory:

```bash
docker compose exec -T api python scripts/repair_dedup_clobbered_status.py --all --dry-run
```

From a checkout, the four scripts in this folder add the backend root to `sys.path` themselves, so
they run from `backend/`:

```bash
cd backend
uv run python scripts/copy_records.py --help
```

Every script that imports the app needs `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT`
set, and acts on whatever database `DATABASE_URL` names.

## Tests

```bash
cd backend
uv run pytest -q tests/test_backfill_doi.py tests/test_repair_dedup_status.py
```

Both load their script by file path. `copy_records.py` and `migrate_from_sqlite.py` have no tests.
`backend/pyproject.toml` limits pytest to `tests/`, so nothing here is collected as a test.

## Documentation

- [Scripts reference](../../docs/reference/scripts.md): every script, its flags, writes, model
  calls and run command.
- [Data model reference](../../docs/reference/data-model.md): the tables these scripts touch.

<!-- reviewed: 2026-09-30 -->
