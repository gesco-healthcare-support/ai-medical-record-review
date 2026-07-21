---
feature: resumable-summarize
date: 2026-07-21
status: in-progress
base-branch: feat/resumable-summarize
related-issues: []
---

## Goal

Make summarization resumable and never dead-end: each summary persists the moment it succeeds,
transient 429/5xx pressure pauses the run and auto-resumes the remaining rows forever, and a
permanent failure ends the job in a calm "needs attention" state -- replacing today's
all-or-nothing `delete + add_all` that loses every row on a single mid-run error.

## Context & decisions

Why now: this is the last MRR-AI-rewrite backlog item (items 1-6 merged, PR #27 -> main @
5abb9fd). Real runs on the Sarhad box lost 15/17 then 7/17 summaries to a mid-run 429 rollback.
Scope is summarize ONLY; segment/classify keep today's behavior (a follow-up item).

Constraints: rq 2.10.0 (verified in the container); Redis persistence is OFF (`--save "" --appendonly
no`, HIPAA) so scheduled state is ephemeral; the summarize pool is DB-free (persistence stays on the
main thread); tests run against docker Postgres with the pipeline mocked, invoking
`summarize_document(job_id)` directly (no live scheduler/Vertex).

Resolved Open Decisions:
- Decision: delayed self-requeue via **RQ scheduler + `Queue.enqueue_in`** (not in-process sleep)
  because it frees the worker slot during an indefinite quota pause and models "retry forever"
  cleanly; cost is a one-line `with_scheduler=True` in the worker entrypoint + an `rq_job_id`
  correlation column for recovery.
- Decision: **any permanent per-row failure ends the whole job `needs_attention`** (partial
  summaries preserved + the failed sub-documents named) because a silent gap in a medical-record
  summary is worse than an explicit "these N could not be read" notice.
- Decision: **skip-done is keyed by row identity `(row_start, row_end, category)` across ALL
  runs** (auto-resume, manual re-click, post-crash) so a 429 only ever costs the remaining rows;
  a `fresh=true` "Re-summarize all" control forces a full redo. Reused summaries keep the
  reviewer's edits.

## All needed context

State machine today (`backend/app/worker/tasks.py`):
- `_run(job_id, work)` [tasks.py:32] is the shared runner: `running -> done/error`. `except
  Exception` [tasks.py:59] rolls back + sets `job.state="error"`, `document.status="error"`,
  `job.error=user_facing_message(exc)`.
- `summarize_document` [tasks.py:152]: `work()` submits all included `ReviewRow`s to a
  `ThreadPoolExecutor(max_workers=pipeline_workers=2)`, `future.result()` [tasks.py:189] raises on
  any row error, then `delete(Summary) + add_all` [tasks.py:208-209] AFTER all rows -- the
  all-or-nothing point.
- `summarize_row(pdf_path, row, model, prompt)` [summarize_engine.py] is DB-free; raises
  `EmptyExtractionError` on blank OCR, else propagates the seam's re-raised error.

Failure taxonomy at the seam (`backend/app/services/genai_retry.py:89` `generate_with_retry`):
after `genai_max_retries`(6) it RE-RAISES the last exception. Classify:
- TRANSIENT: `errors.ClientError` code==429 and NOT ("PerDay"/"free_tier" in str) [mirror
  genai_retry.py:104-110]; `errors.ServerError` (5xx/503); `httpx.TransportError`.
- PERMANENT: `PipelineError` (Empty/OcrUnavailable); `errors.ClientError` code!=429 (auth/400);
  429 with "PerDay"/"free_tier" (daily quota); any other exception.

RQ facts (verified, rq 2.10.0): `Queue.enqueue_in(time_delta, func, args, kwargs)` schedules into
`ScheduledJobRegistry`; a running scheduler moves due jobs to the queue. `Worker.work(...,
with_scheduler=False)` -- flip to `True`. `Worker.work()` called with no flag today
[worker/__main__.py:31]. Recovery [worker/recovery.py] treats RQ status `scheduled` as healthy
[recovery.py:21] and reaps queued/running jobs whose RQ job is gone; it fetches by
`RQJob.fetch(str(job.id))` [recovery.py:37] -- must fetch by the current rq id for requeued jobs.
`recover_orphans` runs at web startup [app/main.py:25 `_lifespan`].

Job service (`backend/app/services/jobs.py`): `ACTIVE_STATES=("queued","running")` [jobs.py:23]
drives the 409 guard `active_job()` [jobs.py:34]; `enqueue` forces `job_id=str(job.id)`
[jobs.py:104] so rq id == db id, and on dispatch failure marks `interrupted` [jobs.py:108-115].
`Document.active_job` property hardcodes `("queued","running")` [models.py:162]; the 409 route
guards use it [documents.py:296 delete, :340 put_rows, :383 summarize_start]. The partial-unique
index predicate is `state IN ('queued','running')` [migration 009991f2eda1:31; models.py:196-197].

Frontend: `use-review-workflow.ts` `pollJob` [hooks/use-review-workflow.ts:80] resolves on
`done`, rejects on `error`/`interrupted`, else re-ticks every 1s (so an unknown state like
`paused` already keeps polling). `STAGE_LABELS` [use-review-workflow.ts:22]. `watchSummarize`
[:137], `boot()` [:162] branches on `detail.active_job` + `detail.status`. `getStatus` returns
the latest job of any state [documents.py:323]. Progress renders via `wf.watching` +
`wf.progress` [review-page-client.tsx:66]; error `wf.banner` [:107]. `JobState`/`DocumentStatus`
[lib/types.ts:12,25]. `startSummarize(id, rows)` [lib/review-api.ts:36].

Migration head: `b1f4a7c9d2e3` [alembic/versions/b1f4a7c9d2e3_add_document_header_fields.py:17].
Index create to mirror: [009991f2eda1:26-32].

Gotchas: `Job.state`/`Document.status` are `String(16)` -- "needs_attention" is 15 chars, fits.
Windows `pnpm build` cannot finish Next standalone (EPERM); "Compiled + tsc" is the Windows pass.
Backend format must match CI ruff 0.15.21 (`uvx ruff@0.15.21 format`).

## Tasks (implementation blueprint) -- ordered

### T1 -- MODIFY `backend/app/models.py`: Job columns + paused-aware active_job
- what: add to `Job`: `rq_job_id = Column(String(64))`, `attempts = Column(Integer, nullable=False,
  server_default="0", default=0)`, `attention = Column(JSON)`. Change `Document.active_job`
  [models.py:162] to include `"paused"` in the state tuple. Widen the index predicate text in
  `Job.__table_args__` [models.py:196-197] to `state IN ('queued', 'running', 'paused')` (both
  postgresql_where + sqlite_where).
- pattern: existing columns [models.py:201-214]; `JSON` already imported [models.py:17].
- approach: code
- acceptance (EARS): The system shall expose `Job.rq_job_id`, `Job.attempts`, `Job.attention`;
  WHEN a job is `paused`, THE SYSTEM SHALL return it from `Document.active_job`.

### T2 -- CREATE `backend/alembic/versions/<rev>_resumable_summarize.py`
- what: `down_revision="b1f4a7c9d2e3"`. upgrade: `op.add_column("jobs", rq_job_id VARCHAR(64)
  null)`, `attempts INTEGER not null server_default '0'`, `attention JSON null`; then
  `op.drop_index("uq_one_active_job_per_document", table_name="jobs")` +
  `op.create_index(... postgresql_where=sa.text("state IN ('queued','running','paused')"))`.
  downgrade reverses (drop 3 columns; restore the 2-state index).
- pattern: mirror [b1f4a7c9d2e3 upgrade] for add_column and [009991f2eda1:26-36] for the index.
- approach: code
- acceptance (EARS): WHEN `alembic upgrade head` runs on the dev Postgres, THE SYSTEM SHALL add
  the 3 columns and the 3-state partial-unique index without error; downgrade shall reverse it.

### T3 -- CREATE `backend/app/worker/failures.py`: failure classifier + control signals
- what: `classify_failure(exc) -> "transient" | "permanent"` per the taxonomy above; `reason_for
  (exc) -> str` (PipelineError -> `user_facing_message`; PerDay 429 -> "The daily AI quota is
  used up; it resets on Google's schedule."; other ClientError -> "The AI service rejected the
  request."; else `GENERIC_USER_MESSAGE`). Define `class JobPaused(Exception)` (carries
  `delay: int`, `done: int`, `total: int`) and `class JobNeedsAttention(Exception)` (carries
  `message: str`, `rows: list[dict]`).
- pattern: 429/PerDay detection mirrors [genai_retry.py:104-110]; import `errors` from
  `google.genai`, `httpx`, `PipelineError`/`user_facing_message`/`GENERIC_USER_MESSAGE` from
  `app.errors`.
- approach: tdd
- acceptance (EARS): WHEN given a 429 ClientError without "PerDay", THE SYSTEM SHALL return
  "transient"; WHEN given an `EmptyExtractionError`, a non-429 ClientError, or a PerDay 429, THE
  SYSTEM SHALL return "permanent".

### T4 -- MODIFY `backend/app/worker/tasks.py`: `_run` finalizers + resumable `summarize_document`
- what (`_run`): before the generic `except Exception` [tasks.py:59], add `except JobNeedsAttention
  as sig`: set `job.state="needs_attention"`, `job.error=sig.message`, `job.attention={"rows":
  sig.rows,"message":sig.message}`, `document.status="needs_attention"`, `finished_at`, commit.
  Add `except JobPaused as sig`: set `job.state="paused"`, `job.stage="paused"`,
  `job.current=sig.done`, `job.total=sig.total`, `job.attempts += 1`; requeue via
  `queue_for(job.kind).enqueue_in(timedelta(seconds=sig.delay), worker_fn(job.kind), job.id)`,
  set `job.rq_job_id=<rq job>.id`; keep `document.status` unchanged ("summarizing"); commit. If the
  enqueue_in raises, mark `interrupted` (mirror [jobs.py:108-115]).
- what (`summarize_document.work`) -- replace the body [tasks.py:157-209] with:
  1. load included `ReviewRow`s (ordered by idx) -> `rows` (list of `as_row()` + keep idx).
  2. reconcile persisted summaries for `document_id`: delete any Summary whose
     `(row_start,row_end,category)` is NOT in the current row set (stale); for each current row i,
     if a Summary with matching identity exists set its `idx=i` and treat the row as DONE.
  3. resolve `prompt_by_cat` for the rows still needing generation (mirror [tasks.py:170-174]).
  4. submit the needing-generation rows to `ThreadPoolExecutor(max_workers=pipeline_workers)`; in
     the `as_completed` loop: on success -> build the Summary (mirror [tasks.py:192-206]),
     `session.add` + `commit` (persist immediately), reset `consecutive_transient=0`, `report(
     "summarizing", done_count, total)`; on exception -> `kind=classify_failure(exc)`; if
     "permanent" record `{idx,pages,reason}` in `attention_rows`; if "transient" append idx to
     `transient_idx` and `consecutive_transient += 1`; WHEN `consecutive_transient >=
     settings.summarize_pause_after` cancel the not-started futures and break.
  5. after the pool: if `transient_idx` non-empty -> raise `JobPaused(delay=
     settings.summarize_resume_delay, done=done_count, total=total)`; elif `attention_rows`
     non-empty -> raise `JobNeedsAttention(message=<"N of M documents could not be summarized: ...">,
     rows=attention_rows)`; else fall through -> `_run` marks `done`.
- what (module import): add `from datetime import timedelta` and `from app.worker.queues import
  queue_for, worker_fn`; import the signals + classifier from `app.worker.failures`.
- pattern: threading + ordering [tasks.py:179-190]; Summary shape [tasks.py:192-206]; friendly
  finalize [tasks.py:59-77].
- approach: tdd
- acceptance (EARS):
  - WHEN every row summarizes, THE SYSTEM SHALL persist one Summary per row (idx = row position)
    and finish `done`.
  - WHILE a summarize job runs, IF a row raises a transient error and `consecutive_transient`
    reaches `summarize_pause_after`, THEN THE SYSTEM SHALL stop, keep the already-persisted
    summaries, enqueue a delayed resume, and set the job `paused`.
  - WHEN a paused job's `summarize_document` runs again, THE SYSTEM SHALL skip rows that already
    have a Summary and only (re)attempt the remaining rows.
  - IF, after all retryable rows are done, one or more rows failed permanently, THEN THE SYSTEM
    SHALL finish `needs_attention` with a message naming the count, while preserving every
    successful Summary.
  - The system shall never `delete(Summary)` for the whole document except when starting fresh
    (T6).

### T5 -- MODIFY `backend/app/worker/__main__.py` + `backend/app/services/jobs.py` + `recovery.py`
- what: `Worker(...).work(with_scheduler=True)` [worker/__main__.py:31] so scheduled resumes fire.
  In jobs.py set `ACTIVE_STATES=("queued","running","paused")` [jobs.py:23] and set
  `job.rq_job_id = str(job.id)` in `enqueue` after the successful dispatch [jobs.py:102-107]. In
  recovery.py change the fetch to `RQJob.fetch(job.rq_job_id or str(job.id), ...)` [recovery.py:37]
  and add `"summarizing"`-status doc handling for paused is already covered [recovery.py:48].
- pattern: [jobs.py:102-107], [recovery.py:36-51].
- approach: code
- acceptance (EARS): WHILE the summarize worker runs, THE SYSTEM SHALL run an RQ scheduler; WHEN a
  paused job's scheduled RQ job still exists, THE SYSTEM SHALL leave it alone during startup
  recovery; WHEN a second job is requested while one is `paused`, THE SYSTEM SHALL raise
  `JobConflict` (409).

### T6 -- MODIFY `backend/app/schemas/documents.py` + `backend/app/api/documents.py`: fresh flag
- what: add `fresh: bool = False` to `SummarizeStartPayload` [schemas/documents.py:17]. In
  `summarize_start` [documents.py:372], WHEN `payload.fresh` is true, `session.execute(delete(
  Summary).where(Summary.document_id == document.id))` before `enqueue` so the run redoes every
  row. Add `config`: `summarize_pause_after: int = 3`, `summarize_resume_delay: int = 60` to
  `Settings` [config.py:53 area].
- pattern: existing route body [documents.py:372-403]; delete pattern [tasks.py:208].
- approach: test-after
- acceptance (EARS): WHEN `POST /documents/{id}/summarize/start` is called with `fresh:true`, THE
  SYSTEM SHALL clear existing summaries first; WHEN called without `fresh`, THE SYSTEM SHALL leave
  existing summaries for the worker to reuse.

### T7 -- MODIFY frontend: types + workflow hook + review-api
- what: `lib/types.ts` add `"paused" | "needs_attention"` to `JobState` and `"needs_attention"`
  to `DocumentStatus`. `lib/review-api.ts` `startSummarize(id, rows, fresh=false)` -> include
  `fresh` in the body. `hooks/use-review-workflow.ts`: add `STAGE_LABELS.paused = "Paused --
  waiting for capacity, will retry automatically"`; add `attention` state (`{message:string}|null`)
  + expose it; in `pollJob` add `if (job.state === "needs_attention") { resolve with an attention
  marker }` (paused keeps polling via the existing fall-through [use-review-workflow.ts:109]);
  `watchSummarize` -> if attention set, `enterEditor()` + keep the notice, else `showSummaries()`;
  `boot()` -> `else if (detail.status === "needs_attention")` fetch `getStatus` for the reason,
  set `attention`, `enterEditor()`; `onSummarize(fresh=false)` passes `fresh` through; clear
  `attention`/`banner` at the start of `onStart`/`onSummarize`.
- pattern: `pollJob` states [use-review-workflow.ts:106-109]; `watchSummarize` [:137-152];
  `boot()` branches [:186-195].
- approach: test-after
- acceptance (EARS): WHILE a job is `paused`, THE SYSTEM SHALL keep polling and show the paused
  label (not an error); WHEN a job ends `needs_attention`, THE SYSTEM SHALL show a calm notice
  with the reason and NOT the red error banner.

### T8 -- MODIFY frontend: `review-page-client.tsx` + `evaluators-ds.css`
- what: render `wf.attention` as an amber calm notice (guidance: "Review & correct or exclude the
  flagged documents, then Summarize again.") distinct from the red `.banner` [review-page-client.tsx:107];
  add a ghost "Re-summarize all" button next to the primary Summarize [:90-101] shown when
  `summaries.length > 0`, calling `wf.onSummarize(true)` behind a `window.confirm` (mirror the
  re-run confirm [use-review-workflow.ts:220]); add `.rce-progress.paused` (amber) + `.notice-
  attention` styles to `frontend/app/evaluators-ds.css`.
- pattern: banner render [review-page-client.tsx:107]; buttons [:87-101]; existing DS classes in
  evaluators-ds.css.
- approach: test-after
- acceptance (EARS): WHERE summaries exist, THE SYSTEM SHALL show a "Re-summarize all" control
  that (after confirm) starts a `fresh` run; WHEN paused, THE SYSTEM SHALL style the progress bar
  distinctly from a normal run.

## Validation loop

Backend (from `backend/`, docker dev Postgres + Redis up):
1. `uvx ruff@0.15.21 format --check . && uvx ruff@0.15.21 check .`
2. `uv run pytest tests/test_failures.py tests/test_jobs.py -q` (new classifier + resumable
   worker: per-row persist, skip-done reuse, pause -> requeue seam called with delay + state
   paused, needs_attention with preserved summaries, 409 while paused).
3. `uv run alembic upgrade head` then `uv run alembic downgrade -1` then `upgrade head` (migration
   round-trips on the real Postgres).
4. `uv run pytest -q` (full backend suite green -- no regression).

Frontend (from `frontend/`):
5. `pnpm exec tsc --noEmit` (typecheck clean) + `pnpm lint`.
6. `pnpm build` -> expect "Compiled successfully" + type/static-gen pass; the standalone symlink
   EPERM at the end is the known Windows-only non-failure.

End-to-end (deferred to end-verification, real browser + Vertex, per the backlog note): rebuild
`api`/`summarize-worker`/`web`, bring workers up, run a summarize; confirm per-row persistence and
(if quota allows inducing a 429) the paused->resume path, as `adriang@gesco.com`.

## Risk / rollback

Blast radius: the summarize worker path + the job state machine + the review page's progress/notice
UI. Segment/classify are untouched (they still raise -> `error`). The index-predicate change is the
riskiest DB edit: a bad predicate could let two active jobs coexist or block legitimate enqueues --
covered by T5's 409-while-paused test and the migration round-trip (validation step 3).

Rollback: revert the branch (no data migration beyond 3 nullable/defaulted columns + an index
predicate). `alembic downgrade -1` drops the columns and restores the 2-state index; existing
Summary rows are untouched. The worker reverts to all-or-nothing cleanly.
