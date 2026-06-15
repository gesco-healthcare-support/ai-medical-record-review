# Project Conventions (MRR AI)

Load-bearing, project-specific rules. The global `~/.claude` rules also apply.

- **Architecture:** app via `create_app()` in `mrr_ai/`; routes = blueprints, logic =
  services (no Flask in services). See `docs/architecture.md` and `docs/INDEX.md`.
- **State:** shared mutable globals in `mrr_ai/state.py`, accessed as `state.x`. The app is
  **single-process** because of this - do not add a multi-worker server without first
  refactoring state. Never `from mrr_ai.state import x` (copies the binding).
- **CSV contract:** 6 columns `start,end,category,doc_date,injury_date,manual_flag`. Don't
  break it; it's the interface between segmentation and summarization
  (`docs/reference/csv-contract.md`).
- **PHI:** never commit PDFs, OCR text, page-map CSVs, or patient names. `uploads/` and
  experiment caches are gitignored. Don't log PDF/OCR/LLM bodies. PHI flows to OpenAI and
  Gemini - any change to those paths needs the PR template's HIPAA review.
- **Secrets:** via `.env` (fail-fast at startup); never hardcode. Rotate the handoff keys.
- **Tooling:** uv (`pyproject.toml` + `uv.lock`), Python 3.12. Lint/format = ruff; types =
  pyright (advisory while untyped). Pre-commit runs ruff + gitleaks + detect-private-key.
- **Tests:** mock OpenAI/Gemini/OCR; synthetic data only; aim ~90% coverage (CI floor ~85%).
