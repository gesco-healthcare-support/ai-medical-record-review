# ADR-0005: Testing strategy, coverage policy, and CI secret-scan

**Status:** Accepted

## Context
The codebase had no automated tests. It is external-dependency-heavy (OpenAI, Gemini,
Tesseract, python-docx), and CI's `secret-scan` job failed because `gitleaks-action@v2`
requires a paid license for organization repos.

## Decision
- **Tests:** pytest with the Flask test client; mock the external clients (OpenAI/Gemini)
  and OCR; build tiny synthetic PDFs/CSVs in-test. Unit tests for `services/`, integration
  tests for blueprints.
- **Coverage:** aim ~90%, but gate CI at a **~85% floor** (`--cov-fail-under`), treating
  coverage as a find-the-gaps signal, not a quality score (per testing.md).
- **CI secret-scan:** drop the licensed `gitleaks-action`; run the **free gitleaks binary
  via the existing pre-commit hook** in CI instead, and add a `pytest --cov` step.

## Alternatives
- Hard-gate exactly 90% - brittle, forces low-value mock tests. Rejected.
- Buy a gitleaks license - unnecessary; the binary/pre-commit hook is free.

## Consequences
- CI runs lint + format + type (advisory) + tests + secret scan, all license-free.
- A safety net for future refactors; coverage visible in CI.
