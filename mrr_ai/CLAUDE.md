# mrr_ai package

The Flask application, built by `create_app()` in `__init__.py`. Full picture:
[../docs/architecture.md](../docs/architecture.md).

- **config.py** - env validation (fail-fast; incl. SECRET_KEY + SECURITY_PASSWORD_SALT),
  paths, constants. `UPLOAD_FOLDER` is `<repo-root>/uploads`.
- **extensions.py** - `db` (Flask-SQLAlchemy + SQLite WAL pragmas), `genai_client`
  (Gemini) + `client` (OpenAI), built once after `validate_env()`.
- **models.py** - Document/Job/SegmentRow/ReviewRow/Summary/AuditLog. SegmentRow (raw
  model output) vs ReviewRow (human-corrected) is the future fine-tuning dataset.
- **security.py** - Flask-Security wiring + the deny-by-default before_request gate;
  every route outside PUBLIC_ENDPOINTS requires a session.
- **state.py** - shared mutable globals for the CLASSIC UI only. Access as `state.x`;
  NEVER `from mrr_ai.state import x` (that copies the binding and writes won't
  propagate). These globals + the in-process job pool are why the app must run
  **single-process** (serve.py).
- **blueprints/** - HTTP routes (see `blueprints/CLAUDE.md`). `documents_api.py` is the
  multi-user flow: owner-checked (404 on foreign ids), no globals.
- **services/** - business logic, no Flask (see `services/CLAUDE.md`). `job_queue.py`
  runs document pipelines on a bounded pool and is the only writer of Document.status.
- **taxonomy.py** - category catalog (ids/names/corpora) driving the B5 cascade
  (`services/classification.py`). **groups.py** - legacy taxonomy, superseded and unused.
  **prompts.py** - per-category summarization prompts.

How-to: add a route -> `../docs/how-to/add-a-blueprint.md`; add a category ->
`../docs/how-to/add-a-category.md`.
