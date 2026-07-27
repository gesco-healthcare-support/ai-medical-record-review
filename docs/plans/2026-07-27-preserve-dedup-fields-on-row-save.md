---
feature: Preserve duplicate-clustering across Review edits + stale-check hint
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Editing on Review & correct no longer silently loses duplicate detection: metadata edits keep the
detected groups, a boundary change correctly drops only the changed row's grouping (never a
phantom 1-copy cluster), and when boundaries have changed since the last check the Duplicates tab
shows a "re-check duplicates" hint (a manual re-run, no automatic AI calls).

## Context & decisions

Why now: on Sarhad, a reviewer's category/include edits wiped the two detected duplicate groups on
`record-227pp.pdf`. Root cause CONFIRMED in code + live: the Review-grid autosave
(`PUT /rows` -> `_store_rows`) deletes and recreates every `ReviewRow` from the grid payload, which
omits the dedup-only fields (`source_text`, `dupe_group`, `dupe_primary`, `dupe_dismissed`), so they
reset. A dedup re-run reproduced the two groups (`[(1,2),(2,3)]`), proving dedup itself works.

Resolved decisions (discussed + agreed):
- Decision: snapshot-and-carry the four dedup fields in `_store_rows`, matching rows on
  `(start, end)` - NOT an update-in-place refactor - because those four are the ONLY persisted
  `ReviewRow` fields the payload does not already set (models.py:270-289 vs the constructor), so the
  refactor preserves nothing extra today; nothing depends on `ReviewRow.id` stability (summaries key
  off `(start,end,category)`, `dupe_group` is a plain int, no FK targets `review_rows.id`). Same
  `(start,end)` == same pages == same OCR content the grouping was computed from, so a carry is
  always valid; a changed range drops the grouping (conservative, never a false positive).
- Decision: also carry `source_text`, because a later dedup re-run skips OCR for rows that already
  have it (tasks.py:321) and unchanged pages keep the text valid - and this doubles as the
  stale-check signal (below).
- Decision: hide singleton groups at READ time in `_dupe_groups` (return only groups with >=2
  members), NOT by mutating data in the save path, because a "group of 1" is meaningless and
  `_dupe_groups` is the single choke point feeding both the Duplicates tab and the unreviewed-count;
  one non-mutating guard covers any cause and a dedup re-run self-heals stale data.
- Decision: surface a stale-check HINT (banner + manual re-run button) when boundaries changed since
  the last dedup - NO automatic dedup jobs (Adrian: no auto AI calls). Signal:
  `stale = (latest dedup job is "done") AND (any included row has source_text IS NULL)`. After a
  dedup, every included row has `source_text`; a metadata edit keeps it (Decision 1), a boundary
  change yields a row with none - so a NULL among included rows means "boundaries changed / a row
  was never checked." Read-time, no schema change.

## All needed context

- `_store_rows` (`backend/app/api/documents.py:79-102`): validate -> delete all rows (line 84) ->
  recreate loop (85-100, no dedup fields) -> commit. Callers: `put_rows` (`documents.py:471`,
  autosave) + `summarize_start` (`documents.py:523`). Only post-dedup path that recreates rows.
- `_dupe_groups` (`backend/app/api/documents.py:336-342`): `{dupe_group: [rows]}`, no size filter.
  Consumers: `get_duplicates` (`documents.py:387`) + `_unreviewed_dupe_count` (`documents.py:345`).
- `get_duplicates` (`backend/app/api/documents.py:380-413`): returns `{clusters, job}`; already
  queries the latest dedup job (`dedup_job`, line 408-412) - add `stale` alongside.
- `ReviewRow` dedup columns: `source_text`, `dupe_group`, `dupe_primary`, `dupe_dismissed`
  (`backend/app/models.py:286-289`). `(start, end)` unique per document (rows tile).
- Dedup writes a group only for confirmed sets of >=2 (`backend/app/worker/tasks.py:345`); OCRs +
  persists `source_text` for every included row (`tasks.py:316-331`), skipping rows that already
  have it (`tasks.py:321`).
- Frontend: `DuplicatesResponse` type (`frontend/lib/types.ts:128-131`) - add `stale: boolean`;
  `getDuplicates` (`frontend/lib/review-api.ts:27`); `useStartDedup` already exists
  (`frontend/hooks/use-duplicates.ts:35`, POST `/dedup/start`); `useDuplicates` already polls while a
  job runs (`use-duplicates.ts:17-20`). `DuplicatesView` (`frontend/components/review/duplicates-view.tsx`)
  renders the header + clusters; it has NO re-run control today - add the hint banner + button there.
- Tests: `backend/tests/test_documents_api.py` (async `authed` client + Postgres);
  `frontend/components/review/duplicates-view.test.tsx` (mocks `use-duplicates`).

## Tasks (implementation blueprint)

### Task 1 - preserve dedup fields in _store_rows
- what: MODIFY `backend/app/api/documents.py` `_store_rows` - after `validate_rows`, BEFORE the
  delete, build `preserved = {(r.start, r.end): (r.source_text, r.dupe_group, r.dupe_primary,
  r.dupe_dismissed) for r in document.review_rows}`; in the recreate loop pass those four (from
  `preserved.get((int(row["start"]), int(row["end"])))`, defaulting `None/None/False/False`).
- pattern: existing `ReviewRow(...)` at documents.py:86-99.
- approach: tdd
- acceptance (EARS):
  - WHEN a saved row keeps its `(start,end)` and only category/include/title/date change, THE
    SYSTEM SHALL retain its `source_text`, `dupe_group`, `dupe_primary`, `dupe_dismissed`.
  - WHEN a saved row's `(start,end)` matches no pre-save row, THE SYSTEM SHALL leave its dedup
    fields at defaults.

### Task 2 - hide singleton groups in _dupe_groups
- what: MODIFY `backend/app/api/documents.py` `_dupe_groups` - return only groups with >=2 members.
- pattern: existing body at documents.py:336-342.
- approach: tdd
- acceptance (EARS):
  - WHEN a `dupe_group` has one member, THE SYSTEM SHALL exclude it from `get_duplicates` clusters
    and the unreviewed count; WHEN it has >=2, THE SYSTEM SHALL include it.

### Task 3 - stale signal in get_duplicates
- what: MODIFY `backend/app/api/documents.py` `get_duplicates` - compute
  `stale = bool(dedup_job and dedup_job.state == "done" and any(r.source_text is None for r in
  document.review_rows if r.include))` and add `"stale": stale` to the returned dict.
- pattern: the existing `dedup_job` query + return at documents.py:408-413.
- approach: tdd
- acceptance (EARS):
  - WHEN a dedup has completed and every included row still has saved text, THE SYSTEM SHALL return
    `stale=false`.
  - WHEN a dedup has completed and an included row has no saved text (boundary change / newly
    included), THE SYSTEM SHALL return `stale=true`.
  - WHILE a dedup job is queued or running, THE SYSTEM SHALL return `stale=false`.

### Task 4 - Duplicates-tab re-check hint (frontend)
- what: MODIFY `frontend/lib/types.ts` `DuplicatesResponse` (add `stale: boolean`); MODIFY
  `frontend/components/review/duplicates-view.tsx` to show a hint banner + "Re-check duplicates"
  button (wired to `useStartDedup`) WHEN `data.stale` is true and no dedup job is running.
- pattern: existing header/running logic + `useResolveDuplicate` usage in duplicates-view.tsx;
  `useStartDedup` in use-duplicates.ts:35.
- approach: test-after (UI)
- acceptance (EARS):
  - WHEN the duplicates data is `stale` and no job is running, THE SYSTEM SHALL show a
    "boundaries changed - re-check duplicates" hint with a button that triggers a manual dedup.
  - WHEN not stale (or a job is running), THE SYSTEM SHALL NOT show the hint.

### Task 5 - tests
- what: EXTEND `backend/tests/test_documents_api.py`: dedup fields survive a category/include save
  on unchanged `(start,end)`; reset on a changed boundary; a 1-member group is absent from
  `get_duplicates` while a 2-member group is present; `stale` true/false per Task 3. EXTEND
  `frontend/components/review/duplicates-view.test.tsx`: hint shows when `stale` + idle, hidden
  otherwise, and its button calls the start-dedup mutation.
- approach: tdd (backend) / test-after (frontend)
- acceptance (EARS): The system shall pass the full backend + frontend suites with new-code
  coverage >= 80%.

## Validation loop

1. BE: `uv run ruff check . && uv run ruff format --check .`
2. BE: `uv run pytest tests/test_documents_api.py -q` then `uv run pytest -q` (the ~5
   enqueue/queue-count failures are the known live-RQ-worker drain, not this diff)
3. FE: `pnpm -C frontend typecheck && pnpm -C frontend exec vitest run` (full suite)
4. Live (local :8080): seed a row's dedup fields; save a category/include change -> group persists;
   change a boundary -> that row drops out, no 1-copy cluster, `get_duplicates` returns `stale=true`
   and the Duplicates tab shows the re-check hint; click it -> dedup re-runs and the hint clears.

## Risk / rollback

- Blast radius: `_store_rows` (every Review autosave + summarize-start), `_dupe_groups` +
  `get_duplicates` (duplicates read), and the Duplicates view. No schema change, no migration.
- The hint runs no AI on its own; the re-check button uses the existing manual `/dedup/start`.
- Rollback: revert the PR. No data migration; a dedup re-run rebuilds groups regardless.
