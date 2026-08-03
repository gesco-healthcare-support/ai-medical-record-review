# Plan: stop, interrupt and restart a running job

Status: researched, nothing implemented. Baseline `origin/main` `a40c85c`.
Read-only investigation, **zero Vertex calls used**.

Covers the "General 1" request. The Summaries and Duplicates items from the same list
are already planned in `docs/plans/2026-07-30-summaries-category-and-duplicates.md`
(including the confirmed alocker findings) and are not repeated here.

---

## 1. What exists today

There is no cancel or stop anywhere in the API. But the resumability story is already
uneven, and that unevenness decides how much work each kind needs.

| Kind | Start endpoint | Progress persisted during the run? | Can resume today | Can start fresh today |
| --- | --- | --- | --- | --- |
| **segment** | `POST /{id}/segment/start` | **No** | No | Always fresh |
| **classify** | chained from the aggregate merge (`documents.py:251`) | **Yes**, per row | No | Always fresh |
| **dedup** | `POST /{id}/dedup/start` | Partial: per-row OCR yes, clustering no | OCR only | — |
| **summarize** | `POST /{id}/summarize/start` | **Yes**, per row | **Yes** | **Yes** (`fresh: true`) |

### Why the difference

`_run()` (`worker/tasks.py:40`) hands `work()` a `report(stage, current, total)` that calls
`session.commit()` on every write. Ticks are throttled by `_PROGRESS_MIN_INTERVAL`, but a
stage change always writes. **So anything `work()` mutated before a tick is committed.**
On failure `_run` does `session.rollback()` -- uncommitted work is discarded, committed
work survives.

That gives:

- **`classify_document`** sets `row.category`, `row.include`, `row.flag` inside a loop
  that calls `report()` each iteration, so **per-row results are already committed
  incrementally**. The data to resume from exists; nothing reads it.
- **`segment_document`** calls `run_segmentation(...)` to completion *before* writing
  anything, then deletes and rewrites all rows in one transaction. **An interruption
  loses the entire run**, however far it got.
- **`summarize_document`** already persists per row and already distinguishes resume from
  fresh: `payload.fresh` deletes prior summaries, otherwise "the resumable worker
  reuses done rows by identity" (`documents.py:603-608`).

### Machinery already in place worth reusing

- `JobPaused` and `JobNeedsAttention` are cooperative signals raised by `work()` and
  handled by `_finalize_paused` / `_finalize_needs_attention`. **A cancel should be a
  third signal in exactly this shape**, not a new mechanism.
- `_finalize_paused` reschedules the same job with `enqueue_in` onto the same per-user
  lane, and records the new `rq_job_id`.
- `recover_orphans` (`worker/recovery.py:24`) already reaps DB jobs whose RQ counterpart
  is gone, correlating by the *current* `rq_job_id`.
- `ACTIVE_STATES = ("queued", "running", "paused")` and `active_job()` enforce one active
  job per document.
- RQ 2.10.0 provides `send_stop_job_command` (verified installed).

---

## 2. Design

### 2.1 Stopping needs two paths, not one

A cooperative flag alone is not enough. The jobs users actually want to kill are the ones
wedged in Vertex backoff, and those do not reach a progress tick for up to ~17 minutes
(8 retries with jitter, plus `rate_limit.acquire()` waits). A stop button that takes a
quarter of an hour to respond will read as broken.

**Path A -- cooperative (primary).**
- Add `cancel_requested: bool` to `Job`.
- `report()` checks it and raises `JobCancelled`.
- **Also check it inside `generate_with_retry`'s backoff sleep** (`services/genai_retry.py`).
  This is the important one: it converts "stuck for 17 minutes" into "stops within one
  backoff interval". Pass a cancel-check callable down, or have the retry loop consult a
  cheap Redis key set by the API so it needs no DB session.
- `_run` catches `JobCancelled` and calls a new `_finalize_cancelled`, mirroring
  `_finalize_paused`.

**Path B -- hard stop (fallback).**
- If the job has not acknowledged within a short grace period, call
  `send_stop_job_command(redis, job.rq_job_id)`.
- The existing `recover_orphans` then reaps the row on the next pass.
- Expose this as the second press of the button ("Force stop"), not as the default.

### 2.2 New job state

Add `cancelled` as a terminal state, distinct from the existing ones:

- `error` -- the pipeline failed
- `interrupted` -- the system lost the job (dispatch failure, orphan reap)
- `cancelled` -- **the user asked for it**

Keeping them separate matters: `interrupted` is a fault to investigate, `cancelled` is
not, and lumping them together makes the failure metrics lie.

`ACTIVE_STATES` is unchanged -- a cancelled job is not active, so `active_job()` frees
the document for a restart immediately.

### 2.3 The restart choice, per kind

The request is "start from scratch, or restart with current progress". What that can mean
differs by kind, and pretending otherwise would ship a button that silently does nothing.

| Kind | "Start over" | "Continue" | Work needed |
| --- | --- | --- | --- |
| summarize | `fresh: true` -- **exists** | default start -- **exists** | Surface both after a cancel |
| classify | default -- **exists** | Skip already-classified rows | Per-row `classified_at` marker |
| segment | clears checkpoints | Skip completed windows and rows | **New checkpoint table** |
| dedup | re-OCR everything | reuse `source_text` -- **exists** | Surface it |

All four are in scope for the first change (decided 2026-07-31), so every kind offers a
real Continue rather than a disabled button.

**classify.** Rows already carry their committed category, but nothing distinguishes "the
classifier set this" from "it was seeded by segmentation". Add a nullable
`classified_at` timestamp on `ReviewRow`, set as each row is classified; on continue,
skip rows that have one. Small, and it also gives the UI something honest to show.

**segment. In scope (decided 2026-07-31): full window checkpointing.** This is the only
genuinely new persistence, and it is the highest-value resume of the four, because
segmentation is the stage that keeps dying on quota -- three consecutive failures at 2/3
windows, each burning 10-18 minutes and producing nothing.

`run_segmentation` (`services/segment_engine.py:130`) has **two checkpointable phases**,
both of which spend Vertex calls:

| Phase | Unit of work | Pool | Cost if lost |
| --- | --- | --- | --- |
| `segmenting` | one window -> `_window_rows`, one Vertex call each | `segment_window_workers` | the expensive one |
| `categorizing` | one row -> `_categorize`, one Vertex call each | `classify_workers` | also real |

Checkpoint both.

#### Window plan signature -- the part that makes it correct

Windows come from `byte_budgeted_windows(pdf_path, total_pages, window_overlap,
window_budget_mb)`. They are a deterministic function of the PDF plus two config values,
so a cached window result is only valid while all of those are unchanged. Store a
signature and invalidate on mismatch:

```
plan_signature = hash(document.sha256, total_pages, window_overlap, window_budget_mb)
```

`Document.sha256` already exists. Without this, lowering `SEGMENT_WINDOW_WORKERS` or
`window_budget_mb` -- which the worker/queue plan is doing right now -- would silently
reuse results computed under a different window layout and produce a corrupt merge.

#### Storage

New table, keyed on the **document**, not the job:

```
segment_checkpoint(document_id, plan_signature, phase, unit_key, payload_json, created_at)
```

Keying on the document is the whole point: a restart creates a *new* job, and it must be
able to pick up the previous job's completed windows. `unit_key` is the window index for
phase 1 and the `(start, end)` page range for phase 2.

- Written as each unit completes, inside the existing `drain_pool` loop where `done` is
  already incremented for progress.
- Read at the start of a continue run: skip units already present, submit only the rest.
- **Deleted on successful completion** of the segment job, and on an explicit "Start
  over", so checkpoints never outlive their usefulness.

#### Interaction with the pool timeout

`drain_pool` raises `PoolTimeout` and segmentation treats a lost window as terminal
(`PipelineTimeoutError`) because "a lost window is lost coverage". With checkpointing that
becomes recoverable: the finished windows are already persisted, so a continue picks up
exactly the unfinished ones. This is a real improvement to an existing failure mode, not
only to user-initiated cancels.

### 2.4 API

```
POST /documents/{id}/jobs/{job_id}/cancel      {force: bool = false}
```

- 404 if the job is not on this document; 409 if it is already terminal.
- Sets `cancel_requested`, and on `force: true` also issues `send_stop_job_command`.
- Returns the job so the UI can poll it to `cancelled`.

Restart reuses the existing per-kind start endpoints. `summarize/start` already takes
`fresh`; add the same flag to `segment/start` and `dedup/start` for symmetry (segment
ignores it until checkpointing lands, and should say so rather than pretend).

### 2.5 UI

`progress-panel.tsx` gains a Stop control while a job is active, and after cancellation
offers two buttons: **Continue** and **Start over**. Where continue is not yet possible
(segment), show only Start over rather than a disabled control with no explanation.

---

## 3. Risks and the one that will corrupt data if missed

**Never cancel between the delete and the insert.** `segment_document` runs
`DELETE FROM review_rows` and then re-adds every row inside one `work()` call. A cancel
check placed in the write phase could commit a document with **zero rows**, destroying the
reviewer's page ranges. The cooperative check must fire only during the compute phase --
i.e. inside `run_segmentation` and the retry backoff, never between the delete and the
final commit. This is the single most important constraint in this plan.

Others:

- **A cancelled summarize may have a scheduled resume already queued.** `_finalize_paused`
  uses `enqueue_in`; cancelling must also cancel that scheduled RQ job, or the run
  reappears minutes later. Look up `job.rq_job_id` -- it points at the *scheduled* job
  after a pause, not the original.
- **`document.status` must be set coherently** on cancel, or the document strands in an
  in-flight status with no active job. Add a `STATUS_ON_CANCEL` map beside the existing
  `STATUS_ON_ENQUEUE` / `STATUS_ON_DONE`.
- **Per-user queues have landed** (`queue_for(job.kind, owner)`), so a restart must
  re-enqueue onto the owner's lane, not the base queue -- `_finalize_paused` already shows
  the correct pattern.
- **Races**: a job can finish between the cancel request and the worker's next check. The
  cancel must be a no-op against a terminal job, not an error.

---

## 4. Validation

```bash
docker compose -f docker-compose.dev.yml up -d postgres redis
cd /c/src/mrr-ai && set -a; . ./.env; set +a
export DATABASE_URL="postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr"
cd backend && uv sync --extra docs && uv run alembic upgrade head
uv run pytest -q && uv run ruff check app/ && uv run ruff format --check app/
```

Targeted: `tests/test_jobs.py`, `tests/test_documents_api.py`, and whichever module
covers `recover_orphans`.

Tests worth writing, none of which need Vertex:

1. Cancelling a queued job never starts it.
2. Cancelling a running job leaves `state='cancelled'`, a coherent `document.status`, and
   **no partial row wipe** -- assert the review rows are unchanged after cancelling a
   segment job mid-compute.
3. Cancelling a paused summarize also cancels its scheduled resume.
4. Continue-after-cancel on summarize regenerates only the missing rows.
5. Cancel against an already-finished job is a no-op, not a 500.

Migrations required: `Job.cancel_requested`, `ReviewRow.classified_at`, and the new
`segment_checkpoint` table. The `cancelled` state needs no migration -- `Job.state` is a
free-form `String`, not an enum.

Two more tests the checkpointing specifically needs:

6. A continue after cancelling a segment job re-runs **only** the unfinished windows --
   assert the completed ones are not recomputed.
7. Changing `window_overlap` or `window_budget_mb` invalidates the checkpoint, so a
   continue recomputes from scratch rather than merging results from two different window
   layouts. This is the one that silently corrupts output if the signature is wrong.

---

## 5. Decisions (all resolved 2026-07-31, none open)

| # | Question | Decision |
| --- | --- | --- |
| 1 | Ship order / scope | **Everything in one change**: cancel for all four kinds, plus a real Continue for each, including segment window and row checkpointing |
| 2 | Does segment checkpointing justify its cost now? | **Yes.** It is the stage that fails most, and checkpointing also makes the existing `PoolTimeout` terminal-failure recoverable |
| 3 | Grace period before force stop | **10 seconds**, config-tunable |
| 4 | Is a cancelled job's partial output visible? | **Visible.** It is already committed, and hiding completed work would be the surprising choice. `STATUS_ON_CANCEL` must leave the document in a status that renders those partial results |

### One consequence of deciding scope this way

Doing segment checkpointing in the same change means this lands **after** the
worker/queue plan (`VERTEX_MAX_RPM=20`, `SEGMENT_WINDOW_WORKERS=1`), which is already in
flight. That ordering is fortunate rather than a problem: those settings change
`window_budget_mb`-adjacent behaviour and the concurrency of the very pool being
checkpointed, so building against the settled values avoids designing for a moving target.

Sequence the work inside the change so the stop button is usable early:

1. `cancel_requested` + `cancelled` state + `_finalize_cancelled` + the cooperative check
   in `report()` and in `generate_with_retry`'s backoff. **The button works from here**,
   with Continue available on summarize immediately (it already exists) and on dedup.
2. `classified_at` marker -> classify Continue.
3. `segment_checkpoint` table, plan signature, both phases -> segment Continue.
4. UI: Stop, then Continue / Start over.

Steps 1 and 4 could ship as one PR and 2-3 as a second if review size becomes a problem;
that is a packaging choice, not a scope change.
