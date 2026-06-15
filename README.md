# AI Medical Record Review (MRR AI)

Flask app that segments large medical-record PDFs into sub-documents, categorizes
them, and produces summarized Medical Record Review (MRR) reports.

> **PHI / HIPAA:** This app processes real patient medical records and sends content
> to OpenAI and Gemini. Never commit patient data. Sample PDFs/CSVs and uploads are
> git-ignored. Stricter PHI review is required on every PR.

## Pipeline (high level)

1. **Segment** a medical-record PDF into sub-documents (page ranges) — Gemini.
2. **Categorize** each sub-document into one of the report categories.
3. **Summarize** each sub-document with a category-specific prompt — OpenAI.
4. **Export** the assembled review to a Word document.

The 6-column CSV (`start,end,category,date,injury_date,manual_flag`) is the contract
between segmentation/categorization and summarization.

## Setup

### 1. Secrets

Copy `.env.example` to `.env` and fill in your keys (the app fails fast at startup if
they are missing):

```
cp .env.example .env
# then edit .env
```

`GEMINI_API_KEY` and `OPENAI_API_KEY` are required. Rotate the keys from the original
handoff — they were stored in plaintext and are considered compromised.

### 2. System dependencies

OCR and PDF rasterization need native binaries:

- **Tesseract OCR**
- **Poppler** (required by `pdf2image`)

(These are encapsulated by the Dockerfile — see the modernization PR.)

### 3. Python dependencies

Baseline uses `requirements.txt`. The project is migrating to **uv** for dependency
and Python-version management (see the `chore/modernize-tooling` PR).

```
pip install -r requirements.txt
python app.py
```

## Status

Pre-MVP, working toward MVP. Active workstreams: dependency modernization (uv, pypdf,
google-genai, Docker), Gemini segmentation fixes, and a categorization cascade.
