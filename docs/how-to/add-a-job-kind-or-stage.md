# How to add a job kind or stage

Use this when a new piece of background work needs its own job (a new kind, like `dedup` was), or
when an existing job gets a new phase that should show on the progress bar (a new stage, like
`injury-dates` was).

Read [Pipeline and jobs](../explanation/pipeline-and-jobs.md) first: it explains the runner, the
signals and the terminal writer this procedure plugs into.

## Prerequisites

- The backend environment set up with `uv sync --extra docs` in `backend/`, plus
  `--extra classifier` if the work needs the classifier (torch).
- The test database and Redis running and migrated, as in
  [How to run the tests](run-the-tests.md). The job tests use a real Postgres and a real Redis.
- The frontend installed with `pnpm install` in `frontend/`, if the job is started from the
  workbench.

## Add a job kind

Make the changes in this order; each step names the file and the pattern to copy.

### 1. Route the kind to a queue

In `backend/app/worker/queues.py`, add the kind to `_QUEUE_FOR_KIND` and its task's dotted path to
`_WORKER_FN`:

- Use `segment` if the task imports the classifier or anything else that needs torch; only the
  segment worker image (`mrr-backend-classifier`) has it.
- Use `summarize` otherwise.

Both base queues already have worker services, per-user lanes and a scheduler. A new base queue
would also need an entry in `QUEUE_NAMES` and a new worker service in `docker-compose.yml`; prefer an
existing one.

### 2. Give the kind its document statuses

In `backend/app/services/jobs.py`, add the kind to all three maps: `STATUS_ON_ENQUEUE`,
`STATUS_ON_DONE` and `STATUS_ON_CANCEL`. `create_job`, `_finalize_done` and `_finalize_cancelled`
index them directly, so a missing entry fails the job with a `KeyError`.

- Use `None` in all three for advisory work that must not change the stage the reviewer sees
  (the `dedup` pattern and the comment above `STATUS_ON_ENQUEUE`).
- A kind that moves the document into an in-progress status should also be able to move it out on
  failure. Failure and interruption only touch documents in `INTERRUPTIBLE_DOCUMENT_STATUSES`
  (`segmenting`, `summarizing`); reuse one of those rather than adding a status.
- A new document status is a larger change: `documents.status` is `String(16)`, and the frontend
  lists statuses in `frontend/lib/types.ts` (`DocumentStatus`) and
  `frontend/components/documents/status-pill.tsx`.

### 3. Write the task function

In `backend/app/worker/tasks.py`, add an RQ entry point that defines `work` and hands it to `_run`.
`classify_document` is the shortest complete example:

```python
def example_document(job_id) -> None:
    """RQ entry: what this job does, and what it writes."""

    def work(session, job, report):
        document = session.get(Document, job.document_id)
        rows = ...  # load what the job works on
        for i, row in enumerate(rows):
            report("example-stage", i, len(rows))
            ...  # do the work and write results on `session`
        report("example-stage", len(rows), len(rows))

    _run(job_id, work)
```

Rules the task must keep:

- Go through `_run`. It disposes the database pool inherited from the worker parent, marks the job
  running, provides `report`, and routes every outcome to the right finalizer.
- Call `report` in every long loop, and end each stage with `report(stage, total, total)`. `report`
  is where a Stop is observed; a loop without it can only be ended by Force stop.
- Call `report` only from the job's own thread. It commits on the job's session, and a session is
  not thread-safe.
- For a thread pool: resolve anything read from the database before submitting, drain with
  `drain_pool(futures, settings.pool_timeout(page_count))` from `backend/app/services/pools.py`,
  and cancel the queued futures before leaving the pool on any exception.
  `populate_document` in `backend/app/services/page_text.py` is the pattern.
- Commit the job's results on `session` as it goes, or once at the end. An exception from `work()`
  rolls back and ends the job `error`; `JobCancelled` does not roll back.
- Raise a `PipelineError` subclass from `backend/app/errors.py` for a failure the reviewer should
  read about in plain words; any other exception shows the generic message.
- Import heavy modules inside the function, as the existing tasks do, so importing `tasks` stays
  cheap.

`JobPaused` and `JobNeedsAttention` are available too, but only summarize uses them; a new kind that
pauses has to schedule its own resume the way `_finalize_paused` does for summarize.

### 4. Decide its provenance

- `backend/app/services/provenance.py` `job_prompt_fingerprint`: `segment` hashes the segmentation
  prompts and every other kind falls through to the summary prompt set. Add a branch if the new kind
  uses different prompts, so its fingerprint describes what it ran.
- `backend/app/services/jobs.py` `create_job` stamps `title_model`, `audit_model` and `backend` only
  for `summarize`. Extend that only if the new kind makes those calls.

### 5. Add the start route

In `backend/app/api/documents.py`, add a route modelled on `segment_start`: take the document with
`Depends(get_owned_document)` and the caller with `Depends(current_active_user)`, call
`enqueue(session, document.id, "<kind>", model=..., prompt_version=PROMPT_VERSION,
catalog_revision=catalog.catalog_version(session), requested_by=user.id)`, and map `JobConflict` to
HTTP 409 with `_JOB_ALREADY_RUNNING_DETAIL`. `requested_by` records who started the job: an admin
may start one on another reviewer's record, and the worker's audit rows name that user (without it
they name the owner). `jobs.kind` is `String(16)`, so keep
the kind name short. General route conventions are in
[How to add an API route or export](add-an-api-route-or-export.md).

### 6. Make failures countable

If step 3 added a `PipelineError` subclass, add its `user_message` to `_ERROR_CAUSES` in
`backend/app/worker/failures.py`, so `job_outcome` and `backend/scripts/eval/job_health.py` put it
in the right bucket instead of `failed_unknown`.

### 7. Teach the frontend about it

- `frontend/lib/types.ts`: add the kind to `JobKind`.
- `frontend/lib/review-api.ts`: add a start function beside `startSegment` and `startDedup`.
- `frontend/hooks/use-review-workflow.ts`: add the kind where the workflow dispatches on
  `job.kind`, namely the boot code that resumes watching an active job and `restartCancelled`,
  which restarts a cancelled job and falls back to `startSegment` for any kind it does not name.
  Add a label for each new stage to `STAGE_LABELS`.

See [How to extend the frontend](extend-the-frontend.md) for the workbench's structure.

### 8. Deploy the API and the workers together

RQ resolves the task's dotted path inside the worker, so a worker running older code cannot run a
job of a kind it does not know. Build and recreate `api`, `segment-worker` and `summarize-worker`
together, as [How to deploy to the server](deploy-to-the-server.md) does.

## Add a progress stage to an existing job

1. In the job's loop, call `report("<stage>", i, total)` per item and
   `report("<stage>", total, total)` when the stage ends. Use lower case with hyphens, as
   `injury-dates` does; `jobs.stage` is `String(32)`. Stages inside `run_segmentation` report
   through its `progress` argument, which is the job's `report`.
2. If the stage drains a thread pool, report from the draining loop, not from inside the pool
   threads, and cancel queued futures when `report` raises (see the thread-pool rule in step 3).
3. Add a label for the stage to `STAGE_LABELS` in `frontend/hooks/use-review-workflow.ts`; a stage
   without one is shown to the reviewer as its raw name.
4. Add the stage to the stages table in
   [Job and document states reference](../reference/job-and-document-states.md).

## Test it

Add tests beside the existing ones:

| Test file | What to copy |
| --- | --- |
| `backend/tests/test_jobs.py` | `test_queue_routing_maps_kind_to_queue_and_task` and `test_classify_and_dedup_keep_riding_their_task_queue` for routing; `test_create_job_sets_queued_and_document_status` for the enqueue status; `test_run_marks_done_and_advances_status` and `test_run_marks_error_with_a_friendly_message` for the finalizers; `test_a_finished_job_records_the_progress_it_actually_made` for a new stage's completion tick. |
| `backend/tests/test_cancel.py` | `test_report_raises_cancelled_even_when_the_tick_would_be_throttled` and `test_cancel_leaves_a_coherent_document_status` for Stop. |
| `backend/tests/test_documents_api.py` | The start route, including its 409 when another job is active. |
| `backend/tests/test_failures.py`, `backend/tests/test_job_health.py` | A new error message's bucket. |

Run the backend checks from `backend/`:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest -q tests/test_jobs.py tests/test_cancel.py tests/test_failures.py tests/test_job_health.py tests/test_provenance.py tests/test_documents_api.py
```

Run the frontend checks from `frontend/` if you changed it:

```bash
cd frontend
pnpm typecheck
pnpm test
```

## Verify it worked

- The new tests pass, and so does the full backend suite (`uv run pytest -q` in `backend/`).
- On a running stack, starting the job from its route creates a `jobs` row that goes `queued` ->
  `running` -> `done`, the worker log shows `job N (<kind>) started` and `done`, and the progress
  bar shows the stage label. Pressing Stop during the job ends it `cancelled`.
- Starting it twice at once returns 409 for the second request.

## If it fails

- `KeyError` in the worker log for the kind: one of the three status maps in step 2 is missing it.
- The job fails as soon as a worker takes it, with an import error for the task in the worker log:
  the workers run older code. Rebuild and recreate them with the API (step 8).
- The job stays `queued`: no worker is free, none serves the owner's lane, or (for a kind on the
  `segment` queue) its reviewer is at `IDENTIFY_PER_REVIEWER_CAP` while another reviewer waits (see
  [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)).
- Stop does nothing until the job ends: a long loop has no `report` call, or a pool is left without
  cancelling its queued futures.
- To back the change out, remove the route first so no new jobs of the kind are created, let or
  make existing ones finish, then remove the kind from the maps and the queue routing.

## Related pages

- [Pipeline and jobs](../explanation/pipeline-and-jobs.md)
- [Job and document states reference](../reference/job-and-document-states.md)
- [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)
- [How to add an API route or export](add-an-api-route-or-export.md)
- [How to run the tests](run-the-tests.md)
- [Data model reference](../reference/data-model.md)
