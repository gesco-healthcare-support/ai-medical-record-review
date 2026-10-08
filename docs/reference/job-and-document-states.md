# Job and document states reference

Every job kind, job state, progress stage and document status, the transitions between them, who
writes each, and where the one-active-job rule and the active-state list live.

Source of truth: `backend/app/services/jobs.py` (status maps, `ACTIVE_STATES`, `mark_terminal`,
`create_job`, `enqueue`), `backend/app/worker/tasks.py` (`_run` and the `_finalize_*` functions),
`backend/app/worker/finalizers.py`, `backend/app/worker/recovery.py`,
`backend/app/worker/queues.py`, `backend/app/worker/failures.py`, `backend/app/models.py` (`Job`,
`Document`), `backend/app/api/documents.py`, `frontend/hooks/use-review-workflow.ts`
(`STAGE_LABELS`), `frontend/components/documents/status-pill.tsx` (`STATUS_LABELS`).

For how these fit together, see [Pipeline and jobs](../explanation/pipeline-and-jobs.md).

## Job kinds

| Kind | Base queue | Task function | Worker service (image) | Enqueued by | `jobs.model` recorded | Stages reported |
| --- | --- | --- | --- | --- | --- | --- |
| `segment` | `segment` | `app.worker.tasks.segment_document` | `segment-worker` (`mrr-backend-classifier`) | `POST /api/documents/{id}/segment/start` | `Settings.genai_model` | `reading`, `segmenting`, `categorizing`, `verifying`, `injury-dates` |
| `classify` | `segment` | `app.worker.tasks.classify_document` | `segment-worker` (`mrr-backend-classifier`) | `POST /api/documents/aggregate`, automatically after the upload | `Settings.genai_model` | `categorizing` |
| `dedup` | `summarize` | `app.worker.tasks.dedup_document` | `summarize-worker` (`mrr-backend-web`) | `POST /api/documents/{id}/dedup/start` | `Settings.classify_model` | `deduping` |
| `summarize` | `summarize` | `app.worker.tasks.summarize_document` | `summarize-worker` (`mrr-backend-web`) | `POST /api/documents/{id}/summarize/start`; `POST /api/admin/reprocess/{document_id}` | the request's `model`, else `Settings.model_for("body")` | `summarizing`, `paused` |

Each job is enqueued on the owner's lane, `<base>:<user_id>`, or on the bare base queue when the
document has no owner (`backend/app/worker/queues.py` `queue_for`). Only `summarize` jobs also record
`title_model`, `audit_model` and `backend` (`create_job`).

### Document status by kind

| Kind | `STATUS_ON_ENQUEUE` | `STATUS_ON_DONE` | `STATUS_ON_CANCEL` |
| --- | --- | --- | --- |
| `segment` | `segmenting` | `reviewing` | `uploaded` |
| `classify` | `segmenting` | `reviewing` | `reviewing` |
| `summarize` | `summarizing` | `done` | `reviewing` |
| `dedup` | `None` (unchanged) | `None` (unchanged) | `None` (unchanged) |

## Job states

`jobs.state` is `String(16)`, default `queued`.

| State | Active | Meaning | Written by | `job_outcome` bucket | In a failure rate | Workbench poller |
| --- | --- | --- | --- | --- | --- | --- |
| `queued` | yes | Created, waiting for a worker. | `create_job` | `in_flight` | no (excluded from the denominator) | keeps polling |
| `running` | yes | A work-horse is running it. | `_run` | `in_flight` | no (excluded) | keeps polling |
| `paused` | yes | Summarize only: waiting for its scheduled resume. | `_finalize_paused` | `in_flight` | no (excluded) | keeps polling |
| `done` | no | Finished. | `_finalize_done` | `completed` | no | settles as done |
| `needs_attention` | no | Summarize only: finished, and named the rows it could not summarize. | `_finalize_needs_attention` | `partial` | no | settles, with the row list |
| `cancelled` | no | Stopped by the reviewer, cooperatively or by Force stop. | `_finalize_cancelled`, `on_job_stopped` | `stopped` | no | settles as cancelled |
| `interrupted` | no | The system lost the job: dispatch failed, resume could not be scheduled, the work-horse died, or orphan recovery reaped it. | `enqueue`, `_finalize_paused`, `on_job_failed`, `recover_orphans` | `orphaned` | no | shows "the run was interrupted" |
| `error` | no | `work()` raised. `error` holds a plain-language message. | `_finalize_failed` | by message, see [Error outcomes](#error-outcomes) | yes | shows `error` |

Any state not in `job_outcome`'s table is bucketed `failed_unknown` (`backend/app/worker/failures.py`
`job_outcome`).

## Job state transitions

| From | To | Trigger | Writer | Through `mark_terminal` |
| --- | --- | --- | --- | --- |
| (none) | `queued` | Row inserted. | `create_job` | no |
| `queued` | `interrupted` | RQ dispatch raised (for example Redis unreachable). `enqueue` then raises `QueueUnavailable`, which the API answers with 503. | `enqueue` | no |
| `queued` | `queued` | An identify job (`segment`, `classify`) steps aside: its reviewer already has `IDENTIFY_PER_REVIEWER_CAP` identify jobs running and another reviewer is waiting. It goes back to the front of its lane under a new `rq_job_id` (`<id>-turn-<hex>`); `started_at` stays empty (`backend/app/worker/fairness.py`). | `_step_aside` (from `_take` in `_run`) | no |
| `queued` or `paused` | `running` | A work-horse starts the job, first run or scheduled resume. | `_take` (from `_run`) | no (unconditional write) |
| `running` | `done` | `work()` returned. | `_finalize_done` | no |
| `running` | `error` | `work()` raised an exception that is not a control signal. | `_finalize_failed` | no |
| `running` | `paused` | `JobPaused` (summarize). | `_finalize_paused` | no |
| `running` | `interrupted` | `JobPaused`, but scheduling the resume raised. | `_finalize_paused` | no |
| `running` | `needs_attention` | `JobNeedsAttention` (summarize). | `_finalize_needs_attention` | no |
| `running` | `cancelled` | `JobCancelled`: the cancel flag was seen. | `_finalize_cancelled` | yes |
| `running` | `cancelled` | Force stop killed the work-horse. | `on_job_stopped` (RQ `on_stopped` callback) | yes |
| `running` | `interrupted` | The work-horse failed or was found abandoned by RQ. | `on_job_failed` (RQ `on_failure` callback) | yes |
| `queued`, `running` or `paused` | `interrupted` | API start-up: the RQ job is missing, or its RQ status is not `queued`, `started`, `deferred` or `scheduled`. | `recover_orphans` | yes |

`mark_terminal` updates only a row whose state is still active; a later caller for the same job gets
no change and returns `False`. All terminal writes set `finished_at`. `_run` sets `started_at`.

## Progress stages

`jobs.stage` is `String(32)`, default `starting`. `report(stage, current, total)` in `_run` writes
it; a stage change always writes, same-stage ticks at most once per second except the tick where
`current == total`.

| Stage | Kinds | Written by | `current / total` counts | Workbench label (`STAGE_LABELS`) |
| --- | --- | --- | --- | --- |
| `starting` | all | column default | 0 / 0 | Starting... |
| `reading` | `segment` | `_populate_page_text` (0 of page count), then `populate_document` | pages stored / pages missing from the store | Reading the pages |
| `segmenting` | `segment` | `segment_engine.run_segmentation` | windows | Finding document boundaries |
| `categorizing` | `segment`, `classify` | `segment_engine.run_segmentation`; `classify_document` | rows | Categorizing each document |
| `verifying` | `segment`, when `VERIFY_MERGE` is true | `verify_pass.verify_and_merge` | suspect boundaries checked | Double-checking uncertain boundaries |
| `injury-dates` | `segment` | `segment_engine.run_segmentation` | rows | none; the raw stage string is shown |
| `deduping` | `dedup` | `dedup_document` | included rows read | not used; the Duplicates tab shows its own "checking" text with `current/total` |
| `summarizing` | `summarize` | `_summarize_work` | included rows with a summary, reused ones included | Writing summaries |
| `paused` | `summarize` | `_finalize_paused` | rows done / included rows | Paused - waiting for capacity, will retry automatically |
| `cancelled` | all | `_finalize_cancelled`, `on_job_stopped` | cooperative stop: the progress carried by the signal; Force stop: unchanged | none; the raw stage string is shown |

`done`, `error`, `needs_attention` and `interrupted` leave the last stage in place.

## Progress payload

`Job.progress()` (`backend/app/models.py`), returned by `GET /api/documents/{id}/status`, the cancel
route and the document listing:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | int | Job id; the cancel route is addressed by it. |
| `kind` | string | Job kind. |
| `state` | string | Job state. |
| `stage` | string | Progress stage. |
| `current` | int | Items done in the stage. |
| `total` | int | Items in the stage. |
| `error` | string or null | Plain-language message for `error`; the summary message for `needs_attention`. |
| `attention` | object or null | `{"rows": [{"idx": int, "pages": "<start>-<end>", "reason": string}], "message": string}`, written only by `_finalize_needs_attention`. |

`GET /api/documents/{id}/status` returns the newest job of any kind, with `attention` taken from the
newest `summarize` job when the newest job is another kind.

## Document statuses

`documents.status` is `String(16)`, default `uploaded`.

| Status | Meaning | Landing-page label (`STATUS_LABELS`) |
| --- | --- | --- |
| `uploaded` | Stored, not identified; also after a cancelled segment job. | Uploaded |
| `segmenting` | A segment or classify job is active. | Identifying documents |
| `reviewing` | Rows exist and are being reviewed. | Ready for review |
| `summarizing` | A summarize job is active, including while paused. | Summarizing |
| `done` | A summarize run finished every included row. | Summarized |
| `needs_attention` | A summarize run finished and named rows it could not summarize. | Needs attention |
| `error` | A segment, classify or summarize job raised. | Failed |
| `interrupted` | A job was lost: dispatch failed, a resume could not be scheduled, a work-horse died, or orphan recovery reaped it. | Interrupted |

`SUMMARIZED_DOCUMENT_STATUSES = ("done", "needs_attention")`;
`INTERRUPTIBLE_DOCUMENT_STATUSES = ("segmenting", "summarizing")` (`backend/app/services/jobs.py`).

## Document status writers

| Writer | Code | Writes | Condition |
| --- | --- | --- | --- |
| Upload | `backend/app/api/documents.py` `create_document`, `aggregate_documents` | `uploaded` (column default) | New document. |
| Job creation | `jobs.py` `create_job` | `STATUS_ON_ENQUEUE[kind]` | Skipped when `None` (dedup). |
| Dispatch failure | `jobs.py` `enqueue` | `interrupted` | When RQ dispatch raises, and only if the status is `segmenting` or `summarizing` (`INTERRUPTIBLE_DOCUMENT_STATUSES`). A failed dedup or classify dispatch leaves the status alone. |
| Success | `tasks.py` `_finalize_done` | `STATUS_ON_DONE[kind]` | Skipped when `None`. |
| Failure | `tasks.py` `_finalize_failed` | `error` | Only when the status is `segmenting` or `summarizing`. |
| Pause that cannot be scheduled | `tasks.py` `_finalize_paused` | `interrupted` | Always, in that branch. |
| Needs attention | `tasks.py` `_finalize_needs_attention` | `needs_attention` | Always. |
| Cooperative stop | `tasks.py` `_finalize_cancelled` via `mark_terminal` | `STATUS_ON_CANCEL[kind]` | Only if `mark_terminal` made the transition; skipped when `None`. |
| Force stop | `finalizers.py` `on_job_stopped` via `mark_terminal` | `STATUS_ON_CANCEL.get(kind)` | Only if `mark_terminal` made the transition; skipped when `None`. |
| Work-horse failed or abandoned | `finalizers.py` `on_job_failed` via `mark_terminal` | `interrupted` | Only if the transition was made and the status is `segmenting` or `summarizing`. |
| Orphan recovery | `recovery.py` `recover_orphans` via `mark_terminal` | `interrupted` | Only if the transition was made and the status is `segmenting` or `summarizing`. |
| Row edit | `documents.py` `put_rows` -> `_reopen_if_summaries_stranded` | `reviewing` | Only from `done` or `needs_attention`, when a stored summary no longer matches a row or an included row has no summary. |
| One-off repair | `backend/scripts/repair_dedup_clobbered_status.py` | `done` or `needs_attention` | Only from `reviewing`, when the newest job is a dedup, the newest other job is a summarize that ended `done` or `needs_attention`, and the document holds at least one summary. |

## The one-active-job rule

At most one job in `queued`, `running` or `paused` per document.

| Where | Code | Effect |
| --- | --- | --- |
| Database | Partial unique index `uq_one_active_job_per_document` on `jobs.document_id` where `state IN ('queued', 'running', 'paused')` (`backend/app/models.py` `Job.__table_args__`; migrations `009991f2eda1` and `c2d5e8f1a3b7`) | A second active row fails to insert. This is the enforcement. |
| Job creation | `jobs.py` `create_job` | `IntegrityError` on commit -> rollback -> `JobConflict`. A document id that does not exist raises `LookupError` before any row is added (the start routes never reach it: `get_owned_document` has already answered 404). |
| Start routes | `segment_start`, `dedup_start`, `summarize_start` in `backend/app/api/documents.py`; `reprocess` in `backend/app/api/admin.py` | `JobConflict` -> HTTP 409. |
| Individual-records upload | `aggregate_documents` | `JobConflict` is ignored; a new document cannot already have a job. |

Routes that refuse while `Document.active_job` is set (any kind unless stated), each with HTTP 409:

| Route | Handler |
| --- | --- |
| `PUT /api/documents/{id}/rows` | `put_rows` |
| `POST /api/documents/{id}/summarize/start` with `rows` in the body | `summarize_start` |
| `DELETE /api/documents/{id}` | `delete_document` |
| `POST /api/documents/{id}/duplicates/{group}/resolve` | `resolve_duplicate` |
| `PUT /api/documents/{id}/summaries/{idx}` with a `category` change | `put_summary` -> `_apply_row_category` |
| `PUT /api/documents/{id}/summaries/{idx}`, any edit, while a `summarize` job is active | `put_summary` |
| `POST /api/documents/{id}/summaries/{idx}/resummarize` | `resummarize` |

The response texts are in [Errors and messages reference](errors-and-messages.md).

## `ACTIVE_STATES` and its copies

`ACTIVE_STATES = ("queued", "running", "paused")` in `backend/app/services/jobs.py`. Imported by
`backend/app/worker/recovery.py` and `backend/app/api/documents.py` (`cancel_job`). These places do
not import it and spell the states out themselves:

| Place | Form |
| --- | --- |
| `backend/app/models.py` `Job.__table_args__` | Index predicate, twice (`postgresql_where`, `sqlite_where`). |
| `backend/app/models.py` `Document.active_job` | Literal tuple. |
| `backend/alembic/versions/c2d5e8f1a3b7_resumable_summarize.py` | Index predicate in the migration that added `paused`. |
| `backend/scripts/copy_records.py` | Its own `ACTIVE_STATES` constant. |
| `backend/app/worker/failures.py` `_STATE_OUTCOMES` | The three states mapped to `in_flight`. |
| `frontend/lib/types.ts` `JobState` | Union of every job state, active and terminal. |

A new active state needs every place above plus a migration that rebuilds the index.

## Terminal writers

| Function | File | Runs in | Uses `mark_terminal` |
| --- | --- | --- | --- |
| `_finalize_done` | `backend/app/worker/tasks.py` | work-horse | no |
| `_finalize_failed` | `backend/app/worker/tasks.py` | work-horse | no |
| `_finalize_paused` | `backend/app/worker/tasks.py` | work-horse | no |
| `_finalize_needs_attention` | `backend/app/worker/tasks.py` | work-horse | no |
| `_finalize_cancelled` | `backend/app/worker/tasks.py` | work-horse | yes |
| `on_job_stopped` | `backend/app/worker/finalizers.py` | worker parent | yes |
| `on_job_failed` | `backend/app/worker/finalizers.py` | worker parent | yes |
| `recover_orphans` | `backend/app/worker/recovery.py` | API start-up | yes |
| `enqueue` (dispatch failure) | `backend/app/services/jobs.py` | API request | no |

## Error outcomes

`job_outcome(state="error", error)` matches the start of `error` against these constants, in order
(`backend/app/worker/failures.py` `_ERROR_CAUSES`):

| Constant | Bucket |
| --- | --- |
| `AI_BUSY_MESSAGE` | `failed_external` |
| `AI_DAILY_QUOTA_MESSAGE` | `failed_external` |
| `PipelineTimeoutError.user_message` | `failed_external` |
| `AI_DEADLINE_MESSAGE` | `failed_config` |
| `AI_REJECTED_MESSAGE` | `failed_config` |
| `AI_OVERSIZED_VALUE_MESSAGE` | `failed_config` |
| `OcrUnavailableError.user_message` | `failed_config` |
| `EmptyExtractionError.user_message` | `failed_content` |
| `GENERIC_USER_MESSAGE` | `failed_unknown` |
| anything else | `failed_unknown` |

`is_failure(outcome)` is true for the four `failed_*` buckets only.

## Redis keys owned by the job system

| Key | Value | TTL | Set by | Read or cleared by |
| --- | --- | --- | --- | --- |
| `mrr:cancel:<job_id>` | `"1"` | `max(60, JOB_CANCEL_GRACE_SECONDS * 60)` seconds | `backend/app/worker/cancel.py` `request_cancel` | `is_cancel_requested`, `current_job_cancelled`; deleted by `clear_cancel` in `_finalize_cancelled` |

RQ's own queue, registry and scheduler keys are managed by the library. The queue names are
`segment`, `summarize`, `segment:<user_id>` and `summarize:<user_id>`.
