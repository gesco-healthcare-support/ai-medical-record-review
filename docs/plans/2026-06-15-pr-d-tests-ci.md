---
status: in-progress
pr: D
branch: test/add-suite-and-green-ci
approach: test-after (characterize existing behavior) + code (config/CI)
---

# PR D - Test suite + green CI (incl. SonarCloud)

## Goal

Add a real pytest suite (unit + integration, externals mocked) covering the `mrr_ai`
package to ~90% (CI floor 85%), and drive GitHub Actions CI fully green - including
SonarCloud - so future runs only flag genuinely new issues, not pre-existing debt.

Docker + from-scratch setup guide are explicitly **out of scope** (deferred to PR E).

## Context / why now

- CI has been red on every run; root cause is the `secret-scan` job using
  `gitleaks/gitleaks-action@v2`, which requires a paid license for orgs. The `quality`
  job (ruff/pyright/import-smoke) passes.
- No tests run in CI today; only a 9-test route-smoke net exists (`tests/test_smoke.py`).
- SonarCloud is connected via the GitHub App in **Automatic Analysis** mode (posts a
  `neutral` check, cannot ingest coverage). To gate on coverage we move to **CI-based**
  analysis.

## Constraints / gotchas (verified against source)

- Blueprints use `from mrr_ai.extensions import client/genai_client` and
  `from mrr_ai.services.ocr import ...`, so mocks must patch the name **in the blueprint
  module** (e.g. `mrr_ai.blueprints.summarize.client`), not `extensions` post-import.
- `mrr_ai.extensions` runs `validate_env()` + builds clients at import -> conftest must set
  dummy `GEMINI_API_KEY`/`OPENAI_API_KEY` before importing the package.
- `state.py` globals mutate across requests -> autouse reset fixture between tests.
- Routes write to `~/MRRs` (reports/export) -> redirect `HOME`/`USERPROFILE` to a tmp dir.
- `UPLOAD_FOLDER` (config constant) -> override `app.config` per test; `UPLOAD_BASE_DIR`
  (reports/individual_mrr) -> monkeypatch the module global.
- HIPAA: synthetic data only. Build tiny PDFs with pypdf in-test; never commit fixtures.
- Existing quirks (e.g. `summarize_indiv_record` `if option == 1 or "1":` always-truthy,
  bare `except:`) are **characterized, not fixed** here (flag separately).

## Tasks

1. [code] `pyproject.toml`: add `pytest-cov` dev dep; `[tool.coverage.run]`
   (source=`mrr_ai`, omit tests), `[tool.coverage.report]` exclusions.
2. [test-after] `tests/conftest.py`: `app`/`client` fixtures via `create_app()` with dummy
   env; helpers to build tiny PDFs/CSVs; autouse state reset; `home_tmp` fixture redirecting
   `~`; OpenAI/Gemini fake factories.
3. [test-after] `tests/unit/`: `categorization`, `files`, `pdf`, `gemini.parse_segment_item`,
   `ocr` (mock pytesseract + pdf2image).
4. [test-after] `tests/integration/`: `pages`/`reset`, `upload`, `segmentation` (getPages
   small + large branch, segmentPDF), `summarize`, `reports`, `export`, `extraction`,
   `individual_mrr`.
5. [code] CI `secret-scan`: replace `gitleaks-action@v2` with the free gitleaks binary
   (reuse the pinned pre-commit hook via `pre-commit run gitleaks --all-files`).
6. [code] CI: add a `tests` job running `uv run pytest --cov=mrr_ai --cov-report=xml
   --cov-report=term-missing --cov-fail-under=85`.
7. [code] SonarCloud CI-based analysis: `sonar-project.properties` (projectKey, org,
   sources=`mrr_ai`, tests=`tests`, `sonar.python.coverage.reportPaths=coverage.xml`,
   exclusions for experiments/templates/static) + `SonarSource/sonarqube-scan-action`
   step after tests, using `SONAR_TOKEN`.

## Manual steps (user-only, cannot be automated here)

- [x] Add `SONAR_TOKEN` repo secret (done via `gh secret set`).
- [ ] SonarCloud -> project -> Administration -> Analysis Method -> turn **Automatic
  Analysis OFF** (else the CI scanner errors: "running CI analysis while Automatic Analysis
  is enabled").
- [ ] Rotate the SonarCloud token after CI is confirmed (it was shared in plaintext).

## Verification

- `uv run pytest --cov=mrr_ai --cov-report=term-missing` >= 85%, all green, locally.
- `uv run ruff check . && uv run ruff format --check .` clean.
- `uv run pre-commit run --all-files` green (incl. gitleaks binary).
- Push -> all GitHub checks green: `quality`, `secret-scan`, `tests`, `SonarCloud`.

## Risk / rollback

- Blast radius: additive (new tests + CI config); no production code changed.
- Rollback: revert the PR; CI returns to prior (red secret-scan) state.
- SonarCloud gate may flag new-code issues on the test files themselves; fix or mark per
  clean-as-you-code. CI scanner hard-fails until Automatic Analysis is disabled (user step).
