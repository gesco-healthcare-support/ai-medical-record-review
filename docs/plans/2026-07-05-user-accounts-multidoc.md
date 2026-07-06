---
feature: user-accounts-multidoc
date: 2026-07-05
status: draft
base-branch: experiment/segmentation-vertex
related-issues: []
---

## Goal

Multi-user MRR AI: login + registration (confirm password), a persistent store for
users, documents, runs, and corrected rows (the future fine-tuning dataset), a landing
page listing each user's documents, and concurrent document pipelines so a user can
start one document, switch to another, and return to the first while it runs.

## Context

- Today the app has NO auth (no SECRET_KEY, no sessions, every route open), NO
  database, and a deliberate one-document design: module globals in `mrr_ai/state.py`
  (one `pdf_filepath`, one `all_data`) and a single-slot in-memory job runner
  (`mrr_ai/services/jobs.py`). state.py itself marks the per-session store as known
  follow-up work. The concurrency requirement removes that design for the /review flow.
- Persisting raw model rows + human-corrected rows + run provenance IS the training
  flywheel: (SegmentRow raw, ReviewRow corrected) pairs keyed by model + prompt
  version are exactly what Vertex fine-tuning / PSS training will consume.
- Deployment reality: single Windows box, single process (required by remaining
  globals), Docker available but unused by this app. Legacy OpenAI blueprints
  (summarize/extraction/reports/individual_mrr) are dead until funded; they get
  auth-gated but not reworked. Classic UI keeps the global-state single-user flow.

## Decisions (Adrian, 2026-07-05, via modal)

- DB: **SQLite via SQLAlchemy** (WAL mode; ORM keeps a later SQL Server/Postgres swap cheap).
- Registration: **open self-registration** (risk + revisit gate below).
- Auth stack: **Flask-Security 5.8** (Pallets ecosystem; OWASP-guided; argon2id default).

## Approach

- **Auth**: Flask-Security `SECURITY_REGISTERABLE`; the v2 register form includes the
  confirm-password field by default (`SECURITY_PASSWORD_CONFIRM_REQUIRED` defaults
  True - verified against 5.8 docs, see Appendix A). `SECURITY_CONFIRMABLE=False`
  (no SMTP on the box; flip later when mail infra exists). Deny-by-default via an
  app-level `before_request` gate with an explicit PUBLIC_ENDPOINTS allowlist so an
  unlisted route can never ship unprotected. Flask-Security content-negotiates:
  JSON requests get 401 JSON, browser requests get the login redirect - no custom
  handler needed. Rejected alternative: Flask-Login + hand-rolled forms (more
  bespoke security code on a PHI app for no gain).
- **Jobs**: DB-backed `jobs` table + bounded in-process `ThreadPoolExecutor`
  (`PIPELINE_WORKERS`, default 2 - Vertex DSQ quota is shared; the 2026-07-06
  bake-off showed congestion behavior). One active job per document. Startup sweep
  marks orphaned running jobs `interrupted`. UI polls per-document status - a
  generalization of the existing polling design. Rejected: Celery/RQ (Redis infra,
  poor native Windows support), APScheduler (schedules, not submit-and-poll; same
  single-process constraints).
- **Storage**: PDFs on disk at `uploads/<user_id>/<document_uuid>.pdf` (uuid names
  kill collisions and keep patient-named filenames out of paths/logs); DB stores
  original filename, sha256, page count. App-level PDF encryption deferred: volume
  encryption (BitLocker) + OS ACLs + app auth (risk section).
- **UI**: landing page (`/`) lists the user's documents with live status chips;
  `/review/<doc_id>` scopes the existing wizard to one document; editor rows
  autosave (debounced PUT) so switching documents never loses corrections.
- **Serving**: waitress, ONE process, threads >= PIPELINE_WORKERS + HTTP headroom.

## Document status machine

```
uploaded -> segmenting -> reviewing -> summarizing -> done
   |            |             ^  |          |
   |            +-> error     |  +----------+   (re-summarize allowed from done/error)
   |            +-> interrupted (app restart) -> re-runnable
   +--(delete allowed in any state with NO active job; active job -> 409)
```
Transitions are driven ONLY by the job service (single writer for status), except
`uploaded` (set by upload) and deletion. `reviewing` is entered when a segment job
finishes; re-entering the editor never changes status backward from `done`.

## Data model (training-flywheel core)

All new code in `mrr_ai/models.py`; `db` (Flask-SQLAlchemy) in extensions.py.

```python
class Role(db.Model, fsqla.FsRoleMixin): pass          # fsqla_v3 mixins
class User(db.Model, fsqla.FsUserMixin): pass          # argon2 hash, fs_uniquifier

class Document(db.Model):
    id            # str uuid4 pk
    user_id       # FK users.id, indexed, NOT NULL
    original_filename  # str - PHI-bearing; never logged (audit uses id)
    stored_path   # str - uploads/<user_id>/<id>.pdf
    sha256        # str(64) NOT NULL - dedup warning, integrity
    page_count    # int NOT NULL
    status        # enum str: uploaded|segmenting|reviewing|summarizing|done|error|interrupted
    created_at, updated_at
    # relationships: jobs, review_rows, summaries (cascade="all, delete-orphan")

class Job(db.Model):                                   # doubles as run provenance
    id            # int pk
    document_id   # FK, indexed, NOT NULL
    kind          # segment|summarize
    state         # queued|running|done|error|interrupted
    stage, current, total                              # progress snapshot for polling
    error         # str nullable
    model         # config.GENAI_MODEL / SUMMARY_MODEL at submit time
    prompt_version                                     # gemini.PROMPT_VERSION at submit
    created_at, started_at, finished_at

class SegmentRow(db.Model):      # RAW model output - immutable training INPUT
    id; job_id (FK, indexed); idx
    start; end; category; title; date; injury_date; flag; suggest_merge (bool)

class ReviewRow(db.Model):       # human-corrected working set - training LABEL
    id; document_id (FK, indexed); idx
    start; end; category; title; date; injury_date; flag

class Summary(db.Model):
    id; document_id (FK, indexed); job_id (FK); idx
    title; date; text; manual_check (bool)
    row_start; row_end; row_category                   # snapshot of the summarized row

class AuditLog(db.Model):
    id; user_id (FK); action                           # login|register|upload|view_pdf|export|delete
    document_id (nullable); at
```

Training pair per document = SegmentRow (by job -> model + prompt_version) vs final
ReviewRow. A training-export script is future work (YAGNI); this schema suffices.
Schema management: `db.create_all()` on boot for v1 (no existing DB anywhere);
GATE: the first post-release schema CHANGE introduces Alembic - create_all cannot
alter existing tables.

## API contract (new blueprint `documents_api.py`; all owner-scoped)

Every route resolves `Document` by id AND `user_id == current_user.id`; any miss
returns 404 (not 403 - do not confirm existence to non-owners; IDOR guard).

| Method+Path                              | Body / Returns | Errors |
|------------------------------------------|----------------|--------|
| POST /api/documents                      | multipart pdf -> {id, page_count, sha256_duplicate: bool} | 400 bad/unreadable pdf |
| GET  /api/documents                      | [{id, original_filename, page_count, status, created_at, updated_at, active_job: {kind, stage, current, total} or null}] | - |
| GET  /api/documents/<id>                 | detail + review_rows + categories | 404 |
| GET  /api/documents/<id>/pdf             | inline PDF (send_file, conditional) | 404 |
| POST /api/documents/<id>/segment/start   | -> {ok} ; queues job | 404, 409 active job |
| GET  /api/documents/<id>/status          | {status, job: {kind, state, stage, current, total, error} or null; rows+categories included once when segment done} | 404 |
| PUT  /api/documents/<id>/rows            | {rows:[...]} -> {ok} ; validate_rows reused; replaces ReviewRow set | 404, 400 invalid rows, 409 while summarizing |
| POST /api/documents/<id>/summarize/start | {rows} -> {ok} ; persists rows, then queues | 404, 400, 409 |
| GET  /api/documents/<id>/summaries       | [{idx, title, date, text, manual_check, row_*}] | 404 |
| POST /api/documents/<id>/export          | {patientName, patientdob, QMEorAME, lawfirm} -> docx download built from PERSISTED summaries (no state.all_data) | 404, 409 none yet |
| DELETE /api/documents/<id>               | -> {ok}; removes rows/jobs/summaries + files | 404, 409 active job |

Legacy singleton endpoints (`/api/segment/*`, `/api/summarize/*`, `/upload`,
`/getPages`, `/exportresultstoword`, ...) remain for the classic UI, auth-gated,
untouched otherwise. `/reset` clears only module globals - classic-only, harmless
to the new flow.

## Job service (`mrr_ai/services/job_queue.py`)

```python
init_app(app)                    # captures app for context, creates executor, runs orphan sweep
submit(document_id, kind, target, *, model, prompt_version) -> Job | None
    # None when the document already has a queued/running job (route -> 409).
    # Creates Job(queued) + Document.status transition, then pool.submit(_run, job_id).
_run(job_id)                     # worker: with app.app_context(): fresh session;
                                 # job -> running; target(report); done/error; status transition
report(stage, current, total)    # throttled DB write: only on stage change or >=1s
                                 # since last write (progress churn must not hammer SQLite)
sweep_orphans()                  # at boot: running/queued -> interrupted; document status follows
active_job(document_id) -> Job | None
```

- Executor: `ThreadPoolExecutor(max_workers=config.PIPELINE_WORKERS)` (default 2,
  env-tunable). Queued jobs simply wait in the pool queue; state stays `queued`
  until the worker picks them up.
- Sessions: workers use their own session (`db.session` inside app context per
  thread via Flask-SQLAlchemy's scoped session); pass IDs into workers, never ORM
  objects (thread + session affinity).
- SQLite: WAL + `busy_timeout=5000` set via an SQLAlchemy connect event listener.
- Job cancellation is OUT OF SCOPE v1 (a Gemini call in flight cannot be safely
  killed; delete is blocked while active instead).
- The existing `services/jobs.py` stays untouched for the classic UI.

## Provenance

`gemini.py` gains `PROMPT_VERSION = "<n>"` (bump on any prompt/schema change; the
2026-07-06 rework is the first stamped version). Job rows record model +
prompt_version at submit; SegmentRows inherit provenance through job_id.

## Tasks (dependency order; T5/T6-T7 parallelize after T4)

- T1: Foundations - deps, config, factory wiring
  - approach: code
  - files-touched: [pyproject.toml, mrr_ai/config.py, mrr_ai/extensions.py,
    mrr_ai/__init__.py, .env.example]
  - Deps: `flask-security[common,fsqla]` (pulls flask-sqlalchemy, flask-wtf,
    argon2-cffi), `waitress`. Config (Appendix A keys): SECRET_KEY +
    SECURITY_PASSWORD_SALT env-required (extend validate_env fail-fast; generate
    via `python -c "import secrets; print(secrets.token_hex(32))"`),
    SQLALCHEMY_DATABASE_URI default `sqlite:///<repo>/instance/mrr.db`,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax"
    (SESSION_COOKIE_SECURE the day HTTPS exists), PIPELINE_WORKERS=2.
  - Interfaces produced: `extensions.db`, `config.PIPELINE_WORKERS`.
  - acceptance: app boots with env set; missing SECRET_KEY/salt exits with a clear
    message naming the variable.

- T2: Data model
  - approach: tdd
  - files-touched: [mrr_ai/models.py (new), mrr_ai/extensions.py,
    tests/test_models.py]
  - All models above; `db.create_all()` on boot; connect-event WAL pragma.
  - Interfaces produced: every model class; consumed by T3-T9.
  - acceptance: round-trip + cascade-delete tests green on tmp SQLite (synthetic
    data only); WAL pragma asserted via `PRAGMA journal_mode`.

- T3: Auth flows + deny-by-default gate
  - approach: tdd (gate, registration validation); code (templates)
  - files-touched: [mrr_ai/__init__.py, mrr_ai/security.py (new),
    mrr_ai/templates/security/*.html, tests/test_auth.py]
  - Flask-Security init (fsqla_v3 + SQLAlchemyUserDatastore); register (confirm
    password ON by default), login, logout. `before_request` gate: allowlist =
    {security.login, security.register, static}; everything else requires an
    authenticated session. CSRF per Appendix A: CSRFProtect(app) + cookie
    (`SECURITY_CSRF_COOKIE_NAME="XSRF-TOKEN"`), JS sends `X-XSRF-Token` (exact
    recipe: Flask-Security patterns.html "CSRF" section - verify at build).
    Templates styled to match review.css.
  - acceptance: pytest matrix - anonymous GET/POST to a SAMPLE of every blueprint
    (incl. /getPages, /exportresultstoword, /review, /api/documents) -> 302 (html)
    or 401 (json); register rejects mismatched confirm server-side; login round-trip
    works; unsafe-method request without CSRF token -> 400.

- T4: Job service
  - approach: tdd
  - files-touched: [mrr_ai/services/job_queue.py (new), tests/test_job_queue.py]
  - As specified above (submit/report-throttle/sweep/active_job, app-context
    workers, IDs-not-ORM-objects).
  - Interfaces produced: `submit`, `active_job`, `sweep_orphans`; consumed by T5.
  - acceptance: stub-target tests - two documents run concurrently; second submit
    on same document returns None; orphan sweep flips running->interrupted at init;
    target exception lands in Job.error + document status error; progress writes
    throttled (call report 100x fast -> far fewer DB writes).

- T5: Document-scoped API
  - approach: tdd (ownership + validation + status transitions)
  - files-touched: [mrr_ai/blueprints/documents_api.py (new),
    mrr_ai/blueprints/__init__.py, tests/test_documents_api.py]
  - Contract table above. Upload: safe_name for display only; storage name is uuid;
    sha256 duplicate -> warning flag in response (never a block - re-runs are
    legitimate). Segment job target wraps `run_segmentation(...)` then bulk-inserts
    SegmentRows AND seeds ReviewRows from them (editor starts from model output).
    Summarize target iterates persisted ReviewRows -> Summary rows.
  - Interfaces consumed: T2 models, T4 submit, existing run_segmentation /
    summarize_row / validate_rows (reused import from review_api).
  - acceptance: pytest - user B 404s on ALL of user A's routes; invalid rows 400
    with row number; 409s (double segment, delete-while-running, rows-while-
    summarizing); export builds docx from DB after app restart (no globals).

- T6: Review UI rework (document-scoped editor)
  - approach: test-after (Playwright, synthetic PDF; DOM-only for real files)
  - files-touched: [mrr_ai/static/review.js, mrr_ai/templates/review.html,
    mrr_ai/blueprints/pages.py]
  - `/review/<doc_id>` route injects doc id via `<body data-doc-id=...>`; review.js
    gains `api(path)` prefix helper + CSRF header helper (reads XSRF-TOKEN cookie);
    upload step moves to the landing page (editor opens an existing document);
    `renderTable` change handler debounce-PUTs rows (800ms) + flush on summarize;
    status polling hits `/api/documents/<id>/status`; summaries + export wired to
    document endpoints; "My documents" link replaces Start over.
  - acceptance: full synthetic flow in-browser on the new endpoints; mid-review
    reload restores rows from DB; split/add/merge/row-click regression-checked.

- T7: Landing page
  - approach: test-after (Playwright)
  - files-touched: [mrr_ai/templates/home.html (new), mrr_ai/static/home.js (new),
    mrr_ai/blueprints/pages.py]
  - `/` lists documents (name, pages, uploaded, status chip, last activity) with
    upload as the primary action; "Summarized" section lists done docs -> summaries
    view; in-progress docs -> resume editor; single poll of GET /api/documents
    while any active_job, stops when idle; delete with confirm (409 surfaced when
    job active). Classic UI link kept.
  - acceptance: docs in uploaded/segmenting/reviewing/done states render correct
    chips + actions; poll stops when idle (network tab assertion).

- T8: Concurrent-documents proof
  - approach: test-after
  - files-touched: [tests/test_concurrent_flow.py]
  - Integration test with stubbed engines (no Gemini spend): summarize doc A +
    segment doc B overlapping via real job_queue; then ONE live Playwright pass on
    two synthetic PDFs (workers=2) - start A summarize, open B, segment, return to A.
  - acceptance: both complete; landing chips tracked both; A's rows intact.

- T9: Audit log + logging hygiene
  - approach: tdd (audit writer); code (wiring)
  - files-touched: [mrr_ai/services/audit.py (new), mrr_ai/security.py,
    mrr_ai/blueprints/documents_api.py, tests/test_audit.py]
  - `audit(action, document_id=None)` writes AuditLog rows on login/register (via
    Flask-Security signals user_authenticated / user_registered), upload, view_pdf,
    export, delete. Logging rule: doc ids only - `original_filename` never appears
    in log/print statements (grep gate in acceptance).
  - acceptance: audit rows asserted per action; `grep -rn original_filename` in
    logging/print contexts returns nothing.

- T10: Serving + ops + docs
  - approach: code
  - files-touched: [serve.py (new), README.md, mrr_ai/CLAUDE.md, .env.example]
  - `serve.py`: waitress, `threads = PIPELINE_WORKERS + 6`, host/port from env;
    SINGLE-PROCESS constraint documented in serve.py header + README (in-process
    pool + classic-UI globals both break under multiple processes). SQLite backup
    note (sqlite3 .backup or copy while stopped + uploads/ dir). Quickstart:
    fresh .env -> register -> upload -> summarize.
  - acceptance: quickstart followed verbatim on a clean checkout succeeds.

## Risk / Rollback

- Blast radius: every route (auth gate) + the new-flow backend. Classic UI behavior
  preserved behind login; segmentation/summarize engines untouched.
- OPEN REGISTRATION (accepted 2026-07-05): per-user isolation confines a rogue
  account to its own uploads, but anyone with network reach gets an authenticated
  foothold + Gemini spend. REVISIT GATE before the app is reachable beyond
  Adrian's machine/LAN; the domain-allowlist fallback is a ~10-line change in a
  registration validator.
- PHI at rest: PDFs + rows on disk/SQLite; protection = BitLocker + OS ACLs + app
  auth. App-level encryption deferred (complicates send_file/page rendering);
  revisit if the box is ever shared.
- SQLite concurrency: WAL + busy_timeout + throttled progress writes keep the
  single-writer window small; if write contention ever bites, the ORM makes
  Postgres/SQL Server a config change plus migration, not a rewrite.
- Single-process: waitress ONE process only; documented twice (serve.py, README).
- MAX_CONTENT_LENGTH stays 1 GB per upload; per-user storage quota deliberately
  out of scope v1.
- No job cancellation v1 (blocked deletes instead); acceptable because pipelines
  are minutes-long, not hours.
- Rollback: single feature branch; new state = instance/mrr.db + uploads/<user_id>
  dirs - delete to reset. No existing data to migrate.

## Verification (end-to-end, after all tasks)

1. Clean checkout + fresh .env (generated SECRET_KEY/salt) -> `uv run python
   serve.py` -> register two users (mismatched confirm rejected first), login as A.
2. Upload synthetic doc A (10pp), start segmentation; immediately upload doc B and
   start segmentation; landing shows both chips progressing (workers=2).
3. On A: split a row, add a row, leave mid-review, reopen -> rows restored from DB;
   summarize; export docx opens in Word.
4. As B (second browser): landing empty; direct GET of A's document/PDF/summaries
   URLs -> 404; POST to A's segment/start -> 404.
5. Kill the app mid-job, restart: job interrupted on landing, re-runnable.
6. DB spot-check: SegmentRow vs ReviewRow differ exactly where edits were made;
   Job rows carry model + prompt_version; AuditLog holds the session's actions.
7. Suite + ruff green; HIPAA checklist: synthetic fixtures only, no
   original_filename in logs, cookies HttpOnly/SameSite=Lax, CSRF enforced.

## Appendix A - Flask-Security 5.8 config (verified against official docs 2026-07-05)

| Key | Value | Note |
|---|---|---|
| SECRET_KEY | env, required | token/session signing |
| SECURITY_PASSWORD_SALT | env, required | HMAC double-hash salt |
| SECURITY_REGISTERABLE | True | default False |
| SECURITY_PASSWORD_CONFIRM_REQUIRED | True (default) | confirm field on RegisterFormV2 |
| SECURITY_USE_REGISTER_V2 | True (default since 5.7) | v2 register form |
| SECURITY_CONFIRMABLE | False | no SMTP yet; flip later |
| SECURITY_PASSWORD_HASH | "argon2" (default since 5.5) | argon2id via libpass |
| SECURITY_CSRF_COOKIE_NAME | "XSRF-TOKEN" | default None (off) |
| SECURITY_CSRF_HEADER | "X-XSRF-Token" (default) | fetch() sends this |
| SECURITY_RETURN_GENERIC_RESPONSES | consider True | user-enumeration hardening |
| (behavior) | JSON requests -> 401 JSON, browser -> login redirect | content negotiation built in |

Setup pattern (quickstart-verified): `fsqla.FsModels.set_db_info(db)`; `class User(
db.Model, FsUserMixin)`; `SQLAlchemyUserDatastore(db, User, Role)`; `Security(app,
datastore)`. CSRF-for-fetch recipe: patterns.html "CSRF" section (CSRFProtect +
cookie); re-read that section at build time before wiring T3.
