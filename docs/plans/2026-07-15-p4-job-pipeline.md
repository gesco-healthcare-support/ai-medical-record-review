---
feature: p4-job-pipeline
date: 2026-07-15
status: draft
base-branch: feat/nextjs-fastapi-rewrite
related-issues: []
---

## Goal
Replace the in-process job pool (`services/job_queue.py`, a 2-wide `ThreadPoolExecutor` in one
process) with **Redis + RQ workers (Path B)** so segmentation and summarization run concurrently
across processes, while the DB stays the source of truth for job status/progress and the
one-active-job-per-document invariant. Then wire the two routes deferred from P3
(`segment/start`, `summarize/start`) to enqueue.

## Context
Phase-0 porting spec section 4 is the redesign map. The Flask queue: a `threading.Lock`
serializes a check-then-insert (`active_job` None -> create Job), `target` is a closure capturing
`pdf_path`, `_run` drives the state machine + throttled DB progress, `sweep_orphans()` marks all
queued/running interrupted at boot. The classifier tier (segment_engine -> classification, which
lazily loads torch) was intentionally NOT ported in P3 - it lands here on the worker tier. The
single-slot `services/jobs.py` is dropped.

## Locked decisions (confirmed with Adrian, 2026-07-15)
- **Redis + RQ** (master plan). Enqueue a **serializable descriptor**
  (`document_id, kind, model, prompt_version, catalog_revision`) - NOT the closure; the worker
  resolves the document from the DB. **Redis carries non-PHI ids only**; persistence stays OFF.
- **Split queues (topology B, Adrian chose over the all-in-one recommendation):** a `segment`
  queue served by torch/classifier workers + a `summarize` queue served by torch-free workers.
  The job service routes by `kind`. **Both worker types need Tesseract + Poppler** (summarize OCRs
  pages too); only segment workers additionally need torch + the embedding model (`classifier`
  extra). Rationale for B: isolate the RAM-heavy classifier from the quota-bound summarize pool and
  scale summaries cheaply. Trade-off accepted: two worker pools + job routing to maintain.
- **One-active-job-per-doc across processes:** a **DB partial-unique index** on
  `jobs(document_id) WHERE state IN ('queued','running')` (the in-process lock cannot span
  processes). A racing second enqueue -> `IntegrityError` -> the existing **409**.
- **Heartbeat-aware orphan recovery** (RQ StartedJobRegistry / worker heartbeat), NOT
  mark-all-on-boot (that would kill healthy jobs on a rolling restart with N workers).
- **Worker code in `backend/app/worker/`** (one package; shares `app.models` + `app.services`).
- **Friendly job errors:** the worker stores a `user_message` on a failed job (reuse
  `app/errors.py` from P3b) + logs the technical detail server-side (ids-only); `/status` returns
  `job.error`. Preserves the P3b OCR fail-fast end to end.
- **Provenance** (`model, prompt_version, catalog_revision`) stamped at enqueue (carried in the
  descriptor) so a delayed job records the catalog version current when the user clicked.
- **Sub-phased** P4a then P4b, with a check-in.

## Approach
- **Port the classifier tier** to `backend/app/services` (needs the `classifier` extra):
  `segment_engine, classification, verify_pass, windows`, and `gemini`'s segmentation
  prompt/schema/parse. Rewire `config.X` -> `get_settings()`, the built client -> `genai_client`.
  `classification` keeps its per-process catalog cache + embedding matrix keyed on
  `catalog_version()`, but must read the catalog via an **explicit Session** (worker context), not
  Flask's `db.session` - the plan's "one crack". `reset_catalog_cache()` runs at worker startup.
- **RQ infra** (`app/worker/`): a Redis connection from `settings.redis_url`; two `Queue`s
  (`segment`, `summarize`); a worker entrypoint (`python -m app.worker <queue>`); serializable job
  functions `segment_document(job_id)` / `summarize_document(job_id)` that open their own Session,
  load the Job + Document, run the work, drive the state machine, and write throttled progress.
- **Job service** (`app/services/jobs.py`, replacing the Flask `job_queue`): `enqueue(session,
  document_id, kind, model, prompt_version, catalog_revision)` inserts the Job (state=queued) + sets
  `Document.status`, relying on the partial-unique index for the invariant (catch `IntegrityError`
  -> 409), then `queue.enqueue(fn, job_id)` on the kind's queue. `active_job()` + status helpers
  stay DB reads. The worker is the single writer of `Document.status` after enqueue.
- **Alembic migration**: the partial-unique index.
- **Wire routes** (P4b): re-add `segment/start` + `summarize/start` to the documents router as thin
  handlers that call `enqueue(...)` (routing by kind); `reprocess` (P5) will reuse the segment path.

### Alternatives rejected
- All-in-one workers (my recommendation): simpler + reversible, but Adrian chose B for RAM
  isolation + cheap summarize scaling.
- Celery: heavier than RQ for a single-box LAN deployment.
- Closures over descriptors: not serializable across processes; the worker rebuilds from the DB.
- In-process lock / app-lock: cannot span worker processes -> the DB index is the real guard.

## Tasks
- **P4a - infra + workers + invariant.** approach: tdd (the enqueue/409 invariant + state machine)
  + test-after (the ported classifier services).
  - files: app/services/{segment_engine,classification,verify_pass,windows,gemini}.py,
    app/services/jobs.py (enqueue + status), app/worker/{__init__,queues,jobs,__main__}.py,
    app/alembic/versions/<new>_one_active_job_index.py, pyproject (rq/redis already present).
  - acceptance: enqueuing two jobs for one document -> the second raises IntegrityError -> 409
    (tested against docker Postgres); a job function run directly (or via RQ SimpleWorker) drives
    queued->running->done and persists rows/summaries; a raised PipelineError -> job.state=error +
    a friendly user_message; classifier reads the catalog on an explicit session; ruff clean.
  - **CHECK-IN AFTER P4a.**
- **P4b - wiring + resilience.** approach: test-after.
  - files: app/api/documents.py (segment/start, summarize/start), app/worker/* (heartbeat orphan
    sweep), app/services/jobs.py (concurrency knobs).
  - acceptance: segment/start + summarize/start enqueue to the right queue + return 409 when a job
    is active; multiple jobs run concurrently across workers; a worker restart does NOT interrupt a
    healthy in-flight job (heartbeat recovery only reaps truly dead ones); concurrency capped to the
    Vertex quota; docs updated on provisioning Tesseract/Poppler (+ torch on segment workers).

## Risk / Rollback
- Blast radius: only `feat/nextjs-fastapi-rewrite`; `main` (Flask) unaffected.
- Rollback: revert P4 commits; P1-P3 stand (the documents API works; only the two job-start routes
  are inert until P4).
- Top risks: (a) Vertex 429 storms once N workers replace the 2-wide pool -> cap worker counts, keep
  genai_retry; (b) orphan recovery reaping healthy jobs -> heartbeat-based, not boot-based; (c) the
  partial-unique index semantics under concurrent enqueue -> a race test; (d) split-queue routing
  bugs (a job on the wrong queue) -> assert the queue per kind + a routing test.

## Verification
`docker compose up` (postgres + redis); `alembic upgrade head`; run a `segment` worker + a
`summarize` worker; enqueue via the routes; watch the DB status machine + progress; force a
concurrent enqueue (expect 409); kill/restart a worker mid-job (expect the healthy job survives).
Live AI paths verified by Adrian with Vertex ADC, as in prior phases.

## Open questions (resolve during the build)
- Exact worker process counts per queue vs the Vertex quota (measure; start conservative).
- Vertex auth on workers: ADC vs API key (same as the web tier; confirm at deploy).
- The LAN box's RAM headroom for the torch segment workers (informs the segment-worker count).
