# Migrations reference

Every Alembic revision in the repository, in chain order, with what it changes and which ones
carry catalog data.

Source of truth: `backend/alembic/versions/` (one file per revision, named
`<revision>_<slug>.py`), `backend/alembic/env.py` and `backend/alembic.ini`.

**Current head: `b7e4c1a9d203`.** The chain has 32 revisions, one head and no branches. How to add
a revision is in [How to create a database migration](../how-to/create-a-database-migration.md).

## Chain

Chain order is the order `alembic upgrade head` applies them, following each file's
`down_revision`. The date is the file's `Create Date` line. Kind: **schema** changes tables,
columns or indexes; **data** changes rows only; **schema + data** does both.

| # | Revision | Parent (`down_revision`) | Date | Kind | Purpose |
| ---: | --- | --- | --- | --- | --- |
| 1 | `73abdcd5ef01` | (none - base) | 2026-07-14 | schema | Baseline: creates `catalog_meta`, `categories`, `prompts`, `role`, `user`, `audit_log`, `documents`, `roles_users`, `jobs`, `review_rows`, `segment_rows`, `summaries` and their indexes. |
| 2 | `a0725d467a48` | `73abdcd5ef01` | 2026-07-15 | schema | Creates `access_token`; adds `user.is_verified` (NOT NULL, filled by a temporary server default that is then dropped). |
| 3 | `009991f2eda1` | `a0725d467a48` | 2026-07-15 | schema | Creates the partial unique index `uq_one_active_job_per_document` for states `queued`, `running`. |
| 4 | `b1f4a7c9d2e3` | `009991f2eda1` | 2026-07-21 | schema | Adds `documents.patient_first_name`, `patient_last_name`, `patient_dob`, `law_firm`. |
| 5 | `c2d5e8f1a3b7` | `b1f4a7c9d2e3` | 2026-07-21 | schema | Adds `jobs.rq_job_id`, `attempts` (server default `0`), `attention`; rebuilds `uq_one_active_job_per_document` to include `paused`. |
| 6 | `e3a9c7b21d84` | `c2d5e8f1a3b7` | 2026-07-24 | schema | Adds `summaries.verified` (server default `false`), `verified_text`, `verify_issues`. |
| 7 | `f1b8d3c60a29` | `e3a9c7b21d84` | 2026-07-24 | schema | Adds `review_rows.source_text`, `dupe_group` (indexed `ix_review_rows_dupe_group`), `dupe_primary`, `dupe_dismissed` (server default `false`). |
| 8 | `a7c3f2e9b1d4` | `f1b8d3c60a29` | 2026-07-26 | schema + data | Adds `categories.summarize_default` (server default `true`); sets it `false` for categories 9 and 100; sets `review_rows.include = false` for rows in categories 9 and 100. |
| 9 | `d4e7a1c93f26` | `a7c3f2e9b1d4` | 2026-07-27 | data | Rewrites category 100's description and examples, only while both still hold the seeded text. |
| 10 | `f1a83b5c60d2` | `d4e7a1c93f26` | 2026-07-28 | data | Deletes each `summary` prompt row whose text hashes to the frozen sha256 of the seeded prompt, so code prompts apply; keeps any other row. |
| 11 | `b7c25e40a913` | `f1a83b5c60d2` | 2026-07-28 | schema | Adds `review_rows.dupe_similarity`. |
| 12 | `a4f2c9e81b53` | `b7c25e40a913` | 2026-07-29 | schema | Adds `summaries.verified_title`. |
| 13 | `c8b1d4e70f92` | `a4f2c9e81b53` | 2026-07-30 | data | Rewrites descriptions of categories 1, 3, 5, 12, 14 and the name of 14 (each guarded on the expected text); removes and adds example titles element-wise in 1, 3, 5, 12, 14. |
| 14 | `d8c2f5b71e43` | `c8b1d4e70f92` | 2026-07-31 | schema | Adds `audit_log.detail`. |
| 15 | `e7b4c1a92d58` | `d8c2f5b71e43` | 2026-07-31 | schema | Adds `jobs.cancel_requested` (server default `false`). |
| 16 | `b6d19f4c30a7` | `e7b4c1a92d58` | 2026-08-06 | schema | Adds `jobs.title_model`, `audit_model`, `prompt_fingerprint` and `summaries.model`, `title_model`, `audit_model`, `prompt_fingerprint`, `audit_fingerprint`. All nullable, no backfill. |
| 17 | `a9c4e13f70b2` | `b6d19f4c30a7` | 2026-08-06 | data | Sets `summarize_default = true` for category 9 where it is `false`. |
| 18 | `f0f4d21dbb53` | `a9c4e13f70b2` | 2026-08-04 | schema | Creates `page_texts` with `ix_page_texts_document_id` and `uq_page_texts_document_page`. |
| 19 | `c5d81f6a3b70` | `f0f4d21dbb53` | 2026-08-11 | schema | Adds `jobs.build_sha`. |
| 20 | `b3f7c02e91a4` | `c5d81f6a3b70` | 2026-08-21 | data | Inserts category 15 (utilization review and independent medical review), only on a seeded catalog. |
| 21 | `c4a7e2b91f60` | `b3f7c02e91a4` | 2026-08-21 | schema | Adds `summaries.unreadable` (server default `false`). |
| 22 | `d5b8c31a740e` | `c4a7e2b91f60` | 2026-08-26 | schema | Adds `summaries.embedded_review` (server default `false`). |
| 23 | `a1e6f4d20c93` | `d5b8c31a740e` | 2026-08-26 | schema | Adds `summaries.updated_at` (nullable, no server default, no backfill). |
| 24 | `e4c8a1f70b93` | `a1e6f4d20c93` | 2026-08-27 | data | Renames category 4 and rewrites its description (each guarded on the expected text); adds one example title element-wise. |
| 25 | `b9d3e5f81c47` | `e4c8a1f70b93` | 2026-08-28 | schema | Adds `segment_rows.method` and `review_rows.method`. |
| 26 | `d7c1a9e34b28` | `b9d3e5f81c47` | 2026-09-08 | data | Inserts category 16 (hospital discharge summary), only on a seeded catalog. |
| 27 | `b3e9f0c47a15` | `d7c1a9e34b28` | 2026-09-11 | schema | Adds `jobs.backend` and `summaries.backend`. |
| 28 | `c2f1a7d94e63` | `b3e9f0c47a15` | 2026-09-14 | schema | Adds `documents.attorney_name`, `doctor`, `letter_type`, `letter_date`, `pages_received`. |
| 29 | `e4b7a2c91d05` | `c2f1a7d94e63` | 2026-09-24 | data | Inserts category 17 (job description), only on a seeded catalog. |
| 30 | `f5c8d2a19e47` | `e4b7a2c91d05` | 2026-09-29 | schema | Adds `jobs.requested_by` (nullable, no backfill). |
| 31 | `a3d6f0b81e57` | `f5c8d2a19e47` | 2026-10-02 | schema | Creates `replaced_review_rows`: the reviewer rows a re-segment replaces, kept per replacing job. No backfill. |
| 32 | `b7e4c1a9d203` | `a3d6f0b81e57` | 2026-10-02 | data | Inserts category 18 (illegible document, `auto_assign` false, so only a reviewer assigns it), only on a seeded catalog, `ON CONFLICT DO NOTHING`; downgrade keeps the row while any review row uses it. **Current head.** |

Totals: 22 schema, 9 data, 1 schema + data.

## Revisions out of date order

Two revisions had their `down_revision` changed when their pull requests merged, so chain order
differs from `Create Date` order. The reason is recorded in each file.

| Revision | Original parent | Current parent | Recorded reason |
| --- | --- | --- | --- |
| `a9c4e13f70b2` | `e7b4c1a92d58` | `b6d19f4c30a7` | `b6d19f4c30a7` merged first and became the head. |
| `f0f4d21dbb53` | `e7b4c1a92d58` | `a9c4e13f70b2` | `b6d19f4c30a7` and `a9c4e13f70b2` landed while it waited; two heads make `upgrade head` fail. |

## Catalog and data migrations

Nine revisions write catalog rows (`categories`, `prompts`, `catalog_meta`) into databases that
already have them. On an EMPTY `categories` table the app serves the catalog from
`backend/app/services/taxonomy.py`, so these revisions are written to leave such a table empty.

| Revision | Tables written | Guard | Bumps `catalog_meta.revision` | Downgrade | Parity test with the constants |
| --- | --- | --- | --- | --- | --- |
| `a7c3f2e9b1d4` | `categories`, `review_rows` | none (by id) | no | drops the column only; the data updates are not reversed | none |
| `d4e7a1c93f26` | `categories`, `catalog_meta` | description AND examples still hold the old text | yes (UPSERT) | swaps the text back, guarded on the new text | none |
| `f1a83b5c60d2` | `prompts`, `catalog_meta` | row text sha256 equals the frozen seeded hash | yes (UPSERT) | re-inserts a row from the CURRENT code prompt for each id in its hash map that has no row | none |
| `c8b1d4e70f92` | `categories`, `catalog_meta` | description / name still hold the expected text; examples edited element-wise | yes (UPSERT) | partial inverse: keeps titles an admin added and category 5's two occupational-therapy titles | `backend/tests/test_catalog.py` `test_the_catalog_migration_carries_the_same_text_as_the_constants()` |
| `a9c4e13f70b2` | `categories` | `id = '9' AND summarize_default = false` | no | sets category 9 back to `false` where it is `true` | none |
| `b3f7c02e91a4` | `categories`, `catalog_meta` | table has a row other than 15; `ON CONFLICT (id) DO NOTHING` | yes (UPSERT), also when it skips | deletes category 15 unless a `review_rows` row uses it | `backend/tests/test_classification.py` `test_the_utilization_review_migration_carries_the_same_text_as_the_constants()` |
| `e4c8a1f70b93` | `categories`, `catalog_meta` | name / description still hold the expected text; example added element-wise | yes (UPSERT) | reverse rewrite, guarded; removes the added example | `backend/tests/test_catalog.py` `test_the_category_four_migration_carries_the_same_text_as_the_constants()` |
| `d7c1a9e34b28` | `categories`, `catalog_meta` | table has a row other than 16; `ON CONFLICT (id) DO NOTHING` | yes (UPSERT), also when it skips | deletes category 16 unless a `review_rows` row uses it | none |
| `e4b7a2c91d05` | `categories`, `catalog_meta` | table has a row other than 17; `ON CONFLICT (id) DO NOTHING` | yes (UPSERT), also when it skips | deletes category 17 unless a `review_rows` row uses it | `backend/tests/test_catalog.py` `test_the_job_description_migration_carries_the_constants_text()` |

None of the category-inserting revisions writes a `prompts` row; each new category resolves to its
`category_NN` prompt in `backend/app/services/prompts.py`.

Messages these revisions print during `alembic upgrade` (stdout):

| Revision | Prints |
| --- | --- |
| `f1a83b5c60d2` | `prompt shadows dropped: [...]; customized rows kept: [...]` |
| `c8b1d4e70f92` | `category 14 name left as edited` (only when skipped); `category descriptions rewritten: [...]; left as edited: [...]` |
| `b3f7c02e91a4`, `d7c1a9e34b28`, `e4b7a2c91d05` | `categories table is unseeded - category NN NOT inserted...`, or `category NN inserted (...)`, or `category NN already present - left exactly as it is` |
| `e4c8a1f70b93` | `category 4: name <outcome>, description <outcome>`, where each outcome is `rewritten` or `left as it was` |

## Dependencies outside Alembic

| Revision | Imports | Effect |
| --- | --- | --- |
| `a0725d467a48` | `fastapi_users_db_sqlalchemy.generics` (`TIMESTAMPAware`) | The package must be installed to load the chain. |
| `f1a83b5c60d2` | `app.services.seed_catalog.code_summary_prompt` | Alembic imports every revision file when it builds the chain, so this function must keep existing under that name. |

## Postgres-only SQL

The migrations run against Postgres only. These revisions use SQL that SQLite does not accept:

| Revision | Postgres-specific SQL |
| --- | --- |
| `009991f2eda1`, `c2d5e8f1a3b7` | partial index (`postgresql_where`) |
| `d4e7a1c93f26` | `::json` casts, `ON CONFLICT ... DO UPDATE` |
| `f1a83b5c60d2` | `ON CONFLICT ... DO UPDATE`, `NOW()` |
| `c8b1d4e70f92` | `jsonb` element removal and concatenation operators, `jsonb_build_array`, `ON CONFLICT` |
| `e4c8a1f70b93` | `jsonb` containment and concatenation operators, `to_jsonb`, `jsonb_agg`, `jsonb_array_elements`, `ON CONFLICT` |
| `b3f7c02e91a4`, `d7c1a9e34b28`, `e4b7a2c91d05` | `ON CONFLICT`, `now()` |

SQLite test schemas are built from `Base.metadata.create_all()` instead
(`backend/tests/test_catalog.py` `session` fixture), so no migration runs on SQLite.

## Alembic configuration

| Setting | Value | Where |
| --- | --- | --- |
| `script_location` | `%(here)s/alembic` | `backend/alembic.ini` |
| `prepend_sys_path` | `.` (so `app` imports when run from `backend/`) | `backend/alembic.ini` |
| `sqlalchemy.url` | placeholder `driver://user:pass@localhost/dbname`, replaced at run time | `backend/alembic.ini` |
| Database URL used | `Settings.database_url` (`DATABASE_URL`) | `backend/alembic/env.py` |
| `target_metadata` | `app.db.Base.metadata`, after `from app import models` registers every table | `backend/alembic/env.py` |
| Online run | `NullPool` engine, one transaction around all pending revisions | `backend/alembic/env.py` `run_migrations_online()` |
| Autogenerate compare options | none set, so Alembic defaults apply (types compared; server defaults not compared) | `backend/alembic/env.py` |
| Post-write hooks | none enabled | `backend/alembic.ini` |
| Loggers | root and `sqlalchemy.engine` at WARNING, `alembic` at INFO, to stderr | `backend/alembic.ini` |
| New-file template | `backend/alembic/script.py.mako` | - |

Because `env.py` builds `Settings`, every Alembic command that loads it needs `DATABASE_URL`,
`SECRET_KEY` and `SECURITY_PASSWORD_SALT` set in the environment or in `.env` in the working
directory ([Configuration reference](configuration.md)).

## Where migrations run

| Context | Command | Source |
| --- | --- | --- |
| CI backend job (empty Postgres service) | `uv run alembic upgrade head`, then the test suite | `.github/workflows/ci.yml` job `backend` |
| CI e2e job (compose stack, empty volume) | `docker compose run --rm api alembic upgrade head`, before `api` starts | `.github/workflows/ci.yml` job `e2e` |
| Local test database | see [How to run the tests](../how-to/run-the-tests.md) | - |
| Compose stack / server | `docker compose exec -T api alembic upgrade head` | [How to deploy to the server](../how-to/deploy-to-the-server.md) |

Both CI jobs start from an empty database, so every category-inserting revision takes its
"unseeded" branch there.

## Related pages

- [Data model reference](data-model.md)
- [How to create a database migration](../how-to/create-a-database-migration.md)
- [How to add or change a category](../how-to/add-or-change-a-category.md)
- [How to back up and restore](../how-to/back-up-and-restore.md)

<!-- reviewed: 2026-09-30 -->
