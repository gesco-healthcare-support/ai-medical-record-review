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
- **taxonomy.py** / **prompts.py** - the category catalog (ids/names/corpora) and
  per-category summary prompts. As of the admin feature these are the SEED source and the
  runtime FALLBACK, not the live source: on first boot they seed the `Category`/`Prompt`
  DB tables (`seed_catalog.py`), and everything reads through **catalog.py** thereafter.
  **groups.py** - legacy taxonomy, superseded and unused.
- **catalog.py** - DB-first accessor for the editable catalog: `get_categories` /
  `get_category_ids` / `get_category_options` / `get_prompt` / `catalog_version` /
  `bump_revision`. Admins edit categories + summary prompts via `/api/admin`
  (`blueprints/admin_api.py`), gated by the `is_admin` flag; an edit bumps the revision so
  the classifier's cached catalog + embedding matrix reload.
- **cli.py** - `flask admin grant/revoke/list` to mark the few admin accounts.

How-to: add a route -> `../docs/how-to/add-a-blueprint.md`; add a category ->
`../docs/how-to/add-a-category.md`.
