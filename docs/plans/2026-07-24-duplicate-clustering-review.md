---
feature: Pre-summarization duplicate-document clustering + Duplicates review tab
date: 2026-07-24
status: in-progress
base-branch: main
related-issues: []
---

## Goal
After a record is segmented and categorized, the app automatically clusters sub-documents it
believes are the same document (exact dupes and near-dupes, including copies with different first
pages), and a new "Duplicates" tab shows each cluster sorted by date so the reviewer can keep one
copy, exclude the rest, or dismiss the cluster - before summarizing.

## Context & decisions
Why now: real records contain the same document scanned several times; summarizing every copy is
wasteful and confusing (problem #1). Content-similarity was validated on ARROYO (memory
`mrr-ai-summary-quality-eval`): at Jaccard >= 0.7 it clustered 112 of 154 sub-docs, and char-difflib
separated true re-scans from recurring-form series. Ships as PR 2 of 2 (after the verify pass).

Resolved decisions:
- Decision: detection = algorithmic (word-set Jaccard + char-difflib) proposes candidate clusters,
  then ONE cheap LLM confirm call per candidate cluster, because the math is accurate/free/instant
  and the LLM only adjudicates the ambiguous "is this really the same document?" edge.
- Decision: it runs as a PRECOMPUTE step and PERSISTS a cluster id per row, because recomputing on
  every tab open would re-OCR the whole record each time.
- Decision: OCR full pages of each sub-doc once and persist as `ReviewRow.source_text`, because some
  dupes differ only on the first page - first-page-only OCR would miss them; persisting also lets the
  Duplicates view and the AI-confirm call reuse it (and it is available to summarize later).
- Decision: UX = a new "Duplicates" tab in the workbench (Review & correct | Duplicates | Summaries),
  because Adrian asked for "a page that shows the duplicate documents" grouped and sorted by date.
- Decision: reviewer actions per cluster = keep-one (mark primary) + exclude-the-rest (set
  include=false) + dismiss ("not duplicates"); NO row merging, because page-range merge is complex
  and risky and keep-one already achieves the goal.
- Decision: advisory, not a hard gate - a notice/badge shows when clusters are unreviewed but
  Summarize is never blocked, because detection is imperfect and the reviewer stays in control.
- Decision: a new `dedup` job kind runs automatically after segment OR classify completes;
  document.status stays "reviewing" (dedup maps to reviewing on enqueue and done). Known constraint:
  while dedup runs it holds the one-active-job slot, so a Summarize attempt during dedup returns 409
  ("checking for duplicates, wait") - acceptable since dedup is part of the pre-summarize review.
- Decision: the AI-confirm call uses `classify_model` (gemini-2.5-flash-lite, cheapest) at temp 0.0,
  text-only (each member's title + date + first ~1500 chars of `source_text`), because a yes/no
  same-document adjudication does not need the full summary model or page images.

## All needed context
- Pipeline entry points: `backend/app/worker/tasks.py` `segment_document` (:181) and
  `classify_document` (:223) both end by committing ReviewRows; chain a `dedup` enqueue after each
  once the job is done. `_run` (:40) is the shared runner; add a new `dedup_document(job_id)` task.
- Job service: `backend/app/services/jobs.py` `STATUS_ON_ENQUEUE`/`STATUS_ON_DONE` (:21-22) - add
  `"dedup": "reviewing"` to both; `enqueue` (:75); `create_job` (:43); `ACTIVE_STATES` (:25).
- Queues: `backend/app/worker/queues.py` `queue_for`/`worker_fn` - register the `dedup` kind + its
  worker fn (confirm mapping shape during build).
- Data model: `backend/app/models.py` `ReviewRow` (:267) - add `source_text` (Text, nullable),
  `dupe_group` (Integer, nullable, indexed), `dupe_primary` (Boolean, default False),
  `dupe_dismissed` (Boolean, default False); extend `as_row()` (:283) to include them.
- OCR: `backend/app/services/ocr.py` `extract_text_from_selected_pages` (:64) is the exact per-page
  path to OCR each row's pages (memory-lean, ~1.5s/page).
- Clustering logic to port: `backend/scripts/eval/dedup_reocr.py` (word-set Jaccard union-find +
  char-difflib range). The AI-confirm shape can mirror the structured call in
  `backend/app/services/verify_pass.py` (:114-119).
- API: `backend/app/api/documents.py` - `get_document` (:241) returns rows; `put_rows`/`_store_rows`
  (:345,:78) + `validate_rows` (rows.py) is the row-write pattern; `summarize_start` (:383) is where
  an advisory "unreviewed duplicates" hint can be surfaced (do NOT block). Add new routes:
  `GET /{id}/duplicates`, `POST /{id}/dedup/start`, `POST /{id}/duplicates/{group}/resolve`.
- Frontend: `frontend/components/review/review-page-client.tsx` (SegmentedTabs :54-60,:101; add a
  "duplicates" tab + body); new `frontend/components/review/duplicates-view.tsx` (cluster cards,
  date-sorted, keep-one/exclude/dismiss); `hooks/use-review-workflow` + a new `hooks/use-duplicates`;
  `rows-table.tsx`/`pdf-viewer.tsx` for page thumbnails/links to mirror.
- Gotchas: dedup OCR of a large record is minutes (~1.5s/page) - report progress via the job like
  summarize does (`report("deduping", i, n)`). Full-doc OCR must be memory-lean (per-page, gc) - the
  container is memory-constrained (OOM risk). All confirm-call inputs are PHI -> Vertex/BAA only,
  never logged. Cluster ids are per-document ints (1..N), null = singleton/not-clustered.

## Tasks (implementation blueprint)
1. MODIFY `backend/app/models.py` `ReviewRow` - add `source_text`, `dupe_group` (indexed),
   `dupe_primary`, `dupe_dismissed`; extend `as_row()`.
   - pattern: `ReviewRow` columns + `as_row` (:267,:283); `Summary.source_text` (:300).
   - approach: code.
   - acceptance (EARS): WHEN a ReviewRow is serialized via `as_row()`, THE SYSTEM SHALL include
     `dupe_group`, `dupe_primary`, `dupe_dismissed`.

2. CREATE alembic migration - add the four `review_rows` columns (additive; nullable / defaults).
   - pattern: an existing additive migration in `backend/alembic/versions/`.
   - approach: code.
   - acceptance (EARS): WHEN `alembic upgrade head` runs, THE SYSTEM SHALL add the columns with no
     data loss.

3. CREATE `backend/app/services/dedup.py` - `cluster_rows(rows_with_text, jaccard_threshold=0.7)`
   (port the Jaccard union-find + char-difflib from dedup_reocr.py; pure, no I/O) returning candidate
   clusters; and `confirm_cluster(model, members)` -> the subset that is truly the same document
   (one classify_model call, temp 0.0, text-only title+date+excerpt).
   - pattern: `backend/scripts/eval/dedup_reocr.py`; structured call verify_pass.py:114-119.
   - approach: tdd (pure clustering logic; synthetic text fixtures).
   - acceptance (EARS): WHEN given rows whose text word-set Jaccard >= threshold, THE SYSTEM SHALL
     place them in the same candidate cluster; WHEN two rows share only boilerplate (low difflib),
     THE SYSTEM SHALL expose that so the confirm step can split them.

4. CREATE `backend/app/worker/tasks.py` `dedup_document(job_id)` - via `_run`: OCR every included
   ReviewRow's pages with `extract_text_from_selected_pages`, persist `source_text` per row (skip
   rows that already have it), report progress; run `cluster_rows` then `confirm_cluster` per
   candidate; assign `dupe_group` to confirmed members (clear stale groups first); leave
   `dupe_primary`/`dupe_dismissed` for the reviewer.
   - pattern: `summarize_document` per-row loop + `report()` (:253,:308); `classify_document` OCR
     (:241).
   - approach: test-after.
   - acceptance (EARS): WHEN the dedup job completes, THE SYSTEM SHALL have stored `source_text` for
     every included row and a shared `dupe_group` for each confirmed set of duplicate rows.

5. MODIFY `backend/app/worker/tasks.py` `segment_document` (:181) and `classify_document` (:223) -
   after `_run` returns done, enqueue a `dedup` job for the document (fresh session; guard
   `JobConflict`).
   - pattern: `enqueue` (jobs.py:75).
   - approach: test-after.
   - acceptance (EARS): WHEN a segment or classify job finishes, THE SYSTEM SHALL enqueue a dedup job
     for that document.

6. MODIFY `backend/app/services/jobs.py` - add `"dedup": "reviewing"` to `STATUS_ON_ENQUEUE` and
   `STATUS_ON_DONE`; MODIFY `backend/app/worker/queues.py` to register the `dedup` kind + worker fn.
   - pattern: existing kind mappings (jobs.py:21-22; queues.py).
   - approach: code.
   - acceptance (EARS): WHEN a dedup job is enqueued, THE SYSTEM SHALL route it to a worker and keep
     document.status "reviewing".

7. CREATE routes in `backend/app/api/documents.py` - `GET /{id}/duplicates` (clusters: for each
   `dupe_group`, its rows sorted by date, with similarity + `dupe_primary`/`dupe_dismissed`, plus the
   latest dedup job progress); `POST /{id}/dedup/start` (manual recompute -> enqueue dedup, 409 if a
   job is active); `POST /{id}/duplicates/{group}/resolve` (body: action `keep_one` with primary idx
   -> set that row `dupe_primary`, others `include=false`; or `dismiss` -> set `dupe_dismissed` on
   all members). Add an advisory `has_unreviewed_duplicates` field to `summarize_start`'s response or
   `get_status` (do NOT block).
   - pattern: `get_document` (:241), `put_rows`/`_store_rows` (:345,:78), `get_owned_document` guard.
   - approach: test-after.
   - acceptance (EARS): WHEN the reviewer resolves a cluster with keep_one, THE SYSTEM SHALL set the
     chosen row `dupe_primary=true` and set `include=false` on the other members. WHEN dismissed, THE
     SYSTEM SHALL set `dupe_dismissed=true` on the cluster and stop surfacing it as unreviewed.

8. CREATE `frontend/components/review/duplicates-view.tsx` + `frontend/hooks/use-duplicates.ts`;
   MODIFY `review-page-client.tsx` to add a "Duplicates" tab (badge with unresolved-cluster count)
   between Review and Summaries, and an advisory banner when unreviewed clusters exist.
   BUILD NOTE (deviation): also added `reloadRows()` to `useReviewWorkflow` and wired it to the tab's
   `onResolved`. Without it, resolving a cluster sets `include=false` server-side but the editor's
   stale local rows would flush `include=true` on the next Summarize and resurrect the excluded copy.
   - pattern: SegmentedTabs + tab body (review-page-client.tsx:54-60,101,193); summaries-view.tsx
     card + action patterns; pdf-viewer.tsx for page links.
   - approach: test-after.
   - acceptance (EARS): WHILE a document has confirmed duplicate clusters, THE SYSTEM SHALL show a
     Duplicates tab listing each cluster's copies sorted by date with keep-one / exclude / dismiss
     controls; WHEN a cluster is unreviewed, THE SYSTEM SHALL show an advisory (non-blocking) notice.

9. Tests: `backend/tests/test_dedup.py` (cluster_rows on synthetic text; resolve-route behavior);
   dedup_document wiring (mock OCR + confirm); a frontend test for the Duplicates tab + resolve
   actions. Satisfy SonarCloud new-code coverage.
   - approach: tdd/test-after per task.

## Validation loop
- Backend lint/format: `docker compose exec -T api sh -c 'cd /app && uv run ruff check . && uv run ruff format --check .'`
- Backend tests: `docker compose exec -T api sh -c 'cd /app && uv run pytest -q'`
- Migration: `docker compose exec -T api sh -c 'cd /app && uv run alembic upgrade head'`
- Frontend: `cd frontend && pnpm typecheck && pnpm test`
- Manual (synthetic multi-copy record): upload -> identify; confirm a dedup job runs, the Duplicates
  tab shows the cluster sorted by date, keep-one excludes the others, dismiss clears the notice, and
  Summarize is never blocked.

## Risk / rollback
- Blast radius: adds a pipeline stage + a tab; does not change segmentation/summarization output. The
  dedup job holds the one-active-job slot while running (Summarize waits with 409 during it).
  Full-doc OCR adds minutes on large records (backgrounded, progress-reported).
- Rollback: revert the commit; migration additive (downgrade drops the four columns). If dedup
  misbehaves, the advisory design means summaries are unaffected - the reviewer ignores the tab.
