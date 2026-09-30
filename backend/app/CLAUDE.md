# backend/app - agent instructions

The package root: `config.py`, `models.py`, `db.py`, `errors.py`, `main.py`, `cli.py`. Subfolders
have their own `CLAUDE.md`.

## config.py

- A new setting is a field on `Settings` plus, in the same PR: validation if a bad value is
  possible (a bound, an enum, a cross-field check in the validators); a decision on whether
  `docker-compose.yml` passes it (most settings are deliberately NOT passed - read
  `docs/explanation/configuration-model.md` and the docstring of
  `tests/test_compose_passthrough.py`); an entry in `.env.example` only if an operator is meant to
  set it; and its row in `docs/reference/configuration.md` (the drift test checks the name).
- A setting that a boot guard compares against must be settable from the environment, or the guard
  becomes a wall nobody can get past.
- Do not weaken the provider guards: production requires Vertex for Gemini, OpenAI requires the
  zero-data-retention acknowledgement, vLLM requires an approved origin. They exist because
  patient data leaves the box through those calls.
- Per-stage backend and model resolution lives here (`backend_for`, `model_for`,
  `model_for_stage`, `thinking_for`). Read `services/llm/CLAUDE.md` before touching it.

## models.py

- Every schema change ships with an Alembic migration in the same PR (`alembic/CLAUDE.md`).
- Database foreign keys are NO ACTION; deletes cascade through ORM relationships. A new table that
  references `documents` needs a cascading relationship on `Document`, or deleting a record fails.
- Provenance columns (`build_sha`, prompt fingerprints, model and backend names, catalog revision)
  are nullable on purpose: NULL means "not recorded". Never give them a server default or backfill
  them.
- The active job states `queued`, `running`, `paused` appear in the partial unique index, in
  `services/jobs.py` `ACTIVE_STATES` and in `Document.active_job`. Adding a state means changing
  all three plus a migration.
- `original_filename` and the patient header columns are patient data: never log them.

## main.py

- `assert_backends_ready()` runs OUTSIDE the try blocks on purpose: a failing model destination
  must stop the boot. Never wrap it in `except Exception`, even though the steps after it are
  wrapped.
- Every route is authenticated by the app-level `enforce_auth` dependency unless its path is in
  the public list in `auth/deps.py`. Adding a public path is a security decision.

## errors.py

- A pipeline failure the user should read is a `PipelineError` subclass with a user-facing
  message. Never surface a vendor exception's text to a user.
- `PdfUnreadableError` subclasses `OcrUnavailableError`, so an `except` chain must test it first.

## Commands

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q tests/test_openai_config_guards.py tests/test_llm_preflight.py tests/test_compose_passthrough.py
python -m app.cli admin grant someone@example.com      # inside the api container
```
