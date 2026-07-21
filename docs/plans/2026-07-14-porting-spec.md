# Porting spec: Flask -> Next.js + FastAPI (all phases)

Companion to `2026-07-14-nextjs-fastapi-rewrite.md` (the phased plan). This is the **read-only
map of the current app** produced by a 6-agent research pass, the reference for every phase.
Full per-endpoint/per-column/per-service detail (the raw agent output) is in
`2026-07-14-porting-spec.raw.json` (~169 KB); this file is the synthesis.

Disposition legend: **port-as-is** (Flask-free Python -> FastAPI unchanged) | **rewrite**
(HTTP/auth/UI re-implemented) | **redesign** (job pipeline -> Redis/RQ) | **drop** (classic
single-user path, `state.py`-based, not worth porting).

---

## 1. Endpoints (FastAPI-router target)

59 blueprint routes across two disjoint surfaces. **Keep (rewrite):** `documents_api` (15
routes, `/api/documents`, every handler owner-scoped via `_own()` -> 404-on-miss IDOR guard) +
`admin_api` (7 routes, `/api/admin`, `is_admin`-gated) + the pages.py SPA-shell routes (`/`,
`/review/<id>`, `/diagnostics`, `/depositions`, `/admin`). **Drop (classic, state.py-based, no
front-end consumer):** `review_api` (`/api/pdf`, `/api/segment/*`, `/api/summarize/*`), `upload`,
`summarize` (OpenAI), `reports`, `extraction`, `individual_mrr`, `export` classic routes,
`segmentation`, classic pages + `/reset`.

Porting notes:
- Two FastAPI routers (documents, admin) cover the whole keep-set + Pydantic request models
  (RowsPayload, SummarizeStartPayload, SummaryEditPayload, ExportPayload, BundlePayload,
  CategoryCreate/Update, PromptPut). `validate_rows` is framework-agnostic -> port-as-is.
- Auth as FastAPI **dependencies/middleware**, not per-route decorators (preserve deny-by-default).
- `_own(document_id)` (filter by id AND user_id -> **404**, never 403) -> a shared
  `get_owned_document` dependency; every documents route depends on it. Admin reprocess
  intentionally bypasses owner-scoping (is_admin, any owner) - preserve that asymmetry.
- File responses: Flask `send_file` -> FastAPI `FileResponse`/`StreamingResponse(BytesIO)`;
  multipart upload field is named `pdf` -> `UploadFile`.
- `segment/start`, `summarize/start`, `admin reprocess` stay thin POST handlers whose body
  is the queue redesign (P4). `resummarize` + `bundle/summarize` are synchronous today ->
  `run_in_threadpool` or fold onto the queue.
- Shared `_build_mrr_document` + `_download_name` (docx/regex, no Flask) -> a reporting service.

Open: confirm `review_api`/`/classic` have zero live consumers before removing (research says
none). CSRF transport (cookie double-submit vs bearer) - RESOLVED: same-origin cookie.

## 2. Data model (reuse the DB; now migrating to Postgres)

11 models. Nine domain (Document, Job, SegmentRow, ReviewRow, Summary, Category, Prompt,
CatalogMeta, AuditLog) are plain SQLAlchemy -> **port-as-is** (done in P1a). User/Role/roles_users
are Flask-Security `fsqla_v3` -> **rewrite mirroring the exact columns** (done in P1a; introspected
+ mirrored). Invariants to uphold: Document.status single-writer (the job service); at-most-one
active job per document; SegmentRow (immutable model output, provenance = model+prompt_version+
catalog_revision) vs ReviewRow (mutable human label) is the fine-tuning flywheel; Summary keeps
raw text immutable + `edited_*` overrides + `source_text` (fine-tuning input, PHI); Category ids
immutable strings with active/auto_assign; CatalogMeta single-row monotonic revision.

Porting notes / gotchas (mostly handled in P1a/P1b):
- Keep table/column names identical (`user`, `role`, `roles_users`, domain names) for the data
  migration. fsqla columns mirrored exactly (incl. fs_uniquifier, password, MFA/us_/tf_).
- Re-apply the SQLite PRAGMA equivalents? -> N/A on Postgres; Postgres handles concurrent writers
  (the whole reason for the DB switch). One process runs Alembic/seed at startup, not every worker.
- `injury_date` widened String(16)->Text (SQLite ignored the length; real multi-DOI = 22 chars).
- Category.examples JSON; datetimes; keep seed-if-empty parity (id 6 auto_assign=False, id 11 no
  prompt -> falls back to 100). Session-per-request (Depends) for web; per-process for workers.

## 3. Auth / session (rewrite; P2)

Flask-Security-Too 5.8: open self-registration, session-cookie login, CSRF, argon2id passwords
salted via SECURITY_PASSWORD_SALT, a custom 8+/digit/symbol rule + required `name` field. One
app-level **before_request deny-by-default gate** (not per-route); `/admin`+`/api/admin` add an
is_admin check. Owner-checks are IDOR-safe 404s.

RESOLVED (P2a probe): passwords are **`argon2id(base64(HMAC_SHA512(SECURITY_PASSWORD_SALT,
password)))`** (params m=65536,t=3,p=4). raw-pw argon2 verify MISMATCHES; the HMAC form MATCHES.
Reproduce exactly; the backend needs the REAL salt from the Flask .env at cutover so adriang's
migrated hash verifies (dev uses a dummy salt).

Decisions (locked): same-origin + HttpOnly signed cookie session (holds `fs_uniquifier`); keep
argon2id + fs_uniquifier so logins survive; reproduce CSRF double-submit (XSRF-TOKEN cookie +
X-XSRF-Token header); global deny-by-default via FastAPI middleware + require_admin dep;
content-negotiate 401-JSON vs 302-login. Admin bootstrap = a management CLI (no HTTP). Candidate
libs: FastAPI-Users (closest) vs a minimal custom cookie+argon2 layer (reuses SECRET_KEY/SALT) -
pick in P2. Open: preserve remember-me? keep fs_uniquifier as session identity?

## 4. Job pipeline (redesign -> Redis + RQ; P4)

Today: DB-backed queue, in-process ThreadPoolExecutor(PIPELINE_WORKERS=2), `submit()` under a
`_submit_lock` does check-then-insert (active_job None) + sets Document.status + commits Job +
runs `_run` in app_context; progress via a `report(stage,current,total)` callback (throttled
1s); `sweep_orphans()` at boot marks all running jobs interrupted; provenance stamped at submit;
classifier keeps a per-process catalog cache + embedding matrix keyed on catalog_version().

Redesign notes (the meat of P4):
- Executor -> RQ. Enqueue a **serializable descriptor** (document_id, kind, model,
  prompt_version, catalog_revision) - NOT the current closures; the worker resolves the document
  from the DB. Register worker functions.
- One-job-per-document across processes: the `_submit_lock` no longer works -> **DB partial-unique
  index on jobs(document_id) WHERE state IN ('queued','running')** (constraint violation -> the
  existing 409). Durable source of truth.
- Status/progress STAY in the DB (the FastAPI /status endpoint = same DB read); keep progress
  throttling (Postgres, but still). Worker remains the single writer of Document.status
  (uploaded->segmenting/summarizing at enqueue is web-side; ->reviewing/done/error worker-side).
- Orphan recovery becomes heartbeat-aware (RQ StartedJobRegistry / worker heartbeat), NOT
  mark-all-on-boot (would kill healthy jobs on a restart with N workers).
- Classifier: each worker holds its own catalog cache + all-MiniLM matrix, refreshed on the
  DB-polled catalog_version() - already multi-process-safe; move reset to worker startup. The
  FastAPI WEB process should NOT import classification (torch); only workers do.
- Provenance stamped at submit (carry in the descriptor) so a delayed job records the revision
  current when the user clicked. Concurrency = worker_count x CLASSIFY_WORKERS - cap vs Vertex
  quota (429 storms are the real ceiling). Sync routes (resummarize/bundle) -> threadpool or queue.
- Redis carries ONLY non-PHI ids; persistence OFF (docker-compose.dev already sets this).
Open: RQ vs Celery (RQ recommended, single-box); dedicated classification worker vs N torch copies.

## 5. Frontend (rewrite -> React/Next; P7, sub-phased per page)

Server-rendered shells + hand-written vanilla JS, no build step. Two shared engines carry most
weight: **`window.MRR`** (the review editor in review.js, mounted by /review AND the bundle
pages) and the **`DocTable` factory** (doc-table.js, used by My Documents + both bundle pickers).
Data-fetching = fetch() + Accept:json + cookie->header XSRF + 401->/login. Two polling loops:
doc-table polls the list every 2s while any doc has an active_job; review.js polls /status every
1s during segment/summarize. Design system = evaluators.css CSS-custom-property tokens +
ev-*/hd-*/rc-*/sum-*/auth-* classes (tokens already ported to frontend/app/globals.css in P1c).

Port map (all -> rewrite as React/Next components): My Documents (DocTable + upload + empty
state), the review editor (window.MRR -> a React component tree: identify/progress/review table +
PDF viewer + autosave + merge/split + the category view-filter added this session), summaries
step, the bundle pages (diagnostics=3+8 / depositions=9; pick-or-upload + embedded editor +
extract/summarize actions + the review filter), the admin console (categories table + prompt
editor), login/register. Classic pages -> drop. Polling -> React data-fetching (SWR/React Query
or hand-rolled). API contracts are stable + reused as-is.

## 6. Services (port-as-is) + integrations + PHI (P3/P4 + HIPAA gate)

**Port-as-is (Flask-free):** pdf, ocr, windows, gemini, genai_retry, segment_engine, verify_pass,
summarize_engine, bundles, files (keep a secure_filename equivalent), prompts.py, taxonomy.py.
Client init (`_build_client`, Vertex/ADC-vs-API-key routing) lifts verbatim to a lifespan/singleton;
config.py ports (drop OPENAI_API_KEY from REQUIRED; make USE_VERTEXAI a hard prod requirement).
The ONE service Flask-coupling = `classification.py` reading the DB catalog via `mrr_ai.catalog`
(falls back to taxonomy constants) -> re-seat on a request-independent Session (worker context),
preserving the version-keyed cache + reset seam. **Drop:** state.py globals, services/jobs.py
(single-slot runner), services/categorization.py (legacy difflib), the OpenAI client.

PHI paths (HIPAA re-review scope, P8 gate): (1) PDFs at `uploads/<user_id>/<uuid>.pdf` - keep
uuid names, don't log filenames; (2) `Summary.source_text` + SegmentRow fine-tuning PHI now in
Postgres - DB-at-rest + export; (3) FOUR Vertex egress points (segment inline PDF bytes; verify
page PNGs + OCR; classify title + first-page OCR; summarize full row OCR) - all gated on
USE_VERTEXAI (fail fast if off); (4) embeddings stay local on every worker; (5) Redis = non-PHI
ids only; (6) enforce ids-only logging at a structured-logging sink (services currently print()).
Open: keep persisting Summary.source_text? Vertex auth on workers (ADC vs API key)? Tesseract/
Poppler + the embedding model provisioned on every worker host?
