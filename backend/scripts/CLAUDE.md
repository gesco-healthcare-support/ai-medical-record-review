# backend/scripts - agent instructions

One-off and reusable command-line scripts that read or write the database and stored PDFs outside
the request path. Every script: `docs/reference/scripts.md`. Tables: `docs/reference/data-model.md`.

## Never without the user's explicit go

- Running any script here against a database that holds real records (the server, the app database
  on port 5433). They read PHI, several write rows, and some send pages to a model.
- Running `backfill_doi.py`, `dev/verify_deposition_format.py` or any `eval/` script that calls a
  model. `--dry-run` does NOT make `backfill_doi.py` free: it makes every model call and only skips
  the write.
- `migrate_from_sqlite.py`. It is a historical one-shot and is not idempotent.

## Rules for a new or changed maintenance script

- Scope is ALWAYS explicit: require exactly one of `--user-email` / `--document-id` (repeatable) /
  `--all`, refuse zero or several, and print the scope before reading anything
  (`backfill_doi.py` `scoped_document_ids()`). A shared box holds several people's records.
- Offer `--dry-run` that writes nothing.
- Make it idempotent, and say how in the docstring (a guard on current state, or a marker row).
- Print ids, counts and statuses only. Never `original_filename`, titles, page text, summary text
  or patient fields - they are PHI. Truncated document ids (`document.id[:8]`) are fine.
- Guard every write on the state it expects, and roll back when the evidence is missing
  (`repair_dedup_clobbered_status.py` `restorable_status()`, `backfill_doi.py` `run()`).
- Header: add the backend root to `sys.path` and import `app.models` so the script runs as a file
  from `backend/` and in the container:
  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))`.
- Use `app.db.get_sessionmaker()()`; never build a second engine.
- Write the file before the row when both are needed, and remove the file if the transaction fails
  (`copy_records.py` `copy_document()`).
- Add a test that loads the script by path (pattern: `tests/test_backfill_doi.py`), with a
  fixture where the thing the script must NOT touch is present.
- Examples in docstrings use `example.com` addresses and `<uuid>` placeholders. The repo is public.

## Traps

- `copy_records.py` `_clone()` copies every mapped column. A new column travels automatically;
  add it to that call's `exclude` set if it must not.
- `copy_records.py` keeps its own `ACTIVE_STATES`. Keep it equal to `app/services/jobs.py`
  `ACTIVE_STATES`.
- Emails: `backfill_doi.py` matches case-insensitively, `copy_records.py` and
  `repair_dedup_clobbered_status.py` match exactly.
- `summaries.row_start` / `row_end` are snapshots and go stale after a boundary edit. Address a
  live row through `review_rows`.
- `Summary.idx` is not `ReviewRow.idx`.
- In the container the working directory is `/app`; a default path relative to the working
  directory (`migrate_from_sqlite.py --sqlite`) does not resolve the way it does from `backend/`.

## Commands (from `backend/`)

```bash
uv run ruff check scripts && uv run ruff format --check scripts
uv run pytest -q tests/test_backfill_doi.py tests/test_repair_dedup_status.py
uv run python scripts/<name>.py --help
```

Subfolders have their own instructions: `dev/CLAUDE.md`, `eval/CLAUDE.md`.

<!-- reviewed: 2026-09-30 -->
