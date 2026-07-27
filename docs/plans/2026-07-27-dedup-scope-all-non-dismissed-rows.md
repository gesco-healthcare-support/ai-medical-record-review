---
feature: Duplicate detection covers every non-dismissed row, and remembers resolutions
date: 2026-07-27
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Duplicate detection examines every sub-document the reviewer has not dismissed (not just the ones
marked for summarization), and a re-check keeps the clusters the reviewer already resolved or
dismissed instead of resurfacing them.

## Context & decisions

Why now: `dedup_document` only queries `include.is_(True)` (`backend/app/worker/tasks.py:310`), and
PR #41 now default-EXCLUDES General(100) + Depositions(9) - exactly where re-scanned cover letters,
exhibit lists and routing sheets live. Those duplicates are therefore invisible to detection. The
same filter means a keep-one resolution (which sets `include=False` on the copies) leaves a single
in-scope member, so a re-check on a reviewed record legitimately reports 0 groups.

Resolved decisions:
- Decision: scope = rows with `dupe_dismissed = False` (not "all rows") because "not duplicates" is
  a reviewer judgement about content that a re-run cannot improve on; re-examining dismissed rows
  would either resurface a dismissed cluster or require carrying the dismissal forward by member
  set, which is more machinery for no gain.
- Decision: new group numbers start ABOVE the highest `dupe_group` still held by dismissed rows,
  because dismissed rows keep their group while a re-run re-issues numbers from 1
  (`tasks.py:342-349`), and `_dupe_groups` keys purely on that id (`backend/app/api/documents.py:369-373`)
  - colliding ids would render two unrelated clusters as one card.
- Decision: dedup stops resetting `dupe_primary` (it resets only `dupe_group`), because that flag is
  the reviewer's "this is the copy I kept" and `_store_rows` already clears it when the row's page
  range changes (`documents.py:88-102`). Nothing else re-derives it.
- Decision: "needs review" becomes `not dismissed AND >= 2 members still included`, replacing
  "no member is primary" (`documents.py:379-383`), because inclusion is what actually matters - a
  cluster whose copies are already excluded cannot cause a duplicated summary. This makes keep-one
  durable across re-checks by construction, and correctly re-flags a cluster that gains a new copy.
- Decision: the `stale` predicate widens to the same scope (`documents.py:448-452`), because an
  excluded row with no `source_text` now means "dedup never saw this row" and is exactly the
  boundary-changed nudge the banner exists for.

## All needed context

- `dedup_document` (`backend/app/worker/tasks.py:296-352`): scope query at :308-312; per-row reset +
  OCR at :316-331 (`row.source_text` skipped when already present, :321); clustering + confirm at
  :337-350, group only for confirmed sets of >= 2 (:345).
- `cluster_rows` / `confirm_cluster` (`backend/app/services/dedup.py`) are content-based and take
  `{id, title, date, text}` - no change needed.
- `_dupe_groups` (`documents.py:361-373`, drops singletons), `_unreviewed_dupe_count`
  (`documents.py:376-383`), `get_duplicates` (`documents.py:411-457`, emits `include` + `primary`
  per row at :432-433 and `stale` at :448-452).
- `resolve_duplicate` (`documents.py:481-512`): keep_one sets `member.include = is_primary` (:504)
  and clears `dupe_dismissed`; dismiss sets `dupe_dismissed = True` + `dupe_primary = False` (:507).
- FE cluster chip: `resolved = cluster.rows.some((r) => r.primary)`
  (`frontend/components/review/duplicates-view.tsx:127`); per-row "kept"/"excluded" labels at :149.
- Tests to extend: `backend/tests/test_dedup.py`, `backend/tests/test_documents_api.py` (async
  `authed` client + Postgres), `frontend/components/review/duplicates-view.test.tsx`.
- Gotcha: `sqlalchemy.func` is already imported in `documents.py`; `tasks.py` imports `select` only -
  add `func` to that import for the max-group query.

## Tasks (implementation blueprint)

### Task 1 - widen the dedup scope + preserve reviewer flags
- what: MODIFY `backend/app/worker/tasks.py` `dedup_document`: change the row query's second
  predicate from `ReviewRow.include.is_(True)` to `ReviewRow.dupe_dismissed.is_(False)`; drop
  `row.dupe_primary = False` and `row.dupe_dismissed = False` from the per-row reset loop (keep
  `row.dupe_group = None`); before the clustering loop compute
  `group_no = session.scalar(select(func.max(ReviewRow.dupe_group)).where(ReviewRow.document_id == job.document_id)) or 0`
  (run AFTER the reset commit, so only dismissed rows still hold a group) and keep the existing
  `group_no += 1` increment.
- pattern: the existing query/reset/cluster body at `tasks.py:308-350`.
- approach: tdd
- acceptance (EARS):
  - WHEN a document has rows with `include = False` that are not dismissed, THE SYSTEM SHALL OCR and
    cluster those rows.
  - WHEN a row has `dupe_dismissed = True`, THE SYSTEM SHALL leave its `dupe_group`,
    `dupe_primary` and `dupe_dismissed` untouched by a dedup run.
  - WHEN dismissed rows already hold group numbers, THE SYSTEM SHALL assign every new group a number
    greater than the highest existing `dupe_group` for that document.
  - WHEN a row was `dupe_primary` before a dedup run and its page range is unchanged, THE SYSTEM
    SHALL still report it as primary after the run.

### Task 2 - inclusion-based "needs review" count
- what: MODIFY `backend/app/api/documents.py` `_unreviewed_dupe_count`: count a group when
  `not any(m.dupe_dismissed for m in members) and sum(1 for m in members if m.include) >= 2`.
- pattern: the existing comprehension at `documents.py:379-383`.
- approach: tdd
- acceptance (EARS):
  - WHEN a cluster has 2 or more included members and is not dismissed, THE SYSTEM SHALL count it as
    unreviewed.
  - WHEN a cluster has at most one included member, THE SYSTEM SHALL NOT count it, whether or not a
    primary is marked.
  - WHEN a cluster is dismissed, THE SYSTEM SHALL NOT count it.

### Task 3 - stale signal follows the new scope
- what: MODIFY `backend/app/api/documents.py` `get_duplicates`: change the `stale` predicate's row
  filter from `if row.include` to `if not row.dupe_dismissed`.
- pattern: the existing `stale = bool(...)` at `documents.py:448-452`.
- approach: tdd
- acceptance (EARS):
  - WHEN a dedup has completed and a non-dismissed row has no `source_text`, THE SYSTEM SHALL return
    `stale = true`.
  - WHEN every non-dismissed row has `source_text`, THE SYSTEM SHALL return `stale = false`.

### Task 4 - cluster chip reflects inclusion
- what: MODIFY `frontend/components/review/duplicates-view.tsx` `ClusterCard`: replace
  `const resolved = cluster.rows.some((r) => r.primary)` with a count of members whose
  `include !== false`, treating `< 2` as resolved; keep the per-row "kept" / "excluded" labels.
- pattern: the existing chip block at `duplicates-view.tsx:127-141`.
- approach: test-after
- acceptance (EARS):
  - WHEN a cluster has at most one included copy and is not dismissed, THE SYSTEM SHALL show the
    "Resolved" chip.
  - WHEN a cluster has 2 or more included copies, THE SYSTEM SHALL show the "Needs review" chip.

### Task 5 - tests
- what: EXTEND `backend/tests/test_dedup.py` (or add `test_dedup_scope`) for Task 1's four criteria
  with a fake `cluster_rows`/`confirm_cluster`; EXTEND `backend/tests/test_documents_api.py` for the
  count rule (2 included -> 1, 1 included -> 0, dismissed -> 0) and the stale rule; EXTEND
  `frontend/components/review/duplicates-view.test.tsx` for the chip.
- approach: tdd (backend) / test-after (frontend)
- acceptance (EARS): The system shall pass the full backend + frontend suites with new-code coverage
  >= 80%.

## Validation loop

1. BE: `uv run ruff check . && uv run ruff format --check .`
2. BE: `uv run pytest tests/test_dedup.py tests/test_documents_api.py -q` then `uv run pytest -q`
   (the ~5 enqueue/queue-count failures are the known live-RQ-worker drain, not this diff)
3. FE: `pnpm -C frontend typecheck && pnpm -C frontend exec vitest run`
4. Live (local :8080): on a record with excluded General rows, start a dedup -> those rows get
   `source_text` and can form clusters; resolve keep-one -> chip "Resolved"; re-check -> the cluster
   returns with the same primary and does NOT count as unreviewed; dismiss a cluster -> re-check
   leaves it dismissed and gives new clusters higher group numbers.

## Risk / rollback

- Blast radius: `dedup_document` (every dedup run), the duplicates read path, the advisory count
  that drives the header banner, and the cluster chip. No schema change, no migration.
- Cost: dedup now OCRs excluded rows too (local Tesseract, no AI); confirm calls still only run for
  candidate clusters, so AI cost rises only when real new candidates appear.
- Rollback: revert the PR; a dedup re-run rebuilds groups under the old scope.
