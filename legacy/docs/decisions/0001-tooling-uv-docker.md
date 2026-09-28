# ADR-0001: Adopt uv + Docker, pin Python 3.12

**Status:** Accepted

## Context
The handoff used a ~2-year-old Pipenv setup with no lockfile reproducibility, and the OCR
system deps (Tesseract, Poppler) had to be installed by hand per machine.

## Decision
Use **uv** for dependency and Python-version management (`pyproject.toml` + `uv.lock`).
Pin **Python 3.12** via `.python-version`. Add a **Dockerfile** that bundles Tesseract +
Poppler so the app runs reproducibly anywhere.

## Alternatives
- Poetry / pip+requirements (heavier or weaker reproducibility).
- Python 3.13 - rejected at the time because the then-current Gemini SDK lacked 3.13 support
  (resolved later by ADR-0002; 3.13 bump deferred).

## Consequences
- Reproducible installs; one tool for deps + Python.
- Container encapsulates the system-binary pain.
- Pinned to 3.12 until the SDK migration (ADR-0002) clears the path to 3.13.
