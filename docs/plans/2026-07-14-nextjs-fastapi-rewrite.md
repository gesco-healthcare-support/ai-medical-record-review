---
feature: nextjs-fastapi-rewrite
date: 2026-07-14
status: in-progress
base-branch: main
related-issues: []
---

## Goal
Re-platform MRR AI from Flask (server-rendered + vanilla JS) to a **Next.js (React/TypeScript) front-end + FastAPI (Python) back-end** with an **externalized job queue (Path B)**, so 10-15 staff use it concurrently from their own logins, while the current Flask app stays live on the server for users to test until cutover.

## Context
Decision made by Adrian after reviewing the counter-arguments (the current app already handles 10-15 daily logins; the real limit is the 2-wide in-process job pool + Vertex quota). The re-platform is driven by wanting a modern React/TS stack + genuine concurrent AI throughput. Phase-0 research (the porting-spec workflow, 6 read-only agents) is the basis for this plan; full track output archived in the run transcript.

Key finding that makes this tractable: the `services/` layer (the AI pipeline) is deliberately Flask-free, so it **ports to FastAPI nearly unchanged**. The rewrite concentrates in the HTTP layer, auth, the queue redesign, and the frontend.

## Locked decisions (confirmed with Adrian)
- **Frameworks:** Next.js (React/TS) frontend + FastAPI backend.
- **Deploy:** SAME-ORIGIN behind one reverse proxy on the LAN box; **HttpOnly signed cookie session** (no browser-held token; best for PHI). Preserve existing logins (argon2 hashes + `fs_uniquifier`) - no forced re-registration.
- **Database:** **local self-hosted PostgreSQL** on the server (NO cloud/managed). One-time SQLite -> Postgres data migration; Alembic for schema. Removes the multi-writer risk that SQLite + external workers would create.
- **Queue (Path B):** Redis + **RQ** worker processes (co-located on the server). DB stays the source of truth for job status/progress and the one-active-job-per-document invariant.
- **AI path:** Vertex-only (drop OpenAI); make `GOOGLE_GENAI_USE_VERTEXAI=true` a hard production requirement (fail fast otherwise). Embeddings stay local.
- **Classic flow:** DROP, EXCEPT re-create three capabilities in the modern flow (see Parity).
- Conventions: ids-only structured logging enforced at the boundary; keep a filename sanitizer at upload; monorepo on this branch (`backend/`, `frontend/`) with the existing `mrr_ai/` Flask app untouched until cutover.

## Target architecture
- `frontend/` - Next.js app (React/TS), served same-origin. Reproduces the Evaluators design tokens; components for My Documents (DocTable), the review editor (today's `window.MRR`), summaries, bundles, admin.
- `backend/` - FastAPI app: two routers (`/api/documents` 15 routes, `/api/admin` 7 routes) + auth endpoints + a shared SQLAlchemy models package + the ported `services/`. Global deny-by-default auth middleware + `is_admin` gate + owner-check dependency.
- `worker/` - RQ workers (same codebase) running segmentation/summarization/reprocess jobs; Tesseract/Poppler + the local embedding model provisioned on the worker host.
- Local **PostgreSQL** + **Redis** services; a reverse proxy (nginx/Caddy) fronting Next.js + FastAPI same-origin.

## Port disposition (from Phase-0 spec)
- **Port as-is:** services (pdf, ocr, gemini, windows, genai_retry, segment_engine, verify_pass, summarize_engine, bundles, files), `config`, the genai client builder, `prompts.py`/`taxonomy.py` constants, the 9 domain SQLAlchemy models (DB-agnostic), `catalog`/`audit` helpers. One crack: `classification` reads the DB catalog - re-seat it on an explicit session (worker context).
- **Rewrite:** the 22 modern HTTP routes (-> FastAPI routers + Pydantic), auth/session/CSRF (Flask-Security has no drop-in FastAPI equal), the User/Role tables (mirror the fsqla columns exactly, preserve hashes), the frontend (all modern pages/components -> React).
- **Redesign:** the job pipeline (`job_queue`) -> Redis + RQ workers.
- **Drop:** `state.py`, `services/jobs.py`, `services/categorization.py`, `review_api`, and the classic blueprints (upload, summarize, reports, extraction, individual_mrr, export-classic, segmentation) + `/classic` pages + OpenAI client.

## Parity to re-create in the modern flow (confirmed)
1. **Auto-extract patient name/DOB + attorney/law firm** from the first pages (via Vertex, not OpenAI) to prefill the report header (today only classic does this).
2. **Individual-record folders** - a case as many separate PDFs processed together (fix the known always-`category_01` prompt-selection bug during the port).
3. **Aggregated-records PDF merge + page ranges** - merge a folder's PDFs into one and compute each source's page ranges.
   (CSV checker is NOT preserved - dropped.)

## Data migration
- Author byte-accurate SQLAlchemy models by introspecting the live DB (`PRAGMA table_info`) for the fsqla `user`/`role`/`roles_users` columns (`fs_uniquifier`, `password`, `active`, ...).
- Alembic baseline = current full schema incl. all `_ADDITIVE_COLUMNS`; migrate data SQLite -> Postgres (types: `examples` JSON->JSONB, datetimes->timestamptz naive-match, category free-text strings preserved). Preserve argon2 `password` hashes + `fs_uniquifier` so logins survive.
- Keep the seed-if-empty semantics + the exact quirks (id 6 active/auto_assign=False, id 11 no prompt -> falls back to 100).

## Phases (each runs its own research -> design -> build when reached)
- **P0 - DONE:** porting-spec research.
- **P1 - Foundations/infra:** monorepo scaffold; local Postgres + Redis (docker-compose for dev); port `config` + genai client (Vertex-only); shared models package; Alembic baseline; SQLite->Postgres migration script; CI for the new stack. Accept: FastAPI boots on local Postgres with migrated schema; a migrated user row round-trips.
- **P2 - Auth:** HttpOnly cookie session, argon2 verify against migrated hashes, global deny-by-default middleware + `is_admin` gate + owner-check dependency, login/logout/register (open/non-confirmable + password rules), CSRF double-submit, admin bootstrap CLI. Accept: an existing user logs in with no reset; non-admin gets 403 on admin routes; a new unlisted route is denied by default.
- **P3 - Documents API:** the 15 `/api/documents` routes (FastAPI router + Pydantic + `_own` 404 IDOR dep), wired to ported services; upload/download, rows CRUD+validation, summaries CRUD, docx export; sync AI routes via threadpool. Accept: documents API at parity, tested.
- **P4 - Job pipeline (Path B):** Redis + RQ; serializable job descriptors (not closures); worker processes (OCR + embedding model provisioned); DB-backed status/progress + throttling; partial-unique index enforcing one active job/doc (-> 409); heartbeat-aware orphan recovery; segment/summarize/reprocess jobs; classifier catalog on a worker session; concurrency capped to the Vertex quota. Accept: multiple jobs run concurrently across workers; one-job-per-doc holds under a race; restart doesn't kill healthy jobs.
- **P5 - Admin API:** the 7 `/api/admin` routes + catalog accessor on a session + revision-bump/classifier-reload. Accept: admin CRUD + reprocess at parity, tested.
- **P6 - Parity features:** the three above, re-created per-document. Accept: each works + tested; the individual-record prompt bug fixed.
- **P7 - Frontend (Next.js), sub-phased per page:** design system (tokens), auth pages, My Documents, review editor, summaries, bundles (incl. the category review filter), admin console, the parity UIs; consumes the API same-origin. Accept: full UI parity, live-verified.
- **P8 - Cutover:** provision Postgres + Redis + reverse proxy on the server; migrate production data; full HIPAA re-review sign-off; parallel run + acceptance; switch the proxy; decommission the Flask app. Accept: users on the new stack; rollback documented.

## HIPAA / PHI re-review scope
Every PHI path is re-audited for the new stack: (1) PDF-at-rest under `uploads/<user_id>/<uuid>.pdf` (keep uuid names; upload handler must not log filenames); (2) `Summary.source_text`/`SegmentRow` PHI in Postgres (DB-at-rest protection + export); (3) the four Vertex egress points gated on the BAA/Vertex flag (fail fast if off); (4) embeddings stay on-box on every worker; (5) **Redis carries only non-PHI identifiers** (document uuid, kind, model, prompt_version, catalog_revision) - workers read PHI from the DB/disk; Redis on the internal network with auth/TLS, persistence off or encrypted; (6) structured logging enforces ids-only at the sink.

## Testing strategy
- Backend: pytest against the FastAPI app + a Postgres test DB (or SQLite for pure-unit where safe); port/keep the existing unit tests for the services (they're framework-free); new tests for auth, the IDOR guard, the queue invariant, and each router. Target the existing ~85% floor.
- Frontend: component/integration tests (Vitest/RTL or Playwright) per page; live verification on the LAN via a test login.
- Parity: for each dropped-then-recreated capability, a test proving the modern equivalent produces the expected output on synthetic input.

## Risk / rollback
- **Blast radius:** entirely on `feat/nextjs-fastapi-rewrite`; `main` (the Flask app) stays deployable throughout, so users keep working during the build. Cutover is the only high-risk moment.
- **Top risks:** Vertex 429 storms once N workers replace the 2-wide pool (cap concurrency; keep genai_retry); auth/hash incompatibility locking users out (verify against a real hash before cutover); the SQLite->Postgres migration (dry-run + verify row counts + spot-check PHI columns); HIPAA regressions (the re-review gate blocks cutover).
- **Rollback:** until P8 cutover, rollback is "keep using `main`." Post-cutover, keep the Flask app + SQLite snapshot recoverable for a defined window; the proxy switch is reversible.

## Deferred / open (resolved per-phase)
- RQ vs a dedicated classification worker to avoid N torch copies (P4).
- FastAPI auth library (FastAPI-Users vs minimal custom) - pending inspection of a live password hash (P2).
- Whether to keep persisting `Summary.source_text` fine-tuning PHI (P1/HIPAA).
- Confirm `review_api`/`/classic` have zero live consumers before removing (P1).
- Reverse proxy choice (nginx vs Caddy) + how the server currently exposes the app (P8).
