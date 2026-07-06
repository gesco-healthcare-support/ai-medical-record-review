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

`SECRET_KEY` and `SECURITY_PASSWORD_SALT` are also required (sessions + password
hashing). Generate each once per machine and keep them stable - rotating SECRET_KEY
logs everyone out; rotating the salt invalidates every stored password:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Dependencies (uv)

This project uses [uv](https://docs.astral.sh/uv/) for dependency and Python-version
management. Python 3.12 is pinned via `.python-version`; uv installs it automatically.

```bash
uv sync                 # create .venv and install locked dependencies
uv run python app.py    # dev server on http://localhost:5010
uv run python serve.py  # production serving (waitress)
```

First run: register an account at `/register`, then upload from the landing page.
Every route requires login; users see only their own documents.

> **Single process only.** Background document pipelines run on an in-process worker
> pool and the classic UI keeps module-level globals, so the app must never run under
> a multi-process server (gunicorn workers, multiple containers on one DB). `serve.py`
> runs waitress with one process and scales threads instead.

User accounts, documents, corrected rows, and summaries live in SQLite at
`instance/mrr.db` (auto-created; git-ignored). Uploaded PDFs live under
`uploads/<user_id>/`. Back up both together while the app is stopped (or use
`sqlite3 instance/mrr.db ".backup backup.db"` live). The schema is created with
`db.create_all()`, which never ALTERs existing tables - the first post-release schema
change must introduce Alembic migrations (or, pre-release, delete `instance/mrr.db`).

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

## Development

Quality gates run via pre-commit and CI:

```bash
uv sync                      # installs dev tools (ruff, pyright, pre-commit)
uv run pre-commit install    # enable git hooks
uv run ruff check .          # lint
uv run ruff format .         # format
uv run pyright               # type check (advisory while code is untyped)
```

Pre-commit also runs gitleaks, detect-private-key, and a large-file check to keep
secrets and patient PDFs out of git. CI (GitHub Actions) runs the same lint / format /
type / secret checks on every PR.

## Status

Pre-MVP, working toward MVP. Done: repo + secret externalization, uv/Docker tooling.
Next: SDK migration (`google-genai`, `pypdf`) + Python 3.13, Gemini segmentation fixes,
and a categorization cascade.
