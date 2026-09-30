# CLAUDE.md - backend/app/worker

Instructions for an AI coding agent changing the RQ worker tier. Read the README in this folder for
what each file does. Full docs: `docs/explanation/pipeline-and-jobs.md`,
`docs/reference/job-and-document-states.md`, `docs/how-to/add-a-job-kind-or-stage.md`,
`docs/how-to/diagnose-a-stuck-or-failed-job.md`.

## Invariants - do not break

- Every task goes through `tasks._run`. Never add an RQ entry point that skips it: `_run` disposes
  the inherited DB pool (`get_engine().dispose(close=False)`), sets `running`, provides `report`,
  and routes outcomes to the finalizers.
- Keep the `return` after `_finalize_failed(...)` inside `_run`'s `try`. Moving it into the helper
  makes a failed job fall through to `done`.
- `report(stage, current, total)` is the cooperative cancel point. It checks the cancel flag BEFORE
  the throttle. Every long loop must call it; every stage must end with
  `report(stage, total, total)` (the completing tick is exempt from the throttle on purpose).
- Call `report` only from the job's thread. It commits on the job's session.
- Never touch a SQLAlchemy session from inside a thread pool. Resolve DB values before submitting,
  or open a fresh session per call (see `_stored_page_text` in `segment_document`).
- A pool that can be left by an exception (including `JobCancelled` from `report`) must cancel its
  queued futures first; `with ThreadPoolExecutor` waits for all of them otherwise. Pattern:
  `services/page_text.populate_document`. Drain with `services/pools.drain_pool` and
  `settings.pool_timeout(page_count)`.
- Terminal outcomes other than the job's own success go through `services/jobs.mark_terminal`
  (conditional UPDATE on active states; first writer wins). Never hand-write a terminal UPDATE in
  recovery, callbacks or scripts.
- A failure or interruption may move the document only out of `INTERRUPTIBLE_DOCUMENT_STATUSES`
  (`segmenting`, `summarizing`). `dedup` never changes document status (`None` in every map).
- Every kind needs an entry in `STATUS_ON_ENQUEUE`, `STATUS_ON_DONE`, `STATUS_ON_CANCEL`
  (`services/jobs.py`) and in `_QUEUE_FOR_KIND` / `_WORKER_FN` (`queues.py`). They are indexed
  directly; a missing key is a `KeyError` at run time.
- Correlate RQ jobs to DB jobs by `rq_job_id` or by `rq_job.args[0]`. A resumed summarize runs
  under a fresh RQ id, so RQ id == DB id holds only on first dispatch.
- `finalizers.on_job_stopped` / `on_job_failed` are stored by RQ as dotted paths. Keep them
  module-level and importable; they are registered in `services/jobs.enqueue` AND in
  `tasks._finalize_paused`. Callbacks must never raise.
- `queues.py` stays import-light (redis, rq, config). The API imports it; importing torch there
  breaks the web image.
- `cancel.py` stays ORM-free (API, worker and retry loop all import it). A Redis error means "not
  cancelled".
- The active states `('queued', 'running', 'paused')` are spelled out in `services/jobs.py`,
  `models.py` (index predicate x2 and `Document.active_job`), migration `c2d5e8f1a3b7`,
  `scripts/copy_records.py`, `failures._STATE_OUTCOMES`. Change all of them plus a migration, or
  none.
- Summarize: check give-up BEFORE pause in `_SummarizeRun.finish`; a success must reset
  `consecutive_transient`; give-up keeps draining running rows. Pinned by
  `test_a_model_refusing_everything_still_ends_the_job`,
  `test_a_success_between_failures_resets_the_pause_streak` and
  `test_one_success_keeps_the_job_paused_at_any_concurrency`. `should_pause` and `transient_left`
  are not interchangeable, and no test catches collapsing them in the `PoolTimeout` handler (see
  its comment) - leave both assignments.
- Log ids, counts, page numbers and stage names only. Never titles, filenames, header fields or
  page text: they are PHI.

## Traps that have bitten before

- A forked work-horse shared the parent's DB socket; Force stop mid-transaction then broke the
  parent's stopped callback. Fixed by the dispose call at the top of `_run`. Keep it first.
- The completing progress tick was throttled away, so finished jobs showed fewer items than they
  processed.
- `classify_document` must select EVERY row, not `include=True`: aggregate seeds rows at category
  100, which is off by default, so the filter selected nothing.
- `_populate_page_text` swallows OCR-pass failures EXCEPT `OcrUnavailableError` (includes
  `PdfUnreadableError`) and `JobCancelled`. Do not widen the swallow.
- A `CancelledError` from a skipped future must be skipped, not classified as a row failure
  (`future.cancelled()` check in `_summarize_work`).
- Lanes are enumerated once at worker start: a new user's jobs wait until the workers restart.
- `tests/test_jobs.py` patches `app.services.page_text.get_row_text_with_report` and
  `app.services.dedup.cluster_rows` by dotted path; keep those imports function-local.
- A worker setting only reaches containers if `docker-compose.yml` names it in `x-backend-env`.
- RQ forks with `os.fork()`: workers do not run natively on Windows. Test via pytest, run via
  compose.
- Changing worker code needs `api`, `segment-worker` and `summarize-worker` rebuilt and recreated
  together (two images).

## Commands

From `backend/` (test Postgres and Redis must be up; see `docs/how-to/run-the-tests.md`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q tests/test_jobs.py tests/test_cancel.py tests/test_cancel_escapes_model_calls.py tests/test_failures.py tests/test_job_health.py tests/test_main.py tests/test_pools.py tests/test_page_text.py
```

Do not export `DATABASE_URL` for tests; `tests/conftest.py` picks the test database and refuses the
application one.

## When you change behaviour here

- Update `docs/reference/job-and-document-states.md` (kinds, states, stages, writers) and
  `docs/explanation/pipeline-and-jobs.md` in the same PR.
- Add or update a stage label in `frontend/hooks/use-review-workflow.ts` `STAGE_LABELS`.
- New error message -> `failures._ERROR_CAUSES`, or `job_health` reports it as `failed_unknown`.

<!-- reviewed: 2026-09-30 -->
