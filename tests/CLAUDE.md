# tests

pytest + the Flask test client. External services (OpenAI, Gemini, Tesseract/Poppler) are
**mocked** - no API keys or network needed.

- **conftest.py** - `app`/`client` fixtures built via `create_app()` with dummy env;
  fixtures that monkeypatch `mrr_ai.extensions.client`, `mrr_ai.extensions.genai_client`,
  and the OCR functions; temp dirs for `~/MRRs` and `uploads/`.
- **unit/** - pure functions in `mrr_ai/services/`.
- **integration/** - routes via the test client, externals mocked.
- **test_smoke.py** - asserts every route registers and the GET pages render.

## Rules
- **Synthetic data only.** Build tiny PDFs/CSVs in-test; never commit or use real patient data.
- Mock at the boundary (`extensions` clients, `services.ocr`), not deep internals.
- Reset shared `state` between tests that mutate it (the app is single-process).
- Run: `uv run pytest --cov=mrr_ai`. Aim ~90% coverage; CI floor ~85%.

How-to: `../docs/how-to/run-tests.md`.
