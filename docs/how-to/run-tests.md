# How to run the tests

Tests use pytest with the Flask test client; external services (OpenAI, Gemini, OCR) are
mocked, so no API keys or network are needed.

```bash
uv sync                       # installs dev deps incl. pytest
uv run pytest                 # run the suite
uv run pytest -q tests/unit   # just unit tests
uv run pytest --cov=mrr_ai --cov-report=term-missing   # coverage
```

## Conventions

- `tests/conftest.py` provides the `app`/`client` fixtures (built via `create_app()` with
  dummy env) and patches the LLM clients + OCR.
- `tests/unit/` - pure functions in `mrr_ai/services/`.
- `tests/integration/` - routes via the test client, externals mocked.
- Never use real patient data; build tiny synthetic PDFs/CSVs in the test.

Coverage target is ~90%; CI fails under ~85% (see ADR-0005 / pyproject `[tool.coverage]`).
