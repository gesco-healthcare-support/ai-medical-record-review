---
feature: Stop a running job, then continue it or start it over
date: 2026-07-31
status: in-progress
base-branch: main
related-issues: []
---

> Supersedes the research-only `docs/plans/2026-07-31-stop-and-restart-jobs.md`, whose baseline was
> `a40c85c`. That file's findings still read correctly except where corrected below; this is the
> executable version, re-verified against `main` `bc8ae1f`.

# Goal

A reviewer can stop any running job and then either continue it from where it stopped or start it
over, with segmentation resuming from its completed windows rather than recomputing them.

# Context & decisions

## Why now

"General 1" from the request list. Nothing in the API can stop a job today. The jobs users most want
to kill are the ones wedged in Vertex backoff - segmentation has died three times at 2/3 windows,
each burning 10-18 minutes and producing nothing - so a stop that only checks at a progress tick
would take up to ~17 minutes to respond and read as broken.

Lands after the worker/queue plan (`VERTEX_MAX_RPM=20`, `SEGMENT_WINDOW_WORKERS=1`, both shipped),
which is fortunate: those settings change the concurrency of the very pool being checkpointed.

## Corrections to the research plan (verified at `bc8ae1f`)

| It said | Actually |
| --- | --- |
| segmentation has **two** checkpointable phases | **three**: `segmenting`, `categorizing`, `verifying` (`segment_engine.py:206`, gated on `settings.verify_merge`) |
| `categorizing` spends one Vertex call per row | *Up to* one. `classify()` is rules -> local embeddings -> Gemini (`classification.py:294`), so a rule match spends nothing |
| Stop goes in `progress-panel.tsx` | That component renders only when `rows.length === 0 && watching` (`review-page-client.tsx:300`) - a first segment run. Stop there is invisible for summarize |
| classify commits "per row" | Throttled: `report()` skips the commit inside `_PROGRESS_MIN_INTERVAL` = 1.0s (`tasks.py:60`), so a resume can redo ~1s of rows |
| `documents.py:251` / `:603-608` | starts are at `:558` (dedup), `:657` (segment), `:677` (summarize) |

Two of its risks resolve without work: the delete-then-rewrite hazard is **real** (`tasks.py:197`),
and the chained dedup is **already safe** - `_chain_dedup` returns unless `state == "done"`
(`tasks.py:284`).

## Resolved decisions

**D1: `plan_signature` includes `model`, `prompt_version` and `catalog_revision`,** because the
research plan's four inputs (sha256, page count, `window_overlap`, `window_budget_mb`) omit
everything about *how* a window was interpreted. Phase 1 calls `settings.genai_model`
(`segment_engine.py:64`) and phase 2's `classify()` reads the catalog, so reusing a checkpoint across
a model change or a catalog edit would merge output from two different classifiers - the exact
corruption the signature exists to prevent. All three already live on `Job` (`models.py:25-27`).
Rejected: waiting for the prompt-provenance plan's resolved-prompt fingerprint. It is strictly better
(it catches a prompt *edit*, not just a version bump) but it would reverse the agreed order.

**D2: phase 3 (`verifying`) is checkpointed, but only reused when phase 2 completed,** and its
signature includes a hash of the phase-2 output plus `verify_suspect_cap`. Because
`suspect_indices(rows)` reads `row["category"]` and `row["date"]` and then truncates at the cap
(`verify_pass.py:129-144`), *which* boundaries get verified is a function of the entire phase-2
result. Treating the three phases as peers could verify a suspect set that never coherently existed.
Rejected: not checkpointing phase 3 (throws away up to `verify_suspect_cap` calls on every resume),
and keying verdicts on page range alone (applies a verdict computed under different categories).

**D3: the cancel flag is a module-level current-job id set in `_run`, plus a Redis key read inside
`generate_with_retry`'s backoff.** No signature changes at any of the 8 `generate_with_retry` call
sites. It is correct because `RoundRobinWorker` inherits `Worker.execute_job`, which **forks a
work-horse per job** (verified in the running image), so a module global is per-job by construction
and readable from every `ThreadPoolExecutor` thread - which a `contextvar` would not be, since
executors do not copy context into workers. Rejected: threading a callable through 8 modules (large
diff for a stop button), and checking only in `report()` (leaves the ~17-minute case unfixed, i.e.
the case that motivates the feature).

**D4: a Continue that would delete edited summaries warns first.** The summarize resume identity is
`(row_start, row_end, row_category)` (`tasks.py:494`), so a row re-classified since its summary was
written no longer matches `wanted` and the summary is `session.delete`d and regenerated - discarding
reviewer edits, while the docstring promises they survive. Pre-existing (Review & correct always
wrote `row.category`) but PR #68 made it a routine path. Rejected: silence, and dropping
`row_category` from the identity (a category change would then *not* force a regeneration, which
contradicts #68's badge).

**D5: Stop lives in the header's inline `rce-progress` block** (`review-page-client.tsx:161-168`),
gated on `wf.watching` alone so it appears for all four kinds; the post-cancel Continue / Start over
pair goes in the existing banner area (`review-page-client.tsx:245-296`), because the progress bar
unmounts once the job is terminal and cannot host the choice.

# All needed context

## Anchors (backend, `backend/app/`)

- `_run` + `report()` + the signal handlers - `worker/tasks.py:40-97`; `report` commits at `:65`
- `_finalize_paused` (the shape `_finalize_cancelled` mirrors) - `worker/tasks.py:107`
- `_finalize_needs_attention` - `worker/tasks.py:152`
- `segment_document`, incl. the delete-then-rewrite - `worker/tasks.py:190`, delete at `:197`
- `classify_document` per-row loop - `worker/tasks.py:241-269`
- `_chain_dedup` `state != "done"` guard - `worker/tasks.py:284`
- `dedup_document` - `worker/tasks.py:302`
- `summarize_document` + resume identity - `worker/tasks.py:468`, `wanted` at `:494`, stale delete at `:505`
- `JobPaused` / `JobNeedsAttention` - `worker/failures.py:46`, `:60`
- `recover_orphans`, correlating by current `rq_job_id` - `worker/recovery.py:24`
- `ACTIVE_STATES`, `STATUS_ON_ENQUEUE`, `STATUS_ON_DONE` - `services/jobs.py:23-37`
- `Job` columns (`state` is `String(16)`) - `models.py:17-37`
- `get_redis`, `queue_for`, `worker_fn`, `lane_name` - `worker/queues.py:51-90`
- `generate_with_retry`, backoff sleep at `:114` - `services/genai_retry.py:89`
- `run_segmentation`, three phases - `services/segment_engine.py:130`; windows `:147`, categorize `:186`, verify `:206`
- `byte_budgeted_windows(pdf_path, total_pages, window_overlap, window_budget_bytes)` - called at `segment_engine.py:147`
- `drain_pool` / `PoolTimeout(unfinished)` - `services/pools.py:24`, `:15`
- `suspect_indices` / `verify_and_merge` - `services/verify_pass.py:129`, `:147`
- Settings: `window_overlap` (`config.py:151`), `window_budget_mb` (`:150`), `verify_merge` (`:152`), `verify_suspect_cap` (`:154`), `genai_model` (`:36`), `classify_model` (`:41`), `segment_window_workers` (`:133`), `classify_workers` (`:78`)

## Anchors (frontend)

- Header inline progress (Stop's home) - `components/review/review-page-client.tsx:161-168`
- Banner area (the Continue / Start over home) - `:245-296`
- `ProgressPanel`, first-run only - `components/review/progress-panel.tsx`, rendered at `:300-305`
- `pollJob` + `PollResult` - `hooks/use-review-workflow.ts:99-140`
- `reloadRows` - `hooks/use-review-workflow.ts:310`

## Gotchas

1. **`pollJob` would hang on a cancelled job.** It handles `done`, `needs_attention`, `error`,
   `interrupted`; anything else falls through to "keep polling" (`use-review-workflow.ts:135`). A new
   `cancelled` state must be a `PollResult` outcome or the bar spins forever. Not in the research plan.
2. **Never check cancel between the delete and the insert.** In `segment_document`, `report()` is
   passed into `run_segmentation` as `progress`, so the cooperative check fires only during compute -
   which is correct and must stay that way. The writes after `run_segmentation` returns call no
   `report()`, so no check can fire there. Do not add one.
3. **`_finalize_cancelled` must not `session.rollback()`.** Mirror `_finalize_paused`, which does not:
   a rollback would discard committed-adjacent work. Partial output stays visible (decision 4 of the
   research plan).
4. **A cancelled paused summarize has a scheduled RQ job.** After a pause, `job.rq_job_id` points at
   the *scheduled resume* (`tasks.py:123`), so cancel must cancel that, or the run reappears.
5. **New setting needs three edits, not one.** `job_cancel_grace_seconds` must be added to
   `config.py`, named in `docker-compose.yml`'s `x-backend-env`, AND added to the key tuple in
   `tests/test_pool_wiring.py:72`. The compose allowlist has silently swallowed settings twice today
   (#64/#65, then #66/#67); that test now exists to catch it.
6. **Migration chain.** Head is `d8c2f5b71e43`. Chain: `d8c2f5b71e43 -> e7b4c1a92d58 ->
   f2d9a6c31e07 -> a1c8e4b72f39`. Ids checked against all 14 existing revisions, no collisions.
   Mirror `alembic/versions/d8c2f5b71e43_audit_log_detail.py` for the additive-column ones.
7. `cancelled` needs no migration: `Job.state` is `String(16)` and `cancelled` is 9 characters.
8. `recover_orphans` only rewrites `document.status` when it is in `("segmenting", "summarizing")`
   (`recovery.py:53`), which is why cancel needs its own `STATUS_ON_CANCEL` rather than reusing that.

# Tasks

## PR 1 - the stop button and the two restarts that already exist

### T1 - CREATE `backend/alembic/versions/e7b4c1a92d58_job_cancel_requested.py`

**what:** `op.add_column("jobs", sa.Column("cancel_requested", sa.Boolean(), nullable=False,
server_default=sa.false()))`. `revision = "e7b4c1a92d58"`, `down_revision = "d8c2f5b71e43"`.
`server_default` because the table is populated.

**pattern:** `alembic/versions/d8c2f5b71e43_audit_log_detail.py`.

**approach:** code

**acceptance (EARS):**
- WHEN `alembic upgrade head` runs on a populated database, THE SYSTEM SHALL add
  `jobs.cancel_requested` defaulting to false for every existing row, and report head `e7b4c1a92d58`.
- WHEN `alembic downgrade -1` runs, THE SYSTEM SHALL drop the column without error.

### T2 - MODIFY `backend/app/models.py`, `backend/app/services/jobs.py`

**what:** `cancel_requested = Column(Boolean, nullable=False, server_default="false", default=False)`
on `Job`. In `jobs.py` add `STATUS_ON_CANCEL = {"segment": "uploaded", "classify": "reviewing",
"summarize": "reviewing", "dedup": "reviewing"}` beside the existing maps, with a comment stating why
segment differs: a cancelled first segment run leaves no rows, so `reviewing` would render an empty
editor. `ACTIVE_STATES` is unchanged - a cancelled job is not active, which frees the document.

**pattern:** `services/jobs.py:23-37`; the column style at `models.py:33` (`attempts`, same
`server_default` + `default` pairing).

**approach:** tdd

**acceptance (EARS):**
- WHEN a job row is created without specifying it, THE SYSTEM SHALL default `cancel_requested` to
  false.
- WHERE a job's state is `cancelled`, THE SYSTEM SHALL NOT report it from `active_job()`, so a
  restart can be enqueued immediately.

### T3 - CREATE `backend/app/worker/cancel.py`

**what:** The cancel channel, deliberately in its own module so both the API and the retry loop can
use it without importing worker internals.
- `_CURRENT_JOB_ID: int | None` module global, `set_current_job(job_id)` / `clear_current_job()`.
- `cancel_key(job_id) -> str` returning `f"mrr:cancel:{job_id}"`.
- `request_cancel(job_id)` - `SETEX` the key with `job_cancel_grace_seconds * 60` TTL so it
  self-cleans and can never wedge a future job that reuses the id.
- `is_cancel_requested(job_id) -> bool` - a `GET`; returns **False** on `RedisError` (a Redis outage
  must not phantom-cancel every running job).
- `current_job_cancelled() -> bool` - False when no current job, else `is_cancel_requested`.
- `clear_cancel(job_id)`.

**pattern:** `worker/queues.py:51` for `get_redis()` and its error handling; `worker/recovery.py:44`
for the "degrade on RedisError rather than act" stance.

**approach:** tdd

**acceptance (EARS):**
- WHEN `request_cancel(7)` is called and then `is_cancel_requested(7)`, THE SYSTEM SHALL return true.
- WHEN no cancel has been requested, THE SYSTEM SHALL return false.
- IF Redis raises, THEN `is_cancel_requested` SHALL return false and log a warning, never raise.
- WHEN no current job is set, `current_job_cancelled()` SHALL return false without touching Redis.

### T4 - MODIFY `backend/app/worker/failures.py`, `backend/app/worker/tasks.py`

**what:**
- `class JobCancelled(Exception)` in `failures.py`, carrying `done: int` and `total: int`, mirroring
  `JobPaused`'s shape.
- In `_run`: `set_current_job(job_id)` right after marking running, `clear_current_job()` in a
  `finally`. `report()` raises `JobCancelled(current, total)` when `current_job_cancelled()`, checked
  **before** the throttle return so a cancel is never swallowed by rate limiting.
- `_finalize_cancelled(session, job_id, sig)`: state `cancelled`, `stage` `cancelled`, `current`/
  `total` from the signal, `finished_at`, `document.status = STATUS_ON_CANCEL[job.kind]`,
  `clear_cancel(job_id)`. **No rollback.** If `job.rq_job_id` names a *scheduled* RQ job, cancel it
  via `rq.job.Job.fetch(...).cancel()` inside a try/except so a missing job is not an error.
- `except JobCancelled as sig:` between the `JobNeedsAttention` and generic handlers.

**pattern:** `_finalize_paused` (`tasks.py:107-147`) for structure, lane awareness and the
try/except-around-RQ discipline.

**approach:** tdd

**acceptance (EARS):**
- WHEN a cancel is requested while a job is running, THE SYSTEM SHALL raise `JobCancelled` at the
  next `report()` even if that tick would otherwise be throttled.
- WHEN `JobCancelled` is raised, THE SYSTEM SHALL set state `cancelled`, a coherent
  `document.status`, and clear the Redis key.
- WHEN a paused summarize with a scheduled resume is cancelled, THE SYSTEM SHALL cancel the
  scheduled RQ job so the run does not reappear.
- WHILE a segment job is cancelled during compute, THE SYSTEM SHALL leave the document's existing
  `ReviewRow`s **unchanged** - never zero rows.

### T5 - MODIFY `backend/app/services/genai_retry.py`, `backend/app/config.py`, `docker-compose.yml`, `backend/tests/test_pool_wiring.py`

**what:**
- In `generate_with_retry`, replace the bare `time.sleep(_sleep_for(...))` at `:114` with a loop that
  sleeps in <= 1s slices and raises `JobCancelled(0, 0)` as soon as `current_job_cancelled()` is
  true. This is the change that turns "stuck 17 minutes" into "stops within a second".
- `job_cancel_grace_seconds: int = 10` in `config.py`.
- Name it in `docker-compose.yml` `x-backend-env` as
  `JOB_CANCEL_GRACE_SECONDS: ${JOB_CANCEL_GRACE_SECONDS:-10}`, and add the key to the tuple in
  `tests/test_pool_wiring.py:72`.

**pattern:** the `DUPE_*` block in `docker-compose.yml` for the comment-plus-fallback style; the
existing retry loop at `genai_retry.py:95-114`.

**approach:** tdd

**acceptance (EARS):**
- WHILE a job is cancelled and its model call is in backoff, THE SYSTEM SHALL abandon the sleep
  within one second rather than serving the full delay.
- WHEN no cancel is requested, THE SYSTEM SHALL sleep the same total duration as before (no change to
  retry timing).
- WHEN `docker-compose.yml` stops naming `JOB_CANCEL_GRACE_SECONDS`, THE SYSTEM SHALL fail
  `test_compose_passes_through_the_settings_it_claims_to_control`.

### T6 - MODIFY `backend/app/api/documents.py`, `backend/app/schemas/documents.py`

**what:**
- `POST /{document_id}/jobs/{job_id}/cancel`, body `CancelPayload{force: bool = False}`. 404 when the
  job is not on this document. **200 no-op** when the job is already terminal (not 409: a job can
  finish between the click and the request, and that is not an error). Sets
  `job.cancel_requested = True`, calls `request_cancel(job_id)`, and on `force` also
  `send_stop_job_command(get_redis(), job.rq_job_id or str(job.id))` inside a try/except. Returns
  `job.progress()`.
- `fresh: bool = False` on new `SegmentStartPayload` and `DedupStartPayload`; segment clears
  checkpoints (PR 2), dedup clears `source_text` for its rows.
- `audit(session, "job.cancel", user.id, document.id, detail=f"job {job_id} kind {job.kind} force {force}")`,
  using the `detail` column added in #68.

**pattern:** the 404/409 shape at `documents.py:713-716`; `_apply_row_category`'s audit call for the
`detail` idiom; `send_stop_job_command` verified present on rq 2.10.0.

**approach:** tdd

**acceptance (EARS):**
- WHEN cancel is posted for a running job on this document, THE SYSTEM SHALL set `cancel_requested`,
  write the Redis key, and return the job.
- IF the job belongs to another document, THEN THE SYSTEM SHALL return 404.
- IF the job is already `done`, `error`, `cancelled` or `interrupted`, THEN THE SYSTEM SHALL return
  200 and change nothing.
- WHEN `force: true` is posted, THE SYSTEM SHALL additionally issue `send_stop_job_command`, and
  SHALL still return 200 if that command fails.
- WHEN a cancel succeeds, THE SYSTEM SHALL record one `audit_log` row with action `job.cancel`.

### T7 - MODIFY `frontend/lib/types.ts`, `frontend/lib/review-api.ts`, `frontend/hooks/use-review-workflow.ts`

**what:**
- `JobState` (or the equivalent union) gains `"cancelled"`; `cancelJob(documentId, jobId, force)` in
  `review-api.ts`.
- `pollJob`: `if (job.state === "cancelled") return resolve({ outcome: "cancelled" })`, and
  `PollResult` gains that variant. **Without this the progress bar polls forever** - `cancelled`
  currently falls through to the keep-polling branch at `:135`.
- Expose `cancelActiveJob(force?: boolean)` and the settled outcome so the page can offer the restart
  pair.

**pattern:** the existing `needs_attention` outcome plumbing (`use-review-workflow.ts:126-131`).

**approach:** tdd

**acceptance (EARS):**
- WHEN a polled job reports `cancelled`, THE SYSTEM SHALL settle the poll with a `cancelled` outcome
  and stop the timer, never continue polling.

### T8 - MODIFY `frontend/components/review/review-page-client.tsx`, `frontend/app/evaluators-ds.css`

**what:**
- A **Stop** button inside the header's `rce-progress` block, shown while `wf.watching`. First press
  posts `{force: false}` and relabels to "Force stop" after `job_cancel_grace_seconds`; second press
  posts `{force: true}`.
- After a `cancelled` outcome, a banner offering **Continue** and **Start over**, using the existing
  banner markup. Continue calls the kind's start endpoint with `fresh: false`; Start over with
  `fresh: true`.
- Before a Continue on summarize, count the summaries it would delete (an `edited` summary whose
  `row.category` differs from the row's live category) and `window.confirm` when non-zero.

**pattern:** the inline progress markup at `review-page-client.tsx:161-168`; the banner blocks at
`:245-296`; the confirm idiom in `summaries-view.tsx:130-137`.

**approach:** test-after

**acceptance (EARS):**
- WHILE any job is active, THE SYSTEM SHALL show a Stop control in the header for all four kinds.
- WHEN Stop is pressed once, THE SYSTEM SHALL request a cooperative cancel and NOT force-stop.
- WHEN the grace period has elapsed without the job settling, THE SYSTEM SHALL offer Force stop.
- WHEN a job settles as cancelled, THE SYSTEM SHALL offer Continue and Start over, and SHALL NOT
  leave a spinning progress bar.
- IF a Continue on summarize would delete summaries carrying reviewer edits, THEN THE SYSTEM SHALL
  warn with the count before starting.

## PR 2 - real Continue for classify and segment

### T9 - CREATE `backend/alembic/versions/f2d9a6c31e07_review_row_classified_at.py`; MODIFY `models.py`, `worker/tasks.py`

**what:** Nullable `classified_at` (`sa.DateTime`) on `review_rows`; `down_revision =
"e7b4c1a92d58"`. `classify_document` sets `row.classified_at = _utcnow()` as each row is classified,
and on a continue (`fresh: false`) skips rows that already have one. `fresh: true` nulls them all
first.

**pattern:** `classify_document`'s loop at `tasks.py:256-269`.

**approach:** tdd

**acceptance (EARS):**
- WHEN classify runs, THE SYSTEM SHALL stamp `classified_at` on each row it classifies.
- WHEN a cancelled classify is continued, THE SYSTEM SHALL skip rows that already carry
  `classified_at` and classify only the rest.
- WHEN classify is started with `fresh: true`, THE SYSTEM SHALL clear every `classified_at` and
  re-classify every row.

### T10 - CREATE `backend/alembic/versions/a1c8e4b72f39_segment_checkpoint.py`; CREATE `backend/app/services/segment_checkpoint.py`

**what:** Table `segment_checkpoint(id, document_id FK, plan_signature String(64), phase String(16),
unit_key String(64), payload JSON, created_at)` with a unique index on
`(document_id, plan_signature, phase, unit_key)`. `down_revision = "f2d9a6c31e07"`. Keyed on the
**document**, not the job, because a restart creates a new job that must inherit the previous one's
completed windows.

New service module with:
- `plan_signature(document, settings) -> str` - sha256 over `document.sha256`, `page_count`,
  `window_overlap`, `window_budget_mb`, `settings.genai_model`, `PROMPT_VERSION`, and the job's
  `catalog_revision` (D1).
- `phase2_signature(rows, settings) -> str` - sha256 over the ordered
  `(start, end, category, date)` tuples plus `verify_suspect_cap`, used **only** for phase 3 (D2).
- `load(session, document_id, signature, phase) -> dict[str, Any]`,
  `save(session, document_id, signature, phase, unit_key, payload)`,
  `clear(session, document_id)`.

**pattern:** `services/dedup.py` for a pure-ish service module with its own session-taking helpers;
the migration style of `alembic/versions/b7c25e40a913_review_row_dupe_similarity.py` for a table plus
index.

**approach:** tdd

**acceptance (EARS):**
- WHEN `plan_signature` is computed twice for an unchanged document and settings, THE SYSTEM SHALL
  return the same value.
- WHEN `window_overlap`, `window_budget_mb`, `genai_model`, `PROMPT_VERSION` or `catalog_revision`
  changes, THE SYSTEM SHALL return a different signature.
- WHEN two units are saved for the same `(document, signature, phase, unit_key)`, THE SYSTEM SHALL
  keep one row, not raise.
- WHEN `clear` is called, THE SYSTEM SHALL remove every checkpoint for that document.

### T11 - MODIFY `backend/app/services/segment_engine.py`

**what:** Thread an optional `checkpoint` handle into `run_segmentation` (default `None` keeps every
existing caller and test working).
- Phase 1: load completed windows by index; submit only the missing ones; `save` each window's rows
  inside the `drain_pool` loop where `done` is already incremented.
- Phase 2: same, keyed on `f"{start}-{end}"`, saving the resolved category and flag.
- Phase 3: compute `phase2_signature` **after** phase 2 finishes; load and save verdicts under that
  signature keyed on the boundary row's `start`. Skip the checkpoint entirely if phase 2 did not
  complete (D2).
- On `PoolTimeout` in phase 1, the completed windows are already persisted, so raise
  `PipelineTimeoutError` as today but leave the checkpoints - the existing terminal failure becomes
  recoverable.

**pattern:** the three existing `drain_pool` loops at `:158`, `:190`, and `verify_pass.py:172`.

**approach:** tdd

**acceptance (EARS):**
- WHEN a segment run is continued after a cancel, THE SYSTEM SHALL re-run only the windows with no
  checkpoint, and SHALL NOT recompute completed ones.
- IF `window_overlap` or `window_budget_mb` changed since the checkpoint was written, THEN THE SYSTEM
  SHALL discard it and recompute from scratch rather than merging two window layouts.
- IF the segmentation model or the catalog revision changed, THEN THE SYSTEM SHALL likewise discard it.
- WHEN phase 2 did not complete, THE SYSTEM SHALL NOT reuse phase-3 verdicts.
- WHEN a segment job completes successfully, THE SYSTEM SHALL delete that document's checkpoints.
- WHEN segment is started with `fresh: true`, THE SYSTEM SHALL delete the checkpoints before running.

### T12 - MODIFY `frontend/components/review/review-page-client.tsx`

**what:** Continue is now offered for all four kinds. Remove any "not available for this kind"
affordance introduced in PR 1.

**pattern:** the banner from T8.

**approach:** test-after

**acceptance (EARS):**
- WHEN a cancelled segment job is continued, THE SYSTEM SHALL start a segment job that resumes from
  its checkpoints rather than offering only Start over.

# Validation loop

Backend, after each task:

```bash
cd backend && uv run ruff format --check app tests alembic && uv run ruff check app tests alembic && uv run pytest -q
```

Migrations, all three, forwards and back:

```bash
cd backend && uv run alembic upgrade head && uv run alembic downgrade -3 && uv run alembic upgrade head
```

Frontend:

```bash
cd frontend && pnpm typecheck && pnpm test
```

Live, on the local stack (synthetic fixtures only, verified via Playwright MCP - never the in-app
browser):

```bash
docker compose build api web segment-worker summarize-worker && docker compose up -d --no-deps api web segment-worker summarize-worker
```

1. Start a segment job on a multi-window synthetic PDF, press Stop mid-run, and confirm the existing
   rows are untouched and the bar settles rather than spinning.
2. Continue it and confirm from the worker log that only the unfinished windows are recomputed.
3. Cancel a summarize with edits present and confirm the warning names the right count.

# Risk / rollback

**Blast radius.** `_run` gains a check per `report()` and a global set/clear; `generate_with_retry`
gains a sliced sleep. Both are on every job path, so a bug here affects all four kinds - which is why
T3-T5 are `tdd` and why `is_cancel_requested` fails **closed** (returns false) on a Redis error.
`run_segmentation`'s checkpoint handle defaults to `None`, so every existing caller and test is
unaffected.

**The dangerous one.** A cancel check reachable between `DELETE FROM review_rows` and the re-insert
in `segment_document` would commit a document with zero rows and destroy the reviewer's page ranges.
The check fires only via `report()`, which is passed into `run_segmentation` and therefore only during
compute; the write phase calls no `report()`. T4's fourth acceptance asserts the rows survive.

**Behaviour change to review.** `PoolTimeout` in phase 1 stays terminal for the current run, but its
completed windows now persist, so the reviewer can continue instead of restarting from zero. That is
the intended improvement, and it means a timed-out segment job now leaves rows in
`segment_checkpoint` that a later `fresh: true` must clear.

**Rollback.** Revert the merge, then `alembic downgrade -3`. The checkpoint table is new and read only
by the new code path; `cancel_requested` and `classified_at` are additive and nullable/defaulted.
Redis keys expire on their own TTL, so nothing is left behind.

# Live verification of PR 1 (2026-08-03) - two defects found, both fixed

The automated loop was green before any of this; both defects were found only by driving the real
stack, and neither was visible to the unit tests as written.

## D3 - the escalation timer outlived its run (frontend)

**Symptom.** After one stop-then-continue cycle on the same page, the button read "Force stop"
immediately, with no grace period - so the reviewer's FIRST press on the next job was a hard kill.

**Cause.** `onStop` scheduled `setForceReady(true)` with a bare `setTimeout` and kept no handle. When
a run stopped INSIDE the grace period, the reset effect (`!wf.watching`) cleared `forceReady`, and
then the stale timer fired and set it true again with nothing running.

**Fix.** Hold the timer in a ref; clear it in the reset effect, on unmount, and before scheduling a
new one. Pinned by `review-page-client.test.tsx` - "does not carry a pending escalation into the next
run", which fails with `expected 'Force stop' to be 'Stop'` against the old code.

## D4 - a force-stopped job was orphaned, wedging the document

**Symptom.** Force stop genuinely stopped the compute (RQ reported `JobStatus.STOPPED`), but the Job
row sat at `running` for 30+ minutes. The bar spins forever and the one-active-job index refuses every
new run on that document.

**Cause.** The work-horse is the process that writes the terminal state, and force stop kills it. The
only reconciler was `recover_orphans`, called ONLY from the API startup lifespan (`main.py:30`) - so
the document stayed wedged until someone restarted the API. `documents.py:709`'s claim that "orphan
recovery reaps whatever is left" was wrong in practice. This was never Force-stop-specific: an
OOM-killed work-horse wedges a document the same way, which is a pre-existing bug this closes.

**Fix - the parent worker finalizes what its dead fork could not.** Verified against installed rq
2.10.0: `on_stopped` runs in the PARENT worker via `monitor_work_horse`
(`rq/worker/worker_classes.py:135`), and `on_failure` runs both in-horse for ordinary exceptions
(`rq/worker/base.py:1585`) and from `StartedJobRegistry.cleanup` with `AbandonedJobError`
(`rq/registry.py:283`) for a horse that died without reporting.

- NEW `app/worker/finalizers.py` - `on_job_stopped` -> `cancelled` + `STATUS_ON_CANCEL`;
  `on_job_failed` -> `interrupted`, document only when mid-pipeline (mirrors `recover_orphans`).
- NEW `jobs.mark_terminal` - the single idempotent writer, a conditional UPDATE on `ACTIVE_STATES`
  so the first writer wins. Required, not defensive: RQ runs the stopped callback AND THEN
  `handle_job_failure` for one stop, abandoned cleanup can race boot recovery, and at the concurrency
  this is heading for (3 users x 2+ documents) parent workers finalize in parallel.
- `_finalize_cancelled` now routes through `mark_terminal` too, so a cooperative stop and a forced
  one write the SAME terminal state and cannot drift.
- Registered at BOTH dispatch sites (`jobs.enqueue` and the resume `enqueue_in` in `tasks.py`) - a
  resumed summarize is the longest-running job here and so the likeliest to be force-stopped.
- Correlation is `rq_job.args[0]`, NOT `rq_job.id`: a resumed summarize runs under a fresh RQ id.

**Known limit.** The abandoned path is eventual - it fires only once the job outlives `job_timeout`,
which is size-aware and can be long. The deliberate stop is immediate. Boot `recover_orphans` remains
the backstop for the case where the whole worker container dies.

## D5 - the fork shared its parent's DB connection, silently defeating D4

Found by live-testing D4 rather than by reading code: the first live force stop still left job 1194 at
`running`, exactly as before the D4 fix.

**Symptom.** `POST .../cancel {"force": true}` returned 200, the compute stopped, and the Job row never
moved. The worker log showed the callback firing and then failing:

```
ERROR app.worker.finalizers stopped callback could not finalize job 1194
  File "/app/app/worker/finalizers.py", line 67, in on_job_stopped
    job = session.get(Job, job_id)
psycopg.ProgrammingError: can't change 'autocommit' now: connection in transaction status INTRANS
```

So the RQ wiring from D4 was correct - the callback ran in the parent, on time. It could not reach the
database.

**Cause.** `get_engine` is an `@lru_cache` process singleton, and the worker parent opens a pooled
connection BEFORE it forks: `app/worker/__main__.py:_user_ids()` enumerates queue lanes for the
round-robin worker. The forked work-horse inherits that socket, so parent and child shared ONE
connection. The horse opened a transaction on it; Force stop SIGKILLed the horse mid-transaction; the
socket was left `INTRANS`. The parent's next checkout is the stopped callback, and `pool_pre_ping`
cannot rescue it - psycopg raises `ProgrammingError` when asked to reset autocommit on an in-transaction
connection, and that is not a disconnect error, so the pool propagates it instead of reconnecting.

This is why the defect was invisible until now: before D4 the parent worker never touched the database,
so the corruption it had been causing all along had nothing to break.

**Fix.** One line at `_run`'s entry (`tasks.py`), the single choke point every job kind funnels
through, before any DB use:

```python
get_engine().dispose(close=False)
```

`close=False` is SQLAlchemy's documented fork initializer - verified in the installed 2.0.51 docstring,
which states the parameter was added in 1.4.33 "to allow the replacement of a connection pool in a
child process without interfering with the connections used by the parent process". The horse
de-references the inherited pool without closing the parent's sockets and builds its own connections,
so it can never corrupt one the parent will reuse. Chosen over disposing at each of the four
`worker_fn` entry points (one missed site reintroduces the bug silently) and over making the callback
retry on a fresh connection (that treats the symptom and leaves every future parent-side DB read
broken).

**Test.** `test_the_work_horse_replaces_the_pool_it_inherited_before_any_query` asserts ORDER, not just
that dispose was called: a `before_cursor_execute` listener and a dispose spy write to one timeline and
the first entry must be `dispose`. Disposing after the first query would leave the inherited connection
already used and the bug intact. It also pins `close=False`, because the default `True` would have the
child close the parent's sockets. Verified to fail on the unfixed code with
`queried before replacing the inherited pool: ['sql', 'sql', 'sql']`.

**Live proof.** Force stop on a real segment job: terminal `state=cancelled stage=cancelled error=None`
in **0.5s**, rows preserved (3 -> 3), `active_jobs=0`, document back to `uploaded`, worker log clean.
The previously wedged job 1194 was reaped as `interrupted` by boot `recover_orphans` on API restart,
which also confirms the backstop path.

**Relevance to the concurrency target.** More workers means more parent processes that outlive dead
forks, so this had to be fixed structurally rather than per-call-site.

## D6 - the escalation leaked across a chained job (frontend)

D3 fixed the escalation TIMER outliving its run. Live testing then showed the escalation FLAG leaking
by a second route the `watching`-only reset could not see.

**Symptom, recorded from the DOM at 200ms intervals.** Job started, page loaded mid-run, Stop pressed:

```
   201ms  btn="Stop"        bar=true
  5602ms  btn=null          bar=true    <- the segment job ended; the chained job had not started
 15614ms  btn="Force stop"  bar=true    <- the NEW job's FIRST button state
```

The reviewer's first press on that job would have been a hard kill, on a run that had never been asked
to stop cooperatively.

**Cause.** The reset effect keyed only on `wf.watching`:

```js
useEffect(() => { if (!wf.watching) { ...setForceReady(false)... } }, [wf.watching]);
```

Segmentation chains straight into the duplicate check, so the active job changes while `watching` stays
true for the whole handover - `bar=true` across all three samples above. The effect therefore never
ran, and `forceReady`, which had legitimately expired on the finished job, was inherited by the next
one. This is not an edge case: the chain is the normal path.

**Fix.** Scope the escalation to a JOB, not to a watch session. The hook now publishes `activeJobId` as
state (it existed only as `activeJobRef`, and a ref cannot drive a re-render), and the reset effect
keys on `[wf.watching, wf.activeJobId]`.

**Test.** `does not carry an escalation across a chained job while it keeps watching` - escalates on job
1, swaps to job 2 with `watching` never false, and asserts the button is back to "Stop" AND that the
next press calls `cancelActiveJob(false)`. Verified to fail on the unfixed code with
`expected 'Force stop' to be 'Stop'`.

**Live re-check.** Escalation still behaves for a single job: `Stop` -> `Stopping...` -> `Force stop` at
10.8s, matching the server's 10s grace. The cooperative stop then settled and the banner appeared
within 500ms offering Continue / Start over, with all rows intact
(`.github/pr-media/stop-03-banner-continue-or-start-over.png`). The chain-handover race itself depends
on the segment job finishing in the same second the stop is requested and could NOT be re-triggered on
demand, so the deterministic unit test is the guard, not the live run.
