---
feature: Pipeline forever-hang fix (concurrent-OCR deadlock + unbounded waits)
date: 2026-07-21
status: in-progress
base-branch: main
related-issues: []
---

## Goal

Every document either completes end-to-end (transient failures retried or degraded so work
finishes even if slower) or fails with a clear, friendly terminal error -- never a silent
forever-hang.

## Context & decisions

Why now: a 133pp record hung twice at `stage=verifying` (once ~16.5h, once until RQ's 1h
`job_timeout` SIGKILLed it). SIGKILL bypasses the error handler, leaving `documents.status`
`segmenting` + `jobs.state` `running` orphaned behind `uq_one_active_job_per_document`, wedging the
doc. Root cause (reproduced + proven on the server, HIGH confidence, supplied by Adrian -- not
re-derived here): a concurrent Tesseract/OpenMP deadlock in the verify pass. `verify_and_merge`
runs up to `classify_workers` (=4) concurrent `_same_document`, each of which OCRs two boundary
pages via `pytesseract.image_to_string` with no timeout; Tesseract 5.x is OpenMP-enabled and
`OMP_THREAD_LIMIT`/`OMP_NUM_THREADS` are unset, so concurrent tesseract procs deadlock on the CPU
and `subprocess.communicate()` blocks in `selectors.select()` forever. Isolation proof: 6-thread
OCR hammer -> OMP unset 0/60 in 55s (deadlock); `OMP_THREAD_LIMIT=1` -> 60/60 in 45s. A deadlocked
subprocess raises nothing, so retry never fires and `as_completed` (no timeout) waits forever. The
timeout-less Vertex call is a real latent risk but was NOT this hang.

Resolved Open Decisions:
- Decision: work on `W:\mrr-ai` `main` (da03691) because it is the up-to-date tree (includes #27
  + #28); `P:\...\mrr-line_source` and `feat/nextjs-fastapi-rewrite` are stale (pre-cutover) and
  get synced from main at deploy, not branched from.
- Decision: size the four `as_completed` timeouts as the SAME size-aware value as `job_timeout`
  (`max(job_timeout, pages*job_timeout_per_page)`) minus `future_timeout_margin_seconds` (=120),
  because it always fires just before RQ's SIGKILL yet scales with page count, so a genuinely large
  (2600pp) pool never trips falsely.
- Decision: a sustained Vertex outage on the NON-resumable paths (segmentation / verify /
  classify) uses bounded retry then fails clearly (bump `genai_max_retries` 6->8, keep honoring the
  server's `retryDelay`; on exhaustion the job ends with a friendly terminal message), NOT
  pause/resume, because pause/resume was scoped to summarize in item 7 and a global "keep-trying"
  budget in the shared `generate_with_retry` would weaken item 7's pause-after-N-consecutive logic.
- Decision: OMP env goes in the compose `x-backend-env` anchor (one place; harmless on api/web);
  HF-offline env + the baked model go in `backend/Dockerfile` (offline env is harmless on the
  torch-free images; the model is pre-downloaded only for the classifier build).
- Decision: a categorize-pool timeout degrades (unfinished rows get `DEFAULT_ID` + review flag +
  continue) rather than failing, because a row without a category breaks the downstream
  verify/merge invariant; a window-pool timeout fails terminally because a lost window is lost
  segmentation coverage.

## All needed context

Versions (HIGH): google-genai 2.11.0, rq 2.10, pytesseract 0.3.13+, httpx (TimeoutException <:
TransportError), python:3.12-slim base (tesseract-ocr + poppler-utils via apt). Tests run on the
HOST: `uv run --extra docs pytest` from `backend/` against docker-dev Postgres (localhost:5432,
`mrr/mrr_dev_only`) + Redis (localhost:6379). CI does NOT run `backend/tests`.

Verified anchors (all under `W:\mrr-ai\backend`):
- `app/config.py:44-55` -- `pipeline_workers`, `classify_workers=4`, `job_timeout=3600`,
  `job_timeout_per_page=20.0`, `genai_max_retries=6`, retry delays. Item-7 pause knobs at :57-62.
  `_derive` model_validator at :91.
- `app/services/ocr.py:30` -- `_ocr_image` calls `pytesseract.image_to_string(image)` (no timeout).
  `TesseractNotFoundError -> OcrUnavailableError` at :35. Per-page `_ocr_image` calls at :63 (in
  `extract_text_from_selected_pages`) and :77 (`extract_text_from_all_pages`) are OUTSIDE their
  try/except. `print()` at :60,:74. pytesseract's own timeout raises `RuntimeError('Tesseract
  process timeout')` (installed `pytesseract/pytesseract.py:152`; `image_to_string(..., timeout=)`
  at :473-484).
- `app/services/verify_pass.py:157-163` -- pool + `as_completed(futures)`/`future.result()` (no
  timeout). `_same_document` (:78) catches all -> returns False (keep boundary). `_boundary_text`
  (:46) catches all OCR -> degrades image-only. `print()` at :55,:120. Called only from
  `segment_engine.py:168`.
- `app/services/segment_engine.py:147-154` (window pool) + `:161-165` (categorize pool) -- both
  `as_completed`/`future.result()` no timeout. `_categorize` (:107) OCR-escalation `print()` at
  :117; verify-stats `print()` at :169. `run_segmentation(pdf_path, total_pages, progress)` has the
  page count.
- `app/worker/tasks.py:320-379` -- `summarize_document` pool; `as_completed(futures)` at :327 (no
  timeout); robust per-future transient/permanent handling already present; `should_pause` /
  `transient_left` -> `raise JobPaused` at :371. `_job_timeout(session, document_id)` at :95 uses
  `max(job_timeout, pages*job_timeout_per_page)`. `_run` generic handler at :74-85 sets
  `job.error = user_facing_message(exc)`.
- `app/services/genai_client.py:16-27` -- `@lru_cache get_genai_client` builds `genai.Client(...)`
  with no `http_options`; `types` NOT imported here. `HttpOptions.timeout: Optional[int]` =
  milliseconds (installed `google/genai/types.py:2490-2492`).
- `app/services/genai_retry.py:89-115` -- `generate_with_retry` already retries `ServerError`,
  429 (non-PerDay/free_tier, honoring `retryDelay`), and `httpx.TransportError`, then re-raises.
- `app/worker/failures.py:26-53` -- `classify_failure` (transient set mirrors the retry set) +
  `reason_for` (friendly permanent reasons). Item-7 signals `JobPaused`/`JobNeedsAttention`.
- `app/errors.py:9-48` -- `GENERIC_USER_MESSAGE`, `PipelineError` (+ `user_message`),
  `OcrUnavailableError`, `EmptyExtractionError`, `user_facing_message`.
- `app/services/classification.py:240-271` -- `classify()` returns `DEFAULT_ID` (from
  `app.services.taxonomy`, the catch-all) with `needs_review=True` on empty/no-signal. Lazy
  `SentenceTransformer('all-MiniLM-L6-v2')` at :179-181 (segment-worker only). `print()` at
  :234,:259.
- `app/worker/__main__.py` -- worker entrypoint; `Worker(...).work(with_scheduler=True)`; NO
  logging config (nothing app-level reaches stdout). `app/services/jobs.py:75` -- `enqueue` also
  computes the size-aware `job_timeout`.
- `docker-compose.yml:11-21` -- `x-backend-env` anchor merged into api / segment-worker /
  summarize-worker. `backend/Dockerfile` -- single stage on `python:3.12-slim`, `UV_EXTRAS` arg;
  the classifier extra (torch + sentence-transformers) is installed ONLY for `segment-worker`
  (image `mrr-backend-classifier`).

Known gotchas:
- `as_completed(fs, timeout=T)` measures T CUMULATIVELY from the call (an overall pool budget, not
  per-future) -- hence deriving from the whole-job size-aware budget. On timeout it raises
  `concurrent.futures.TimeoutError` (a builtin `TimeoutError` alias in 3.12); already-completed
  futures were already yielded.
- `future.cancel()` only cancels not-yet-started futures; a running one finishes on its own -- fine
  because OCR (T3) and the genai HTTP call (T6) are now individually bounded.
- Per-attempt `HttpOptions.timeout` x up to 8 attempts + backoff must stay under `pool_timeout`;
  true for realistic page counts (noted, not gated).
- `OcrUnavailableError` is a fail-fast CONFIG error (re-raised in `extract_text_from_selected_pages`
  :57); an OCR TIMEOUT must NOT be mapped to it (it is a per-page, skippable failure).

## Tasks (implementation blueprint)

### T1 -- Config knobs + shared size-aware helpers
- what: MODIFY `app/config.py`. Add `ocr_timeout_seconds: int = 120`,
  `genai_http_timeout_ms: int = 120000`, `future_timeout_margin_seconds: int = 120`; change
  `genai_max_retries` default `6 -> 8`. Add methods `effective_job_timeout(self, pages: int) -> int`
  (`max(self.job_timeout, int(pages * self.job_timeout_per_page))`) and
  `pool_timeout(self, pages: int) -> int` (`max(1, self.effective_job_timeout(pages) -
  self.future_timeout_margin_seconds)`).
- what: MODIFY `app/worker/tasks.py:_job_timeout` and `app/services/jobs.py:enqueue` to call
  `settings.effective_job_timeout(pages)` (dedupe the inlined formula).
- pattern: existing knobs `app/config.py:44-62`; existing formula `app/worker/tasks.py:95-99`.
- approach: code
- acceptance (EARS): The system shall expose `ocr_timeout_seconds=120`,
  `genai_http_timeout_ms=120000`, `future_timeout_margin_seconds=120`, and `genai_max_retries=8`.
  WHEN `pool_timeout(pages)` is called, THE SYSTEM SHALL return
  `effective_job_timeout(pages) - future_timeout_margin_seconds`, floored at 1. WHEN `enqueue` and
  `_job_timeout` compute a wall-clock cap, THE SYSTEM SHALL use `effective_job_timeout(pages)`.

### T2 -- OMP env in compose + concurrency-hammer proof
- what: MODIFY `docker-compose.yml` -- add `OMP_THREAD_LIMIT: "1"` and `OMP_NUM_THREADS: "1"` to the
  `x-backend-env` anchor (:11).
- what: CREATE `backend/scripts/dev/ocr_concurrency_hammer.py` -- rasterize ~8 pages @dpi120 from a
  PDF-path arg, submit ~60 `image_to_string` tasks across 6 threads, print `N/60 in Ss`, then
  `os._exit(0)`. Docstring: run inside the segment-worker container; OMP unset -> deadlock,
  `OMP_THREAD_LIMIT=1` -> completes.
- pattern: anchor env `docker-compose.yml:11-21`.
- approach: code
- acceptance (EARS): While a segment-worker or summarize-worker container runs, THE SYSTEM SHALL
  set `OMP_THREAD_LIMIT=1` and `OMP_NUM_THREADS=1`. WHEN the hammer runs with `OMP_THREAD_LIMIT=1`,
  THE SYSTEM SHALL complete 60/60 OCRs without deadlock.

### T3 -- Bounded OCR + per-page skip + logger
- what: MODIFY `app/services/ocr.py`. `_ocr_image`: pass
  `timeout=get_settings().ocr_timeout_seconds` to `image_to_string`; keep
  `TesseractNotFoundError -> OcrUnavailableError`; catch the timeout `RuntimeError`, log a warning,
  re-raise it (NOT as `OcrUnavailableError`). In `extract_text_from_selected_pages` and
  `extract_text_from_all_pages`, wrap the per-page `_ocr_image` call so a non-config OCR failure is
  logged and the page skipped (`continue`), while `OcrUnavailableError` still propagates. Add
  `logger = logging.getLogger(__name__)`; replace `print()` at :60,:74 with `logger.warning`.
- pattern: `_boundary_text` degrade `verify_pass.py:51-56`; existing per-page `continue`
  `ocr.py:59-61`.
- approach: tdd
- acceptance (EARS): WHEN `pytesseract.image_to_string` exceeds `ocr_timeout_seconds`, THE SYSTEM
  SHALL kill it (pytesseract subprocess timeout), log it, and raise a `RuntimeError` that is NOT an
  `OcrUnavailableError`. WHEN OCR of one page fails (timeout or other) in
  `extract_text_from_selected_pages` / `extract_text_from_all_pages`, THE SYSTEM SHALL log and skip
  that page and continue. Where Tesseract is missing, THE SYSTEM SHALL still raise
  `OcrUnavailableError` (fail-fast). The boundary path (`_boundary_text`) shall degrade image-only.
- tests: CREATE `tests/test_ocr.py` -- monkeypatch `pytesseract.image_to_string`: (a) asserts the
  `timeout=120` kwarg is forwarded; (b) raising `RuntimeError('Tesseract process timeout')` ->
  `_ocr_image` re-raises `RuntimeError` (not `OcrUnavailableError`); (c)
  `extract_text_from_selected_pages` skips the failing page and returns without raising (monkeypatch
  `_rasterize` to yield a dummy image); (d) `TesseractNotFoundError` -> `OcrUnavailableError`.

### T4 -- Pool-drain helper + terminal-timeout error
- what: CREATE `app/services/pools.py` -- `class PoolTimeout(Exception)` carrying
  `unfinished: list`; `def drain_pool(futures, timeout)` generator that yields futures as
  `as_completed(futures, timeout=timeout)` completes them and, on
  `concurrent.futures.TimeoutError`, cancels the unfinished futures and raises
  `PoolTimeout(unfinished)`. Works for a dict or list of futures.
- what: MODIFY `app/errors.py` -- add `class PipelineTimeoutError(PipelineError)` with a friendly
  `user_message` ("Processing took too long and was stopped. Please try again; if it keeps
  happening the document may be very large or the AI service may be busy.").
- pattern: `as_completed` loops at `verify_pass.py:161`, `segment_engine.py:152`;
  `OcrUnavailableError` subclass shape `app/errors.py:25-31`.
- approach: tdd
- acceptance (EARS): WHEN all futures finish within `timeout`, `drain_pool` SHALL yield each exactly
  once and raise nothing. WHEN `timeout` elapses with futures outstanding, `drain_pool` SHALL cancel
  the unfinished futures and raise `PoolTimeout` whose `unfinished` lists exactly those not yet
  completed. `PipelineTimeoutError` SHALL be a `PipelineError` whose `user_facing_message` is its
  friendly `user_message`.
- tests: CREATE `tests/test_pools.py` -- submit fast futures + one that sleeps; with a short
  `timeout`, assert the fast ones are yielded, `PoolTimeout` is raised, and `unfinished` holds the
  slow one; with a generous `timeout`, assert all yielded and no raise.

### T5 -- Wire drain_pool into the four pools
- what: MODIFY `app/services/verify_pass.py:verify_and_merge` -- add param
  `pool_timeout: float | None = None`; if None, `settings.pool_timeout(rows[-1]["end"] if rows else
  0)`. Drain via `drain_pool(futures, pool_timeout)`; on `PoolTimeout`, log a warning and leave
  those boundaries unverified (absent from `same_doc` -> split kept; never auto-merged). Add logger;
  replace `print()` at :55,:120.
- what: MODIFY `app/services/segment_engine.py:run_segmentation` -- compute
  `pool_timeout = settings.pool_timeout(total_pages)`. Window pool: `drain_pool`; on `PoolTimeout`,
  log + `raise PipelineTimeoutError(...)` (terminal). Categorize pool: build `{submit: row}`;
  `drain_pool`; on `PoolTimeout`, for each unfinished row set `row["category"] = DEFAULT_ID` and
  `row["flag"] = "x"`, log, and continue. Pass `pool_timeout` to `verify_and_merge`. Add logger;
  replace `print()` at :117,:169. Import `DEFAULT_ID` from `app.services.taxonomy`.
- what: MODIFY `app/worker/tasks.py:summarize_document` -- `pool_timeout =
  settings.pool_timeout(document.page_count)`; drain via `drain_pool(futures, pool_timeout)`; on
  `PoolTimeout`, set `transient_left = True` and `should_pause = True`, log, and let the existing
  post-loop `raise JobPaused` pause + auto-resume the outstanding rows.
- pattern: existing pool bodies `verify_pass.py:157-163`, `segment_engine.py:147-165`,
  `tasks.py:320-366`; `classify` default `classification.py:254`.
- approach: tdd
- acceptance (EARS): WHEN the verify pool exceeds `pool_timeout`, THE SYSTEM SHALL cancel the rest
  and keep the unverified boundaries split. WHEN the window pool exceeds `pool_timeout`, THE SYSTEM
  SHALL fail the job with `PipelineTimeoutError`. WHEN the categorize pool exceeds `pool_timeout`,
  THE SYSTEM SHALL assign `DEFAULT_ID` + review flag to unfinished rows and continue. WHEN the
  summarize pool exceeds `pool_timeout`, THE SYSTEM SHALL pause and auto-resume the outstanding rows.
- tests: CREATE `tests/test_pool_wiring.py` -- monkeypatch the inner callables to sleep and
  `settings.pool_timeout` tiny: (a) `verify_and_merge` returns without raising and adds no
  `suggest_merge` for unverified boundaries; (b) `run_segmentation` raises `PipelineTimeoutError`
  on a window stall (monkeypatch `byte_budgeted_windows` to return >=2 windows and `_window_rows`
  to sleep; `verify_merge=False`); (c) categorize stall -> rows carry `DEFAULT_ID` + `flag=="x"`.
  EXTEND `tests/test_jobs.py` -- summarize pool stall (monkeypatch `summarize_row` to sleep) raises
  `JobPaused` and schedules a resume.

### T6 -- Vertex HTTP timeout
- what: MODIFY `app/services/genai_client.py` -- `from google.genai import types`; pass
  `http_options=types.HttpOptions(timeout=settings.genai_http_timeout_ms)` to every `genai.Client(...)`
  branch.
- pattern: existing client construction `genai_client.py:19-27`; retry already catches the raised
  timeout `genai_retry.py:111`.
- approach: test-after
- acceptance (EARS): WHEN `get_genai_client` builds a client, THE SYSTEM SHALL set
  `HttpOptions.timeout = genai_http_timeout_ms` (120000 ms). If a Vertex call stalls past that, then
  httpx raises a `TimeoutException` (<: `TransportError`) that `generate_with_retry` retries.
- tests: EXTEND `tests/test_genai_retry.py` or CREATE `tests/test_genai_client.py` -- assert the
  constructed client carries `http_options.timeout == 120000` for the ADC and api-key branches
  (monkeypatch `genai.Client` to capture kwargs; clear the `lru_cache`).

### T7 -- Retriable-vs-terminal friendly messages
- what: MODIFY `app/errors.py` -- add module constants `AI_BUSY_MESSAGE`,
  `AI_DAILY_QUOTA_MESSAGE`, `AI_REJECTED_MESSAGE`, and `def genai_user_message(exc) -> str | None`
  (ServerError or retryable-429 -> busy; PerDay/free_tier -> daily quota; other ClientError ->
  rejected; else None). `user_facing_message`: `PipelineError -> user_message`; elif
  `genai_user_message(exc)`; else `GENERIC_USER_MESSAGE`.
- what: MODIFY `app/worker/failures.py:reason_for` -- reuse the shared constants / delegate the
  genai cases to `genai_user_message` (single source of the phrasing). Confirm `classify_failure`
  already scores an httpx timeout transient (it does: `TimeoutException <: TransportError`).
- pattern: `reason_for` `failures.py:45-53`; `_is_daily_quota` `failures.py:20-23`.
- approach: tdd
- acceptance (EARS): WHEN a job fails on a genai `ServerError` or a retryable 429, THE SYSTEM SHALL
  show the "AI service was busy" message (not the generic one). WHEN it fails on the per-day quota,
  THE SYSTEM SHALL show the daily-quota message. WHEN it fails on another `ClientError`, THE SYSTEM
  SHALL show the rejected message. `reason_for` and `user_facing_message` SHALL return identical
  text for the same genai error.
- tests: EXTEND `tests/test_failures.py` -- `user_facing_message` maps ServerError/429/PerDay/auth
  to the shared constants; `reason_for` and `user_facing_message` agree; `classify_failure` scores
  `httpx.ReadTimeout` transient.

### T8 -- Structured stdout logging in the worker
- what: MODIFY `app/worker/__main__.py` -- before starting the worker, configure logging to stdout
  at INFO with a greppable format including `%(asctime)s %(levelname)s %(name)s %(message)s`
  (`logging.basicConfig(level=logging.INFO, stream=sys.stdout, ...)`; leave RQ's own handlers).
  Ensure the pipeline log lines carry `document_id`/`job_id` (ids only, never PHI) -- extend the
  existing per-stage / per-row `logger.info`/`warning` calls in `tasks.py`, and add per-stage
  start/finish INFO logs in `run_segmentation` and `verify_and_merge` keyed by the ids passed
  through `progress`/params. Replace any remaining `print()` in `ocr.py`, `verify_pass.py`,
  `segment_engine.py`, `classification.py` with the module logger.
- pattern: existing `logger.info(... job %s ... document %s ...)` `tasks.py:134-141,156`.
- approach: code
- acceptance (EARS): While a worker runs, THE SYSTEM SHALL emit INFO logs to stdout (visible in
  `docker logs`) for each stage start/finish and each terminal error, each carrying `document_id`
  and `job_id` and never any PHI. The codebase SHALL contain no `print()` in the four pipeline
  service modules.
- tests: none (logging config; validated by import smoke + `docker logs` at end-verification).

### T9 -- HF offline + baked embedding model
- what: MODIFY `backend/Dockerfile` -- set `ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
  HF_HOME=/opt/hf-cache`; after `uv sync`, add a RUN that -- only when `UV_EXTRAS` contains
  `classifier` -- pre-downloads the model into `HF_HOME` with network still available
  (`HF_HUB_OFFLINE=0 python -c "from sentence_transformers import SentenceTransformer;
  SentenceTransformer('all-MiniLM-L6-v2')"`), guarded via `case "$UV_EXTRAS" in *classifier*) ...
  ;; esac`. Runtime env stays offline so no container reaches huggingface.co.
- pattern: existing `UV_EXTRAS` arg + `uv sync` `backend/Dockerfile:24-29`; model name
  `classification.py:28`.
- approach: code
- acceptance (EARS): WHERE the classifier extra is installed (segment-worker image), THE SYSTEM
  SHALL contain the `all-MiniLM-L6-v2` model under `HF_HOME` at build time and load it at runtime
  with `HF_HUB_OFFLINE=1` without any network call. WHERE the classifier extra is absent (api /
  summarize images), the offline env SHALL be harmless (those tiers never import
  sentence-transformers).
- tests: none (build-time; validated by the docker build + a no-network classify in the validation
  loop).

## Validation loop

Run from `W:\mrr-ai\backend` unless noted (docker-dev Postgres:5432 + Redis:6379 must be up):

1. Format + lint (must match CI ruff 0.15.21):
   `uvx ruff@0.15.21 format . && uvx ruff@0.15.21 check .`
2. Backend suite (all green, including the new tests):
   `uv run --extra docs pytest -q`
3. Import smoke:
   `uv run --extra docs python -c "import app.main, app.worker.tasks, app.services.pools, app.services.ocr, app.services.segment_engine, app.services.verify_pass, app.services.genai_client"`
4. Compose validity + worker image builds (authoritative for T2/T9; Linux build):
   `cd /w/mrr-ai && docker compose config >/dev/null && docker compose build segment-worker summarize-worker api`
5. Offline-model proof (T9), inside the built classifier image:
   `docker compose run --rm --no-deps -e HF_HUB_OFFLINE=1 segment-worker python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); print('offline load OK')"`
6. Concurrency-hammer proof (T2), inside the segment-worker container against a real PDF in
   `/app/uploads`: run `scripts/dev/ocr_concurrency_hammer.py` once with `OMP_THREAD_LIMIT` unset
   (expect deadlock/low count) and once with `OMP_THREAD_LIMIT=1` (expect 60/60). Deferred to
   end-verification on the box (needs real tesseract + a sample PDF).

## Risk / rollback

Blast radius: the entire worker pipeline (segment / classify / verify / summarize), the shared
genai client, compose env, and the backend image build. Changes are additive (timeouts, env, a new
helper module, friendly-message mapping) plus one retry-count bump; no schema change, no migration.
The item-7 summarize state machine is preserved (summarize-pool timeout feeds the existing
pause/resume). Every new bound is an env-overridable config knob, so a misfire is tuned via `.env`
without a code change.
Rollback: `git revert` the feature branch (or redeploy the prior image tag). Reverting restores the
pre-fix behavior exactly (the deadlock returns, but nothing else regresses).
