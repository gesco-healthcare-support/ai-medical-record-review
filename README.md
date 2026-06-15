# AI Medical Record Review (MRR AI)

Flask app that segments large medical-record PDFs into sub-documents, categorizes
them, and produces summarized Medical Record Review (MRR) reports.

> **PHI / HIPAA:** This app processes real patient medical records and sends content
> to OpenAI and Gemini. Never commit patient data. Sample PDFs/CSVs and uploads are
> git-ignored. Stricter PHI review is required on every PR.

## Pipeline (high level)

1. **Segment** a medical-record PDF into sub-documents (page ranges) - Gemini.
2. **Categorize** each sub-document into one of the report categories.
3. **Summarize** each sub-document with a category-specific prompt - OpenAI.
4. **Export** the assembled review to a Word document.

The 6-column CSV (`start,end,category,date,injury_date,manual_flag`) is the contract
between segmentation/categorization and summarization.

## Setup

### 1. Secrets

Copy `.env.example` to `.env` and fill in your keys (the app fails fast at startup if
they are missing):

```bash
cp .env.example .env
# then edit .env
```

`GEMINI_API_KEY` and `OPENAI_API_KEY` are required. Rotate the keys from the original
handoff - they were stored in plaintext and are considered compromised.

### 2. Dependencies (uv)

This project uses [uv](https://docs.astral.sh/uv/) for dependency and Python-version
management. Python 3.12 is pinned via `.python-version`; uv installs it automatically.

```bash
uv sync                 # create .venv and install locked dependencies
uv run python app.py    # runs on http://localhost:5010
```

> Python 3.12 is pinned because the current (deprecated) `google-generativeai` SDK has
> no 3.13 support. The `refactor/migrate-sdks` work migrates to `google-genai` + `pypdf`
> and bumps to 3.13.

### 3. System dependencies (only for local, non-Docker runs)

OCR and PDF rasterization need native binaries, installed separately when running on the
host:

- **Tesseract OCR** (`pytesseract`)
- **Poppler** (`pdf2image`)

These are baked into the Docker image, so Docker users can skip this step.

## Run with Docker

The image bundles Tesseract and Poppler, so no host system deps are needed.

```bash
docker build -t ai-medical-record-review .
docker run --env-file .env -p 5010:5010 ai-medical-record-review
```

## Status

Pre-MVP, working toward MVP. Done: repo + secret externalization, uv/Docker tooling.
Next: SDK migration (`google-genai`, `pypdf`) + Python 3.13, Gemini segmentation fixes,
and a categorization cascade.
