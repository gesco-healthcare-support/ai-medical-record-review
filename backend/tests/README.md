# backend/tests

The backend's pytest suite. Tests drive the real FastAPI app over an in-process HTTP client, the
real services and the real worker task functions, against a throwaway Postgres and Redis from
`docker-compose.dev.yml`. Model calls, OCR and other external services are replaced with stand-ins,
and every record, name and account is synthetic. The folder is flat: one file per area, plus the
shared `conftest.py`.

## Files

| File | What it tests |
| --- | --- |
| `__init__.py` | Makes the folder a package. |
| `conftest.py` | Shared setup, not tests: finds the test database from the Compose files and refuses the app database, gives each parallel worker its own database, Redis database and account prefix, hashes test passwords at argon2's cheapest cost, deletes test accounts around every test, and provides the `client`, `seeded_user` and `lanes()` helpers. |
| `test_ab_stats.py` | The aggregation used by the segmentation boundary A/B script (`scripts/eval/ab_stats.py`). |
| `test_admin_api.py` | The `/api/admin` routes: categories, prompts, reprocess, admin-only access. |
| `test_admin_record_access.py` | An admin opening and fixing another reviewer's record: the ownership guard, the `owner` list filter, audit attribution, owner-only delete, the accounts list. |
| `test_auth_gate.py` | The deny-by-default authentication gate and its public-path allowlist. |
| `test_auth_integration.py` | Login, logout, the registration rules and the password-reset routes, against the database. |
| `test_backend_provenance.py` | Which model backend answered a call is recorded beside which model did. |
| `test_backfill_doi.py` | Scope resolution of the injury-date backfill script (`scripts/backfill_doi.py`). |
| `test_bundle_cover.py` | The list page placed in front of a combined bundle PDF. |
| `test_cancel.py` | The cancel channel: the Redis flag, the per-process current job, and the worker's cooperative stop. |
| `test_cancel_escapes_model_calls.py` | A reviewer's Stop unwinds through every model-call site instead of being caught as a model failure. |
| `test_catalog.py` | Category and prompt catalog accessors, seeding and row validation, on an in-memory SQLite session. |
| `test_classification.py` | The categorization model call uses the classify model by default and honours an override. |
| `test_classify_prompt_ab.py` | The categorization prompt A/B script's control arm. |
| `test_cli.py` | The admin command-line tool (`app/cli.py`). |
| `test_config_bounds.py` | Settings with a lower bound (for example `GENAI_MAX_RETRIES` at least 1) refuse to boot below it. |
| `test_docs_reference_drift.py` | The reference pages give every setting, route, migration, compose service, frontend page and CI job a row of its own. |
| `test_docs_guides.py` | Every file is named in its folder's README, every path and symbol a doc cites exists, every CLAUDE.md stays at 200 lines or fewer. |
| `test_compose_passthrough.py` | Settings that must be settable from `.env` are named in `docker-compose.yml`, documented in `.env.example`, and share their defaults with `app/config.py`. |
| `test_conftest_db_selection.py` | `conftest.py`'s own port and password pairing and its per-worker isolation. |
| `test_date_in_source.py` | The eval check of whether a row's date appears in its pages. |
| `test_date_label_check.py` | The eval check of labelled encounter dates. |
| `test_date_vs_human_entries.py` | The eval comparison of dates against human-written reports. |
| `test_db.py` | The session factory's flags, which the thread pools depend on. |
| `test_dedup.py` | The duplicate-clustering service and its thresholds. |
| `test_deposition_pages.py` | Deposition page grouping: the transcript-page offset, marker labels and the audit guard. |
| `test_deposition_pages_request.py` | What the transcript page-number read sends, and how it handles a truncated reply. |
| `test_documents_api.py` | The `/api/documents` routes against the database. |
| `test_download_delivery.py` | The delivery status a page can ask about for each prepared download. |
| `test_downloads.py` | Prepared exports: stored on the server, fetched by the browser, deleted on expiry. |
| `test_eval_corpus.py` | The eval corpus rule: one row per distinct PDF for anything pooled across records. |
| `test_extraction.py` | Header extraction and how it reports a partial OCR read. |
| `test_failures.py` | The failure taxonomy and control signals behind resumable summarizing. |
| `test_files.py` | The upload filename sanitizer. |
| `test_genai_client.py` | The Gemini client bounds every request with an HTTP timeout. |
| `test_genai_metrics.py` | Per-attempt model call accounting, which never breaks the caller. |
| `test_genai_retry.py` | The retry seam: the default thinking setting applied to every call, and retry behaviour. |
| `test_house_style.py` | The deterministic capitalisation transform applied to summaries. |
| `test_job_health.py` | How the job-health script classifies terminal states. |
| `test_jobs.py` | The job service (one active job per document, enqueue routing, per-user lanes) and the worker state machine. |
| `test_linked_pdf.py` | The linked-PDF export builder. |
| `test_llm_openai.py` | The OpenAI provider: payload shape, patient-data constraints, retries, startup guards. |
| `test_llm_preflight.py` | The startup check of the vLLM server version, and when it refuses to boot. |
| `test_llm_provider.py` | The provider abstraction changes nothing on the Gemini path. |
| `test_llm_vllm.py` | The vLLM provider: where it deliberately differs from OpenAI, and its retries. |
| `test_main.py` | The application can start. |
| `test_models_methods.py` | Model helper methods (progress, active job, listing, row conversion), in memory. |
| `test_ocr.py` | OCR bounds and per-page resilience. |
| `test_ocr_cap_word_recall.py` | The eval instrument for word recall under a capped OCR render. |
| `test_openai_config_guards.py` | Startup guards for the OpenAI provider settings. |
| `test_pacing.py` | Adaptive pacing of model calls: request and token meters and the controller. |
| `test_page_text.py` | The per-page OCR text store: extract once, reuse, keep errored and blank pages distinct. |
| `test_password.py` | The Flask-Security-compatible password hashing, including its production cost. |
| `test_pool_wiring.py` | Thread-pool timeouts in the pipeline, and that every key in `.env.example` is named in `docker-compose.yml`. |
| `test_pools.py` | Bounded draining of thread pools. |
| `test_prompt_content.py` | Required wording in the prompts. |
| `test_provenance.py` | Per-call model tiering and prompt provenance recorded on jobs and summaries. |
| `test_rasterise.py` | The shared PDF rasteriser: render resolution and the page cap. |
| `test_repair_dedup_status.py` | Which documents the status-repair script touches. |
| `test_reporting.py` | Assembly of the Word export. |
| `test_rows_property.py` | Property-based tests (Hypothesis) of row validation. |
| `test_segment_engine.py` | Segmentation's injury-date stage. |
| `test_segment_engine_request.py` | What a segmentation window call sends. |
| `test_segment_thinking.py` | Segmentation keeps model thinking on while other calls default it off. |
| `test_summarize_engine.py` | Summarizing one row: the preamble, temperature and related settings. |
| `test_summary_doi.py` | The isolated injury-date read. |
| `test_summary_doi_request.py` | What the injury-date read sends, and how it handles a truncated reply. |
| `test_summary_verify.py` | The summary faithfulness check. |
| `test_user_manager.py` | The password rule: length, a number, a symbol. |
| `test_verify_pass.py` | Which boundaries the segmentation verify pass sends to the model. |
| `test_windows.py` | Window packing for segmentation: the byte budget and the page cap. |

## Running the suite

From the repository root, start the test Postgres and Redis, then from `backend/` install, migrate
and run:

```bash
docker compose -p mrrtest -f docker-compose.dev.yml up -d --wait postgres redis
cd backend
uv sync --extra docs
DATABASE_URL=postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr SECRET_KEY=dev-only-secret SECURITY_PASSWORD_SALT=dev-only-salt uv run alembic upgrade head
uv run pytest -q
```

CI runs `uv run pytest -n 3 --dist loadfile --cov=app --cov-branch --cov-report=xml --cov-report=term-missing`
and fails below 90% branch-aware coverage. Every test, even a pure one, needs the Postgres above,
because the automatic cleanup fixture in `conftest.py` queries the users table around each test.

Full instructions, the error messages and their fixes:
[How to run the tests](../../docs/how-to/run-the-tests.md). CI details:
[CI and merge gates](../../docs/reference/ci-and-merge-gates.md).
