# backend/alembic - agent instructions

Alembic revisions for the Postgres schema and for catalog rows already stored on deployed
databases. Full procedure: `docs/how-to/create-a-database-migration.md`. Every revision:
`docs/reference/migrations.md`. Every column: `docs/reference/data-model.md`.

## Invariants

- Exactly one head. Run `uv run alembic heads` before and after adding a revision; two heads make
  `alembic upgrade head` fail everywhere.
- Never edit a revision that has reached `main`. Deployed databases have already run it and will not
  run it again. Add a new revision instead. The only accepted edit is re-pointing `down_revision` of
  YOUR unmerged revision when `main` gained one first; record it in the file (see `f0f4d21dbb53`).
- Schema changes go in `backend/app/models.py` AND a revision, together. SQLite tests build tables
  from the models (`Base.metadata.create_all`), Postgres gets them from the revisions; they must
  agree.
- Every foreign key to `documents.id` is `NO ACTION` in the database. A new table referencing
  `documents` needs an ORM `delete-orphan` relationship on `Document`, or documents become
  undeletable (`tests/test_models_methods.py` guards this).
- The active-job set (`queued`, `running`, `paused`) lives in five places: the partial index
  `uq_one_active_job_per_document` (model `__table_args__` and a revision), `ACTIVE_STATES` in
  `app/services/jobs.py`, `Document.active_job`, and `ACTIVE_STATES` in `scripts/copy_records.py`.
  Change all or none.

## Rules for a new revision

- New column on a populated table: nullable, or NOT NULL with a `server_default`
  (`e7b4c1a92d58`). Provenance columns (model, backend, fingerprint, build, method): nullable, NO
  server default, NO backfill - NULL means "not recorded".
- Review autogenerate output line by line. Server defaults are not compared and renames come out as
  drop + add. Delete anything you did not intend.
- Write a docstring that says what and why, and a `downgrade()`. State in the docstring when the
  downgrade cannot restore data.
- Keep the file self-contained: freeze constants inside it. Do not import from `app` (Alembic
  imports every revision to build the chain; `f1a83b5c60d2` already pins
  `app.services.seed_catalog.code_summary_prompt` - do not rename it).
- Raw SQL gets no ORM defaults: supply `updated_at` and any other Python-default column yourself.

## Catalog data revisions (categories / prompts / catalog_meta)

- INSERT a category only when `categories` already holds another row; otherwise print and skip.
  One row in an empty table collapses the catalog to that row (measured).
- INSERT with `ON CONFLICT (id) DO NOTHING`.
- NEVER insert a `prompts` row and NEVER call `seed_catalog()`: a prompt row shadows
  `app/services/prompts.py` forever.
- Guard every UPDATE on the expected old text. Edit `examples` one element at a time.
- `CAST(x AS json)`, never `x::json`, next to bind parameters in `sa.text()`. Use bind parameters,
  not f-strings (do not copy `d4e7a1c93f26`).
- Bump `catalog_meta.revision` with the UPSERT
  `INSERT ... VALUES (1, 1) ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1`,
  on the skip path too.
- `print()` what was applied and what was skipped.
- Downgrade must refuse to delete a category any `review_rows` row still uses.
- Add a parity test loading the revision by path (pattern:
  `tests/test_catalog.py::test_the_job_description_migration_carries_the_constants_text`) and an
  unseeded-fallback test. CI only runs the unseeded branch.
- Do not copy `a9c4e13f70b2`: it cannot tell a seeded value from an admin's choice.

## Traps that have bitten

- A revision applied locally BEFORE its parent was re-pointed leaves the database stamped past
  revisions it never ran; `upgrade head` then "succeeds" with columns missing. Rebuild the test
  database; do not try to patch it.
- `docker compose down -v` without `-p mrrtest -f docker-compose.dev.yml` deletes the APP's volumes.
- Alembic does not refuse the app database. Check `DATABASE_URL` is port 5432 (test), not 5433 (app),
  before any `upgrade` / `downgrade`.
- `env.py` builds `Settings`: `DATABASE_URL`, `SECRET_KEY`, `SECURITY_PASSWORD_SALT` must be set
  (env or `backend/.env`) for every command that connects (`upgrade`, `downgrade`, `current`,
  `revision --autogenerate`).

## Commands (from `backend/`)

```bash
uv run alembic heads
uv run alembic current
uv run alembic revision --autogenerate -m "short description"
uv run alembic revision -m "short description"          # data-only
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
uv run ruff check alembic && uv run ruff format --check alembic
uv run pytest -q tests/test_catalog.py tests/test_classification.py -k migration
uv run pytest -q
```

Never run `upgrade` or `downgrade` against the server or the app database without the user's go.

<!-- reviewed: 2026-09-30 -->
