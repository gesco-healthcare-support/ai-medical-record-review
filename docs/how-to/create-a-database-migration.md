# How to create a database migration

Use this when a change needs the Postgres schema to change (a new column, table or index), or when
catalog rows (`categories`, `prompts`) already stored on a deployed database have to change. The
schema is only ever changed by an Alembic revision in `backend/alembic/versions/`; nothing creates
or alters tables at application start.

Editing `backend/app/services/taxonomy.py` or `backend/app/services/prompts.py` alone does not
change a database whose `categories` table already has rows. For what to edit on the code side of
a category change, see [How to add or change a category](add-or-change-a-category.md); this page
covers the migration half.

## Prerequisites

- The backend environment is installed and the test database and Redis are running, as in
  [How to run the tests](run-the-tests.md). The test database is `docker-compose.dev.yml`'s
  Postgres on port 5432.
- `DATABASE_URL`, `SECRET_KEY` and `SECURITY_PASSWORD_SALT` are available to Alembic, because
  `backend/alembic/env.py` builds `Settings` to get the database URL. The simplest way is
  `backend/.env` copied from `backend/.env.example`, whose `DATABASE_URL` already points at the
  test database.

> **Warning:** Alembic has no guard against the wrong database. Before running any command below,
> check that `DATABASE_URL` names port 5432 (the test database), not 5433 (the running application's
> database).

All commands run in Git Bash (or any bash) from `backend/` unless a step says otherwise.

## Steps

### 1. Bring the test database to the current head

```bash
cd backend
uv run alembic upgrade head
uv run alembic current
```

Expected: `current` prints the revision marked `(head)`; today that is `e4b7a2c91d05`. The chain
must have exactly one head:

```bash
uv run alembic heads
```

Expected: one line. If `upgrade head` fails because a revision id in the database is unknown, see
[If it fails](#if-it-fails).

### 2. Change the model (schema changes only)

Edit `backend/app/models.py` first; the SQLite-backed tests build their schema from the models
(`Base.metadata.create_all()`), and autogenerate diffs the models against the database. Rules the
existing schema follows:

- **A new column on a populated table** is nullable, or NOT NULL with a `server_default` so the
  `ALTER` can fill existing rows (pattern: `e7b4c1a92d58`). If the column should have no server
  default afterwards, add it with one and drop it in the same revision (pattern: `a0725d467a48`).
- **A provenance column** (which model, backend, prompt or build produced a row) is nullable with
  no server default and no backfill. NULL must keep meaning "not recorded"
  ([Data model reference](../reference/data-model.md)).
- **Write the column's NULL meaning in a comment beside it**, as every provenance column in
  `models.py` does.
- **A new table with a foreign key to `documents.id`** needs a
  `relationship(..., cascade=_CASCADE_DELETE_ORPHAN)` on `Document`. The database rule is
  `NO ACTION`, so without it no document that has such a row can be deleted.
  `backend/tests/test_models_methods.py` `test_every_table_that_references_a_document_is_cascaded_by_it()`
  fails until you add it.
- **A change to which job states count as active** must change four places together: the
  `postgresql_where` / `sqlite_where` predicate in `Job.__table_args__`, a migration that drops and
  recreates `uq_one_active_job_per_document` (pattern: `c2d5e8f1a3b7`), `ACTIVE_STATES` in
  `backend/app/services/jobs.py`, and `Document.active_job` (plus `ACTIVE_STATES` in
  `backend/scripts/copy_records.py`).
- **`backend/scripts/copy_records.py` copies every mapped column automatically.** Add the new
  column to the relevant `_clone(..., exclude=...)` set only if a copied record must not carry it.

### 3. Generate the revision file

For a schema change (the database must be at head, step 1):

```bash
uv run alembic revision --autogenerate -m "short description of the change"
```

For a data-only change there is nothing to diff, so create an empty revision:

```bash
uv run alembic revision -m "short description of the change"
```

Expected: a new file `backend/alembic/versions/<revision>_<slug>.py` built from
`backend/alembic/script.py.mako`, with `down_revision` set to the current head.

### 4. Review and finish the file by hand

- `down_revision` is the current head (`uv run alembic heads` before you generated it).
- Delete every generated operation you did not intend. `env.py` sets no compare options, so
  Alembic's defaults apply: column types are compared, server defaults are NOT compared, and a
  rename is generated as a drop plus an add. Check `server_default` values and renames yourself.
- Replace the file's docstring with what the revision does and why, including the NULL meaning of
  new columns and anything a later reader needs before reversing it. Most revisions in the chain
  do this; `b9d3e5f81c47` and `a1e6f4d20c93` are good models.
- Write a `downgrade()` that reverses the change. Where an exact reverse is impossible (a data
  update, a dropped value), say so in the docstring, as `a7c3f2e9b1d4` and `c8b1d4e70f92` do.
- Keep the file self-contained. Alembic imports every revision file to build the chain, so an import
  from `app` (as `f1a83b5c60d2` does) ties loading the whole chain to that name continuing to exist.
  Freeze any constant you need inside the file, as the category revisions do.
- Raw SQL gets no ORM defaults. A column that only has a Python-side default (for example
  `prompts.updated_at` or `categories.updated_at`) must be given a value in the statement.

### 5. For catalog data: follow the guarded pattern

A revision that writes `categories`, `prompts` or `catalog_meta` must be safe on three kinds of
database: an empty catalog (a fresh box, a developer database, CI), a seeded catalog, and a seeded
catalog an admin has edited. The existing revisions settle each rule with a recorded reason:

| Rule | Why | Model revision |
| --- | --- | --- |
| Only INSERT a category when the table already holds another row; otherwise print and skip. | On an empty table the app serves every category from `taxonomy.py`; one inserted row ends that fallback and the catalog collapses to that single category (measured on the test database). | `e4b7a2c91d05`, `d7c1a9e34b28`, `b3f7c02e91a4` |
| INSERT with `ON CONFLICT (id) DO NOTHING`. | An admin may already have created that id by hand. | same |
| Never insert a `prompts` row; never call `seed_catalog()`. | Prompts resolve database-first, so a row shadows `prompts.py` for good; `f1a83b5c60d2` exists to delete such rows. | `e4b7a2c91d05` |
| Guard a wholesale rewrite on the expected OLD text (`... WHERE id = :cid AND description = :expected`). | A description an admin edited is left alone. | `c8b1d4e70f92`, `e4c8a1f70b93` |
| Edit `examples` element by element (remove or ensure one title), never swap the whole array. | A swap guarded on the old array silently skips a category an admin added a title to. | `c8b1d4e70f92`, `e4c8a1f70b93` |
| Use `CAST(x AS y)`, never `x::y`, and bind parameters rather than f-strings. | In `sa.text()` a colon opens a bind parameter, so `:title::text` does not parse. `d4e7a1c93f26` builds SQL with f-strings; do not copy that form. | `e4c8a1f70b93` |
| Bump `catalog_meta.revision` by UPSERT, including on the skip path. | The classifier caches the catalog per revision; an empty catalog has no meta row, so a plain UPDATE does nothing. | all of the above |
| `print()` what was applied and what was skipped. | A guarded statement that matched nothing is otherwise invisible, and the deploy looks successful. | all of the above |
| Downgrade deletes a category only when no `review_rows` row uses it. | `validate_rows()` accepts only active categories, so removing a used one makes those documents unsaveable. | `e4b7a2c91d05` |
| Do not blindly copy `a9c4e13f70b2`. | It cannot tell a seeded value from an admin's deliberate choice and overwrites either. | - |

The body of a category insert, condensed from `e4b7a2c91d05`, to go below the generated revision
identifiers (fill in your own id and text, which must be byte-identical to the new entry in
`taxonomy.CATEGORIES`):

```python
import json

import sqlalchemy as sa
from alembic import op

CATEGORY_ID = "18"
_NAME = "..."
_DESCRIPTION = "..."
_EXAMPLES = ["...", "..."]


def _bump_revision() -> None:
    op.execute(
        "INSERT INTO catalog_meta (id, revision) VALUES (1, 1) "
        "ON CONFLICT (id) DO UPDATE SET revision = catalog_meta.revision + 1"
    )


def upgrade() -> None:
    bind = op.get_bind()
    seeded = bind.execute(
        sa.text("SELECT count(*) FROM categories WHERE id <> :cid"), {"cid": CATEGORY_ID}
    ).scalar()
    if not seeded:
        print(f"categories table is unseeded - category {CATEGORY_ID} NOT inserted")
        _bump_revision()
        return
    result = bind.execute(
        sa.text(
            "INSERT INTO categories "
            "(id, name, description, examples, active, auto_assign, summarize_default, updated_at) "
            "VALUES (:cid, :name, :description, CAST(:examples AS json), true, true, true, now()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "cid": CATEGORY_ID,
            "name": _NAME,
            "description": _DESCRIPTION,
            "examples": json.dumps(_EXAMPLES),
        },
    )
    print(f"category {CATEGORY_ID} {'inserted' if result.rowcount else 'already present'}")
    _bump_revision()


def downgrade() -> None:
    bind = op.get_bind()
    in_use = bind.execute(
        sa.text("SELECT count(*) FROM review_rows WHERE category = :cid"), {"cid": CATEGORY_ID}
    ).scalar()
    if in_use:
        print(f"category {CATEGORY_ID} kept: {in_use} review row(s) still carry it")
        return
    bind.execute(sa.text("DELETE FROM categories WHERE id = :cid"), {"cid": CATEGORY_ID})
    _bump_revision()
```

Then add the tests the existing catalog revisions have:

- a parity test that loads the revision file by path and asserts its text equals the constants
  (pattern: `backend/tests/test_catalog.py` `test_the_job_description_migration_carries_the_constants_text()`);
- an unseeded-fallback test that the constants still offer the category
  (pattern: `backend/tests/test_catalog.py` `test_an_unseeded_catalog_offers_the_job_description_category()`).

CI and a fresh test database start with an empty `categories` table, so they only ever run the
skip branch of a category insert. The parity test is what checks the text the seeded branch writes.

### 6. Apply it locally, reverse it, apply it again

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: all three succeed, and any `print()` lines of your revision appear in the output. Then run
the suite ([How to run the tests](run-the-tests.md)):

```bash
uv run pytest -q
```

### 7. Re-point the parent if `main` moved before you merge

Two open pull requests that both add a revision on the same parent produce two heads once both
merge, and `alembic upgrade head` then fails. Before merging, update your branch from `main` and
run:

```bash
uv run alembic heads
```

If it prints two revisions, change your file's `down_revision` to the revision that landed on `main`
first, then repeat step 6. Record the re-pointing in the file, as `f0f4d21dbb53` and
`a9c4e13f70b2` do. The chain order then differs from the files' `Create Date` order; that is
expected ([Migrations reference](../reference/migrations.md)).

### 8. Apply it on the server

1. Back up the database first ([How to back up and restore](back-up-and-restore.md)).
2. Rebuild and recreate the backend images and run the migration as part of the deploy
   ([How to deploy to the server](deploy-to-the-server.md)). The migration command, from the
   checkout directory on the server:

   ```bash
   docker compose exec -T api alembic upgrade head
   ```

3. Read the output. A catalog revision prints what it applied and what it skipped; a skip on the
   deployed database means an admin had edited that row, and the edit was kept.

CI's e2e job migrates with `docker compose run --rm api alembic upgrade head` before it starts the
`api` service, so application code never runs against an older schema there.

## How to verify it worked

Locally:

```bash
uv run alembic current
```

On the server:

```bash
docker compose exec -T postgres psql -U mrr -d mrr -t -c 'SELECT version_num FROM alembic_version;'
```

Expected in both: your new revision id. When the test suite starts,
`backend/tests/conftest.py` `warn_if_the_schema_is_behind()` prints a `[conftest]` warning naming
the head if the test database is behind it.

## If it fails

**The upgrade stops with an error.** Online migrations run all pending revisions in one transaction
(`backend/alembic/env.py` `run_migrations_online()`), and Postgres DDL is transactional, so a failed
`upgrade head` leaves the database at the revision it started from. Fix the file and run it again.

**Undo a revision on a development or test database:**

```bash
uv run alembic downgrade -1
```

or `uv run alembic downgrade <revision>` to go back to a specific revision. A data revision's
downgrade is not always an exact inverse ([Migrations reference](../reference/migrations.md)).

**Undo on the server:** restore the dump taken before the migration and deploy the recorded commit
([How to back up and restore](back-up-and-restore.md)). Prefer this over `downgrade` for real data.

**A stranded local database.** `alembic_version` stores only a revision id, not the path that led
to it. Two situations follow:

- The database applied a revision before its `down_revision` was re-pointed (for example
  `f0f4d21dbb53` while its parent was still `e7b4c1a92d58`). Alembic now treats the revisions that
  were inserted beneath it (`b6d19f4c30a7`, `a9c4e13f70b2`) as already applied, so `upgrade head`
  succeeds but their columns are missing. Symptom: `alembic current` reports the head while queries
  fail with `UndefinedColumn` for a column one of those revisions adds.
- The database is stamped with a revision that never reached `main`. Alembic cannot find it in
  `versions/` and refuses to upgrade or downgrade.

Recovery for the throwaway test database is to rebuild it (below). For a database holding real data,
restore the backup taken before the migration.

**Rebuild the test database from scratch.** From the repository root:

```bash
docker compose -p mrrtest -f docker-compose.dev.yml down -v
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
```

Then, from `backend/`:

```bash
uv run alembic upgrade head
```

> **Warning:** always pass both `-p mrrtest` and `-f docker-compose.dev.yml` to `down -v`. Without
> them, `docker compose down -v` in the repository root acts on `docker-compose.yml`, the running
> application's stack, and deletes its database and uploads volumes. Use the same `-p` value you
> used to start the test stack.

A parallel test run copies the migrated test database into one database per worker
(`backend/tests/conftest.py` `_clone_worker_databases()`), dropping any copies an earlier run left,
so the worker copies need no separate rebuild.

## Related pages

- [Migrations reference](../reference/migrations.md)
- [Data model reference](../reference/data-model.md)
- [How to add or change a category](add-or-change-a-category.md)
- [How to run the tests](run-the-tests.md)
- [How to deploy to the server](deploy-to-the-server.md)
- [How to back up and restore](back-up-and-restore.md)
- [CI and merge gates](../reference/ci-and-merge-gates.md)
