# mrr_ai package

The Flask application, built by `create_app()` in `__init__.py`. Full picture:
[../docs/architecture.md](../docs/architecture.md).

- **config.py** - env validation (fail-fast), paths, constants. `UPLOAD_FOLDER` is
  `<repo-root>/uploads`.
- **extensions.py** - `genai_client` (Gemini) + `client` (OpenAI), built once after
  `validate_env()`.
- **state.py** - shared mutable globals. Access as `state.x`; NEVER
  `from mrr_ai.state import x` (that copies the binding and writes won't propagate). These
  globals are why the app must run **single-process**.
- **blueprints/** - HTTP routes (see `blueprints/CLAUDE.md`).
- **services/** - business logic, no Flask (see `services/CLAUDE.md`).
- **groups.py** - category taxonomy; **prompts.py** - per-category prompts.

How-to: add a route -> `../docs/how-to/add-a-blueprint.md`; add a category ->
`../docs/how-to/add-a-category.md`.
