# How to diagnose a stuck or failed job

Use this when a record's progress bar has not moved for a long time, a job never starts, Stop seems
to do nothing, or a record shows Failed or Interrupted and you need to know why and what to do next.

The job model this relies on (states, Stop and Force stop, finalizer callbacks, orphan recovery) is
explained in [Pipeline and jobs](../explanation/pipeline-and-jobs.md); every state and stage is
listed in [Job and document states reference](../reference/job-and-document-states.md).

## Prerequisites

- A shell in the folder that holds `docker-compose.yml`, with the stack running: the repository
  root on a developer machine, or `/home/<SERVER_USER>/mrr` on `<SERVER_HOST>` (see
  [How to deploy to the server](deploy-to-the-server.md) for connecting). Every command below runs
  there, in Git Bash or any POSIX shell.
- The document id. It is the last part of the workbench URL, `/records/<DOCUMENT_ID>`.
- The queries below select ids, states, counts and the app's own messages only. Do not select
  `original_filename`, header fields, row titles or any text column: they hold patient data.

## Steps

### 1. Read the job rows for the document

```bash
docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, kind, state, stage, current, total, attempts, cancel_requested, rq_job_id, started_at, finished_at FROM jobs WHERE document_id = '<DOCUMENT_ID>' ORDER BY id DESC LIMIT 5;"
```

```bash
docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT status, page_count, user_id FROM documents WHERE id = '<DOCUMENT_ID>';"
```

The newest row is the one the workbench shows. Note its `id` (`<JOB_ID>`), `rq_job_id`
(`<RQ_JOB_ID>`) and the document's `user_id` and `page_count`.

To see every active job on the box at once:

```bash
docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, document_id, kind, state, stage, current, total, started_at FROM jobs WHERE state IN ('queued', 'running', 'paused') ORDER BY id;"
```

For a job that ended `error` or `needs_attention`, read its message and row list:

```bash
docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, state, error, attention FROM jobs WHERE id = <JOB_ID>;"
```

`error` and `attention` hold the app's plain-language messages, page ranges and row positions only.

### 2. Match what you see

| What the job row shows | What it means | Go to |
| --- | --- | --- |
| `queued`, `started_at` empty, for minutes | No worker has taken it: every worker is busy, no worker is listening on the owner's lane, or the workers are down. | Step 4, then 5c |
| `paused`, stage `paused`, `attempts` rising | Summarize hit repeated transient model failures and resumes itself every `SUMMARIZE_RESUME_DELAY` seconds (default 60). There is no limit on cycles. | 5b |
| `running`, `current` moving | Working. | 5b |
| `running`, `current` not moving, recent log lines for the job | A slow stage: the OCR pass (`reading`) or model calls in retry backoff. | Step 3, then 5b or 5a |
| `running`, no log lines for the job for a long time | The work-horse may be gone. | Step 4, then 5d or 5e |
| `cancel_requested` true, still `running` | Stop was asked for and has not been observed yet. | 5a |
| `error` | The job raised. `error` says why in plain words. | 5f |
| `needs_attention` | Summarize finished and named the rows it could not summarize. Not a fault. | 5f |
| `interrupted` | Dispatch to Redis failed, a resume could not be scheduled, the work-horse died, or orphan recovery reaped it. | Step 3, then start the stage again from the workbench |
| `cancelled` | Someone pressed Stop. Committed work is kept. | Continue or Start over from the workbench |

A job may legitimately run for up to `max(JOB_TIMEOUT, page_count x JOB_TIMEOUT_PER_PAGE)` seconds
(defaults 3600 and 20, so a 500-page record gets 10,000 seconds) before RQ stops it. See
[Configuration reference](../reference/configuration.md).

### 3. Read the worker log for that job

Segment and classify jobs run on `segment-worker`; summarize and dedup jobs on `summarize-worker`.
The command shows all replicas of the service:

```bash
docker compose logs --since 3h summarize-worker 2>&1 | grep -e "job <JOB_ID> " -e "<DOCUMENT_ID>"
```

Log lines name job ids, document ids, counts and page numbers, never record content. The ones that
matter here (`backend/app/worker/tasks.py`, `backend/app/worker/finalizers.py`,
`backend/app/services/page_text.py`, `backend/app/services/ocr.py`):

| Line | Meaning |
| --- | --- |
| `job N (kind) started on document D` | A work-horse took the job. |
| `job N stage 'x' on document D` | The job entered stage `x`. |
| `job N (kind) done on document D` | Finished. |
| `job N (kind) failed on document D`, followed by a traceback | Ended `error`; the traceback is the technical cause. |
| `job N paused after d/t; resume scheduled in Ss (attempt a)` | Summarize paused. |
| `summarize row i transient failure on document D (n in a row)` | A row failed with a retryable model error. |
| `summarize pool timed out after Ss on document D; k row(s) will retry` | The row pool hit its time budget; the job pauses. |
| `job N needs attention: k row(s) could not be summarized` | Ended `needs_attention`. |
| `job N (kind) cancelled at d/t` | Stop was observed. |
| `resume enqueue failed for job N; marked interrupted` | A pause could not schedule its resume. |
| `job N finalized as cancelled by the stopped callback` | Force stop landed. |
| `job N finalized as interrupted by the failure callback` | RQ reported the work-horse failed or abandoned. |
| `page text population failed for D` | The OCR pass failed; later stages read pages on demand. |
| `OCR gave up on page P after 2 attempt(s)` | One page errored twice. |
| `worker listening round-robin on N queue(s): [...]` | Printed at worker start: the lanes this worker serves. |
| `could not enumerate users for queue lanes; serving base queues only` | At worker start the user list could not be read. |

Orphan recovery logs in the API, not the workers:

```bash
docker compose logs --since 1h api 2>&1 | grep -i -e "orphan recovery" -e "force stop could not be delivered"
```

If a worker container keeps restarting, read the end of its log; a worker refuses to start when the
model-backend preflight fails (see [How to switch model backends](switch-model-backends.md)):

```bash
docker compose ps
docker compose logs --tail 50 segment-worker
```

### 4. Check RQ and the cancel flag

Queue lengths and what each worker is doing:

```bash
docker compose exec -T api rq info --url redis://redis:6379/0
```

The RQ status of this job and the worker holding it:

```bash
docker compose exec -T api python -c "from rq.job import Job; from app.worker.queues import get_redis; j = Job.fetch('<RQ_JOB_ID>', connection=get_redis()); print(j.get_status(), j.worker_name)"
```

A `NoSuchJobError` traceback means RQ has no record of the job (for example after Redis restarted;
Redis runs without persistence).

Whether a Stop is pending, and for how many more seconds:

```bash
docker compose exec -T redis redis-cli GET mrr:cancel:<JOB_ID>
docker compose exec -T redis redis-cli TTL mrr:cancel:<JOB_ID>
```

`"1"` means a Stop is waiting to be observed; `(nil)` means none is pending, or it already expired.

### 5. Act on what you found

**a. Stop, then Force stop.** In the workbench, press Stop on the progress bar. The run normally
stops within a second: the worker checks at every progress write and in every one-second slice of
a model-call retry wait. If it has not stopped after the grace period (`JOB_CANCEL_GRACE_SECONDS`,
default 10), the button becomes Force stop; pressing it kills the work-horse, and the worker parent
records `cancelled`. The Stop button belongs to the progress bar the workbench shows while it
watches an identify or summarize run. For any other job, the document owner can call
`POST /api/documents/<DOCUMENT_ID>/jobs/<JOB_ID>/cancel` with body `{"force": false}` (then
`{"force": true}`); the route accepts only the document's owner. See
[HTTP API reference](../reference/http-api.md).

Force stop cannot reach a job with no running work-horse (`queued`, or `paused` between attempts).
The API logs `force stop could not be delivered`, and the job is cancelled the first time it runs:
for a paused summarize, when its scheduled resume starts.

**b. Wait.** A `paused` job resumes on its own. A `running` job that is still writing progress or
log lines is working; the OCR pass alone can take on the order of ten minutes for a few hundred
pages (the code records about 700 s for a 297-page record).

**c. Restart the workers when a job waits on a lane nobody serves.** Workers list their lanes when
they start, so a user created after that has no lane. Compare the document's `user_id` with the
`worker listening round-robin on N queue(s)` line from step 3. If the lane `segment:<user_id>` or
`summarize:<user_id>` is missing, first make sure no other job is `running` (step 1, all active
jobs), because a restart can cut off a running job, then:

```bash
docker compose restart segment-worker summarize-worker
```

The queued job is picked up once the workers are back.

**d. Restart the API when the database still says active but RQ has lost the job.** Orphan recovery
runs only when the API starts. If step 4 shows no RQ record, or an RQ status other than `queued`,
`started`, `deferred` or `scheduled` (for example `failed`, `stopped` or `canceled`), restart the
API so recovery marks the job `interrupted` and frees the document:

```bash
docker compose restart api
```

The restart drops in-flight web requests for a few seconds. The API log then shows
`startup orphan recovery interrupted N stale job(s)`.

**e. Wait for RQ when a dead worker's job still reads `started`.** After a worker container is
replaced or crashes, RQ can keep the job at `started` until its own registry maintenance notices the
dead work-horse and fires the failure callback, which records `interrupted`. Orphan recovery leaves
`started` jobs alone, so an API restart does not help yet. Check `rq info` from step 4: if no worker
lists the job, wait and re-check step 1; once RQ reports `failed` or loses the job, step 5d applies.

**f. A job that ended `error` or `needs_attention`.** Read `error`. The messages and their causes
are listed in [Errors and messages reference](../reference/errors-and-messages.md). Common ones:

| `error` starts with | Cause | What to do |
| --- | --- | --- |
| "Text recognition (OCR) is unavailable on the server" | Tesseract or Poppler is missing from the worker image. | Check the binaries, then rebuild the image: `docker compose exec -T segment-worker tesseract --version` and `docker compose exec -T segment-worker pdftoppm -v`. |
| "This PDF could not be opened" | The stored file is corrupt, encrypted, truncated or missing. | Have the reviewer upload the file again. |
| "The AI service was busy" or "The daily AI quota has been used up" | Model capacity or quota. | Start the stage again later. |
| "One part of this document needed longer than the current per-request time limit" | A model request exceeded the configured deadline. | See [How to switch model backends](switch-model-backends.md) and the deadline settings in [Configuration reference](../reference/configuration.md). |
| "The AI service rejected the request" | The model backend refused the call: a permission, credential or request problem. | Check the backend configuration; see [How to switch model backends](switch-model-backends.md). |
| "The AI generated a value too long to store" | A generated value exceeded its database column. | Start again; if it repeats, the worker log traceback names the column. |
| "Processing took too long and was stopped" | A pipeline stage exceeded its time budget. | Read the log from step 3 for which stage; start again. |
| "Something went wrong while processing this document" | Anything unrecognized. | The traceback after `job N (kind) failed` in the worker log names the cause. |

For `needs_attention`, the workbench lists the rows from `attention`; the reviewer corrects,
excludes or re-runs them and summarizes again. Summarization problems in the output itself are
covered in [How to troubleshoot a summary](troubleshoot-a-summary.md).

## Verify it worked

- Step 1 shows the job in a terminal state (`done`, `cancelled`, `interrupted`, `error` or
  `needs_attention`), or `running` with `current` moving.
- No row for the document remains in `queued`, `running` or `paused` unless a job is genuinely
  working, so the reviewer can start the next stage without a 409.
- The document status matches the job: see the status tables in
  [Job and document states reference](../reference/job-and-document-states.md).

## If it does not work

- A restart of the API or workers changes no data; restarting again is safe.
- A job left active with no work-horse keeps blocking its document. Do not update the `jobs` row by
  hand: several processes finalize jobs through `mark_terminal`
  (`backend/app/services/jobs.py`), which only ever moves a still-active row, and a hand-written
  update can overwrite an outcome another process has just written.
- If the same failure repeats, collect the job ids, the `error` text and the worker log lines (they
  contain no patient data) before changing configuration.

## Operator scripts

| Script | Use | Command |
| --- | --- | --- |
| `backend/scripts/eval/job_health.py` | Pipeline health by outcome rather than raw state: separates in-flight, stopped, orphaned and partial runs from real failures, and names unrecognized error messages. Options: `--kind`, `--since YYYY-MM-DD`, `--build <sha prefix>`, `--by-kind`. Prints no patient data. | `docker compose exec -T api python -m scripts.eval.job_health --by-kind` |
| `backend/scripts/repair_dedup_clobbered_status.py` | One-off, idempotent repair for records whose finished status was overwritten with `reviewing` by a duplicate check before that was fixed. Scope is always explicit (`--all`, `--user-email`, `--document-id`); run with `--dry-run` first. | `docker compose exec -T api python scripts/repair_dedup_clobbered_status.py --dry-run --all` |
| `backend/scripts/dev/ocr_concurrency_hammer.py` | Proves the OpenMP thread limit that stops concurrent Tesseract deadlocks, inside a worker container; restart the worker between runs. | See the script header and [OCR and page text](../explanation/ocr-and-page-text.md). |

All scripts are described in [Scripts reference](../reference/scripts.md).

## Related pages

- [Pipeline and jobs](../explanation/pipeline-and-jobs.md)
- [Job and document states reference](../reference/job-and-document-states.md)
- [OCR and page text](../explanation/ocr-and-page-text.md)
- [How to deploy to the server](deploy-to-the-server.md)
- [How to manage users and admins](manage-users-and-admins.md)
- [Errors and messages reference](../reference/errors-and-messages.md)

<!-- reviewed: 2026-09-30 -->
