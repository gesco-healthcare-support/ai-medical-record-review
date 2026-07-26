---
feature: Default-unselect General and Depositions for summarization
date: 2026-07-26
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Documents categorized General (id `100`) or Depositions (id `9`) are unchecked for
summarization by default on both the Review & Correct page and the Duplicates page, driven by a
new admin-editable per-category `summarize_default` flag, with existing rows backfilled.

## Context & decisions

Why now: reviewers must currently uncheck every General/Depositions sub-document by hand before
summarizing; these categories are rarely summarized.

Root: `ReviewRow.include` defaults `True` (models.py:281) and is set at three creation sites;
both the review grid (`review-editor.tsx:88`) and the Duplicates view (`duplicates-view.tsx:119`)
already render `include`, so a server-side default requires no display change - only a
re-checkable default.

Resolved Open Decisions:
- Decision: implement via a new per-category DB flag `summarize_default` on the `Category`
  catalog (not a hardcoded `{9,100}` set), because it is admin-editable and fits the existing
  editable catalog (mirrors `auto_assign`), so business can change the defaults without a deploy.
- Decision: `summarize_default` is INDEPENDENT of `auto_assign` (which gates classifier
  assignment, catalog.py:20-33); reusing `auto_assign` would wrongly stop 9/100 being assigned.
- Decision: backfill existing rows - the migration sets `include=false` on existing
  `review_rows` in categories 9/100, accepting that it overrides any prior include choice on
  those rows (none were reviewed under this feature).

## All needed context

- `Category` DB model: `backend/app/models.py:361-380` (columns + `listing()`); mirror
  `auto_assign` (:369 column, :379 serializer) for the new `summarize_default`.
- Seed/fallback: `backend/app/services/seed_catalog.py` - `constants_categories()` (:31-46) and
  `_ID_SIX` (:16-23) build category dicts; `seed_catalog()` (:54) is idempotent (skips a seeded
  DB), so existing DBs need the migration's data step, not a re-seed.
- Catalog accessor: `backend/app/services/catalog.py:20-46` (`get_categories`,
  `get_category_options`) - DB-first with constants fallback; add a `summarize_default_for`
  helper in the same style.
- Row-creation sites (set `include`):
  - `backend/app/worker/tasks.py:206` - `segment_document` builds `ReviewRow(**fields)`;
    `fields` is SHARED with `SegmentRow` (no `include` column), so pass `include` separately.
  - `backend/app/worker/tasks.py:249` - `classify_document` sets `row.category`; add
    `row.include` here (runs during identify, before review).
  - `backend/app/api/documents.py:190-204` - aggregate route hardcodes
    `category="100", include=True`.
  - LEAVE `backend/app/api/documents.py:98` (`replace_review_rows`) unchanged - it honors the
    reviewer's explicit `include` on autosave.
- Admin surface to mirror `auto_assign`:
  - `backend/app/schemas/admin.py:13` `CategoryCreate` (add `summarize_default: bool = True`),
    `:22` `CategoryUpdate` (add `summarize_default: bool | None = None`).
  - `backend/app/api/admin.py:74` create + `:103-104` PATCH (`if "summarize_default" in body:`).
  - `frontend/lib/admin-api.ts:10,27` (Category + payload types); `category-dialog.tsx:38,49,62,141`
    (checkbox state + submit); `admin-view.tsx:157` (table column).
- Migration head: `f1b8d3c60a29` (`uv run alembic heads`). Pattern:
  `backend/alembic/versions/e3a9c7b21d84_summary_verify_fields.py` (additive column w/ default).
- Category ids: `taxonomy.py:175` (`9` Depositions), `:218` (`100` General), `DEFAULT_ID=100`.

## Tasks (implementation blueprint)

### Task 1 - model column + serializer
- what: MODIFY `backend/app/models.py` `Category` - add
  `summarize_default = Column(Boolean, nullable=False, default=True)`; add
  `"summarize_default": self.summarize_default` to `listing()`.
- pattern: `auto_assign` at models.py:369 / :379.
- approach: code
- acceptance (EARS): The system shall expose `summarize_default` on every category record
  returned by `Category.listing()`.

### Task 2 - migration (schema + data backfill)
- what: CREATE `backend/alembic/versions/<rev>_category_summarize_default.py`,
  `down_revision="f1b8d3c60a29"`. Upgrade: add `categories.summarize_default` BOOLEAN NOT NULL
  server_default true; `UPDATE categories SET summarize_default=false WHERE id IN ('9','100')`;
  `UPDATE review_rows SET include=false WHERE category IN ('9','100')`. Downgrade: drop column
  (leave data updates).
- pattern: `e3a9c7b21d84_summary_verify_fields.py`.
- approach: code
- acceptance (EARS):
  - WHEN the migration runs on an existing DB, THE SYSTEM SHALL set `summarize_default=false`
    for categories 9 and 100 and `true` for all others.
  - WHEN the migration runs, THE SYSTEM SHALL set `include=false` on all existing `review_rows`
    in categories 9 and 100.

### Task 3 - seed / constants
- what: MODIFY `backend/app/services/seed_catalog.py` - set
  `summarize_default=category.id not in {"9", "100"}` in `constants_categories()` and
  `summarize_default: True` in `_ID_SIX`.
- pattern: the `auto_assign` key in the same dicts.
- approach: code
- acceptance (EARS): WHEN a fresh DB is seeded, THE SYSTEM SHALL store `summarize_default=false`
  for categories 9 and 100 and `true` for every other category.

### Task 4 - catalog helper
- what: MODIFY `backend/app/services/catalog.py` - add
  `summarize_default_for(session, category_id) -> bool` (DB-first via `Category`, constants
  fallback), returning `True` for an unknown id.
- pattern: `get_prompt` (catalog.py:49) DB-first + constants fallback.
- approach: tdd
- acceptance (EARS):
  - WHEN asked for category 9 or 100, THE SYSTEM SHALL return `False`.
  - WHEN asked for any other known category, THE SYSTEM SHALL return `True`.
  - WHEN the categories table is unseeded, THE SYSTEM SHALL fall back to the constants and still
    return `False` for 9/100.

### Task 5 - apply the default at row creation
- what: MODIFY `backend/app/worker/tasks.py` - `segment_document` (:206) pass
  `include=catalog.summarize_default_for(session, row["category"])` to `ReviewRow(...)`;
  `classify_document` (:249) set `row.include = catalog.summarize_default_for(session,
  result.category)`. MODIFY `backend/app/api/documents.py` aggregate route (:202)
  `include=catalog.summarize_default_for(session, "100")`.
- pattern: existing `catalog` usage in `tasks.py:20` import + `documents.py` catalog calls.
- approach: test-after
- acceptance (EARS):
  - WHEN a document is segmented/classified and a row is category 9 or 100, THE SYSTEM SHALL
    create/leave that row with `include=false`; other categories `include=true`.

### Task 6 - admin surface (mirror auto_assign)
- what: MODIFY `backend/app/schemas/admin.py` (add `summarize_default` to `CategoryCreate` and
  `CategoryUpdate`); `backend/app/api/admin.py` (create :74 + PATCH :103 block);
  `frontend/lib/admin-api.ts` types; `frontend/components/admin/category-dialog.tsx` (checkbox);
  `frontend/components/admin/admin-view.tsx` (table column).
- pattern: `auto_assign` at every one of those anchors.
- approach: test-after
- acceptance (EARS): WHEN an admin sets a category's summarize-default toggle, THE SYSTEM SHALL
  persist it and return it from the categories endpoint.

### Task 7 - tests
- what: EXTEND `backend/tests/test_catalog.py` (helper), `backend/tests/test_jobs.py` +
  `backend/tests/test_documents_api.py` (row-creation include defaults for 9/100 vs others),
  admin PATCH test. EXTEND `frontend/components/admin/admin-view.test.tsx` +
  `category-dialog` test (new toggle); add a review-flow assertion that a 9/100 row renders
  unchecked.
- approach: test-after
- acceptance (EARS): The system shall pass the full backend + frontend suites with new-code
  coverage >= 80%.

## Validation loop

1. BE: `uv run ruff check . && uv run ruff format --check .`
2. BE migration: `uv run alembic upgrade head` then `uv run alembic downgrade -1` then
   `uv run alembic upgrade head` (reversible add).
3. BE: `uv run pytest -q`
4. FE: `pnpm -C frontend typecheck && pnpm -C frontend exec vitest run` (full suite)
5. Live: segment a document; confirm a Depositions/General row is unchecked in Review & Correct
   and shows "excluded" on Duplicates; flip the admin toggle and confirm it persists.

## Risk / rollback

- Blast radius: the include default on new rows + a one-time UPDATE of existing 9/100 rows +
  one additive Category column. The UPDATE overrides any existing include choice on 9/100 rows.
- Migration must run on every Category-bearing DB (host + each tenant/office DB if applicable).
- Rollback: `alembic downgrade -1` drops the column; the `review_rows`/`categories` data updates
  are not auto-reverted (note in the PR). Revert the PR for code.
