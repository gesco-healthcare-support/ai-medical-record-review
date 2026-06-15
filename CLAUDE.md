# CLAUDE.md - AI Medical Record Review (MRR AI)

Project memory for Claude Code. The global rules in `~/.claude/` apply; this file adds
project-specific context.

## What this is

Flask app (Python 3.12) that turns a large scanned medical-record PDF into a summarized
Medical Record Review (MRR). Pipeline: **segment -> categorize -> summarize -> export to Word.**

- **Stack:** Flask, google-genai (Gemini), OpenAI, pypdf, PyMuPDF, pytesseract + Poppler OCR.
- **Tooling:** uv (`pyproject.toml` + `uv.lock`), Python pinned 3.12, Docker (bundles Tesseract + Poppler).
- **Gates:** ruff (lint+format), pyright (advisory), pre-commit + gitleaks, GitHub Actions CI.
- Serves on port **5010**.

## The CSV contract (load-bearing)

6 columns: `start_page,end_page,category,doc_date,injury_date,manual_flag`. This is the
interface between segmentation/categorization and summarization. Category numbers map to
`groups.py` and `docs/reference/Categories ...docx`.

## PHI / HIPAA (strict)

- Real patient records are PHI. NEVER commit PDFs, OCR text, page-map CSVs, or patient
  names. Sample data lives outside the repo; `uploads/` and experiment caches are gitignored.
- gitleaks + detect-private-key + large-file pre-commit hooks guard every commit.
- Secrets via `.env` (never committed; see `.env.example`). Rotate the handoff keys.

## Commands

```bash
uv sync
uv run python app.py                       # run (http://localhost:5010)
uv run ruff check . && uv run ruff format . # lint + format
uv run pyright                              # type check (advisory)
uv run pre-commit run --all-files           # all gates incl. gitleaks
```

## Key references

- `docs/reference/Categories ...docx` - the category taxonomy (B6 source).
- `docs/reference/prompts/` - per-category prompt sources (became `prompts.py`).
- `docs/research/Initial-Research.md` - verified research on segmentation/OCR/summarization.
- `experiments/a1-segmentation/` - prior Page Stream Segmentation spike for the chunking fix (#4/#5).
- `docs/RUNBOOK.md` - how to run + retrieve outputs.

## Structure

`app.py` is a thin entry (`app = create_app()`); the application lives in the `mrr_ai/`
package: `config.py`, `extensions.py` (genai + openai clients), `state.py` (shared globals -
keeps the app single-process), `services/` (pdf, ocr, gemini, categorization, files),
`blueprints/` (routes grouped by area), `templates/`, `static/`. Route-smoke tests in `tests/`.

## Status / focus

Done: modularized into the `mrr_ai` package (factory + blueprints + services + route-smoke
tests); segmentation B1 fixes (temp 0, JSON schema, robust parser) on google-genai. Next:
categorization cascade (B5) + taxonomy curation (B6); then fix the deferred CI issues.
