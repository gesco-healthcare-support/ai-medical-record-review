---
feature: p3-documents-api
date: 2026-07-15
status: in-progress
base-branch: feat/nextjs-fastapi-rewrite
related-issues: []
---

## Goal
Port the 15-route `/api/documents` surface from the Flask `documents_api` blueprint to a FastAPI
router, wired to the (Flask-free) ported services, preserving the owner-scoped 404 IDOR guard, the
one-active-job 409 guards, and the exact request/response contracts the (future) frontend consumes.

## Context
Phase-0 porting spec (`2026-07-14-porting-spec.md`, sections 1 + 6) is the map. The documents API
is the heart of the multi-user flow; the `services/` layer it calls is deliberately Flask-free and
ports nearly unchanged. The bulk of P3 is bringing the services + model helper methods over and
rewiring Flask couplings (`db.session`, `mrr_ai.config`) to the backend equivalents.

## Locked decisions (confirmed with Adrian, 2026-07-15)
- **The 2 job-start routes (`segment/start`, `summarize/start`) are DEFERRED to P4** (they are thin
  handlers whose body IS the RQ queue redesign). P3 ships the other 13 routes.
- **Dependency extras split**: a `docs` extra (pypdf, pymupdf, pdf2image, pytesseract, python-docx,
  google-genai, numpy) for the web tier; a `classifier` extra (torch, sentence-transformers) only
  workers add. Verified: `torch` (via `classification`) is imported ONLY by `segment_engine` (the
  P4 worker); `summarize_engine`/`bundles` don't touch it, so the web tier stays torch-free.
- **Sub-phased**: P3a (foundations: services + model methods + helpers + dep split), then a
  check-in, then P3b (router + Pydantic + ownership dep + tests).
- **Sync domain layer**: the documents routes are sync `def` handlers on the **sync** session
  (`get_db`); FastAPI runs sync handlers in its threadpool, so blocking IO / the sync-AI calls
  (`resummarize`, `bundle/summarize`) need no explicit `run_in_threadpool`. (Auth stays async per
  FastAPI-Users; both hit the same Postgres via the separate sync/async engines already in db.py.)

## Approach
- **Services port** (`backend/app/services/`): copy the Flask-free services the router calls -
  `pdf, ocr, gemini, genai_retry, summarize_engine, bundles, files, audit, catalog, prompts,
  taxonomy` - rewiring `mrr_ai.config` -> `app.config`, `mrr_ai.models` -> `app.models`, and
  `mrr_ai.extensions.db.session` -> an explicit `Session` argument. `catalog` reads the DB catalog
  (get_prompt / get_category_options / catalog_version) -> takes a `Session` param (the plan's "one
  crack"). `classification`/`segment_engine` are NOT ported here (P4 worker only).
- **Config mapping**: the router/services reference `GENAI_MODEL, SUMMARY_MODEL,
  BUNDLE_SUMMARIZE_CAP, UPLOAD_FOLDER, PROMPT_VERSION`. `app.config.Settings` already has
  `genai_model, summary_model, bundle_summarize_cap, upload_folder`; `PROMPT_VERSION` lives in the
  ported `gemini` service.
- **Model helper methods** (port to `app/models.py`): `Document.listing()`/`active_job`,
  `Job.progress()`, `SegmentRow.as_row()`/`ReviewRow.as_row()`,
  `Summary.listing()`/`effective_title/date/text()`, plus any other `listing()`.
- **Extracted helpers**: `validate_rows` (from `review_api`) -> `app/services/rows.py` (framework
  -free); `_build_mrr_document`/`_download_name`/`_export_entry` (from the `export` blueprint) ->
  `app/services/reporting.py`.
- **Router** (`app/api/documents.py`): 13 routes, `APIRouter(prefix="/api/documents")`, a shared
  `get_owned_document` dependency (id + current_active_user -> 404), Pydantic bodies (RowsPayload,
  SummaryEditPayload, ExportPayload, BundlePayload), file responses via
  `FileResponse`/`StreamingResponse(BytesIO)`, upload via `UploadFile` (field `pdf`). Wire into
  `main.py`.

### Alternatives rejected
- Stub / temp-sync the job routes in P3 -> throwaway code + torch on the web tier (rejected; defer).
- Single `pipeline` extra on the web tier -> pulls torch into web + CI (rejected; split).
- Async domain routes -> the services are sync; sync handlers + threadpool is simpler + faithful.

## Tasks
- **P3a - foundations.** approach: test-after.
  - files: pyproject.toml (extras split), app/models.py (helper methods), app/services/{pdf,ocr,
    gemini,genai_retry,summarize_engine,bundles,files,audit,catalog,prompts,taxonomy}.py,
    app/services/rows.py (validate_rows), app/services/reporting.py.
  - acceptance: `uv sync --extra docs` installs without torch; `import app.services.*` builds;
    `configure_mappers()` still maps; unit tests for the pure helpers (validate_rows, model
    methods, `_export_entry` tag recomposition, `_download_name`) pass; ruff clean.
  - **CHECK-IN AFTER P3a.**
- **P3b - the router.** approach: test-after.
  - files: app/api/documents.py, app/api/deps.py (get_owned_document), app/schemas/documents.py,
    app/main.py.
  - acceptance: non-owner -> 404 on every id route; upload/list/get/delete round-trip; rows PUT
    validation (bad range -> 400); summaries GET/PUT; export returns a docx; bundle/pdf returns a
    PDF; 409 guards fire when a job row is active; sync-AI routes (resummarize, bundle/summarize)
    tested with the Vertex boundary mocked (live Vertex verify is Adrian's, ADC-gated).

## Risk / Rollback
- Blast radius: only `feat/nextjs-fastapi-rewrite`; `main` (Flask) unaffected.
- Rollback: revert P3 commits; P1/P2 stand.
- Top risks: (a) catalog/session re-seat subtly changing behavior -> port verbatim, pass a Session;
  (b) `send_file` -> FastAPI response nuances (conditional/range for PDF view) -> use FileResponse;
  (c) sync/async session mixing -> domain uses get_db (sync) exclusively, auth uses async only.

## Verification
`uv sync --extra docs`; ruff + import smoke; `pytest` (unit for P3a, integration for P3b) on the
docker Postgres. Live AI paths (segment is P4; summarize/resummarize/bundle-summarize) verified by
Adrian with Vertex ADC, as in prior phases.
