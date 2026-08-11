"""Configuration and fail-fast secret validation."""

import os

from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ("GEMINI_API_KEY", "OPENAI_API_KEY", "SECRET_KEY", "SECURITY_PASSWORD_SALT")

MAX_CONTENT_LENGTH = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}
# Relative base dir for individual-MRR patient folders (matches original behavior).
UPLOAD_BASE_DIR = "uploads"

# Absolute uploads dir at the repo root (one level above this package), preserving the
# original location used when everything lived in app.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(_REPO_ROOT, "uploads")


# Tesseract binary override for machines where it is installed but not on PATH
# (Windows installer default). Empty -> pytesseract uses PATH lookup.
TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")

# --- Web sessions / auth / persistence --------------------------------------------------
# SECRET_KEY signs session cookies; SECURITY_PASSWORD_SALT is Flask-Security's HMAC salt.
# Both are REQUIRED (validate_env fails fast): a guessable default would silently break
# every security property. Generate: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
SECURITY_PASSWORD_SALT = os.environ.get("SECURITY_PASSWORD_SALT", "")

# SQLite next to the app by default (single-box deployment); the ORM keeps a later
# move to SQL Server/Postgres a config change, not a rewrite.
SQLALCHEMY_DATABASE_URI = os.environ.get("SQLALCHEMY_DATABASE_URI") or (
    "sqlite:///" + os.path.join(_REPO_ROOT, "instance", "mrr.db")
)

# Concurrent document pipelines (segment/summarize workers). Kept at 2 because all jobs
# share one Vertex quota pool - more workers just trade throughput for 429 storms.
PIPELINE_WORKERS = int(os.environ.get("PIPELINE_WORKERS", 2))

# --- Gemini routing -------------------------------------------------------------------
# Vertex AI is the BAA-covered Gemini platform (BAA signed 2026-07); the AI Studio
# Developer API is NOT covered - PHI processing requires GOOGLE_GENAI_USE_VERTEXAI=true.
USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Vertex has no "-latest" model aliases, so the default is chosen per endpoint.
GENAI_MODEL = os.environ.get("GENAI_MODEL") or (
    "gemini-2.5-flash" if USE_VERTEX else "gemini-flash-latest"
)

# Summaries run on Gemini too (Adrian, 2026-07-05): keeps the whole pipeline on the
# BAA-covered Vertex path and off the unfunded OpenAI account. Override to experiment.
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL") or GENAI_MODEL

# Retry tuning for transient 429 (Vertex dynamic shared quota) and 5xx overload.
GENAI_MAX_RETRIES = int(os.environ.get("GENAI_MAX_RETRIES", 6))
GENAI_RETRY_BASE_DELAY = float(os.environ.get("GENAI_RETRY_BASE_DELAY", 2.0))
GENAI_RETRY_MAX_DELAY = float(os.environ.get("GENAI_RETRY_MAX_DELAY", 30.0))

# Sliding-window segmentation: windows are packed to a raw-byte budget (Vertex inline
# requests cap at ~20 MB after base64) and overlap so no document is severed at a seam.
WINDOW_BUDGET_MB = float(os.environ.get("WINDOW_BUDGET_MB", 12.5))
WINDOW_OVERLAP = int(os.environ.get("WINDOW_OVERLAP", 30))

# Per-row categorization runs on a small thread pool: sequential rows took 1-2h on a
# 294-page case under quota congestion (measured 2026-07-05). Kept modest so the
# shared Gemini quota pool is not stormed; raise cautiously.
CLASSIFY_WORKERS = int(os.environ.get("CLASSIFY_WORKERS", 4))

# Boundary verification merge pass (measured recall-safe): suspect boundaries get one
# two-page check and refuted ones merge away. Disable only to isolate raw model output.
VERIFY_MERGE = os.environ.get("VERIFY_MERGE", "1").strip().lower() not in ("0", "false", "no")

# The verification JUDGE may be a stronger model than the segmenter: verdict quality is
# what gates automatic merging, and the call volume (~100 tiny questions/case) is cheap.
VERIFY_MODEL = os.environ.get("VERIFY_MODEL") or GENAI_MODEL

# Category-bundle on-demand summarize (Diagnostic&Operative / Depositions pages) runs
# synchronously, so bound the record count to keep the request within a reasonable
# latency; above this the UI routes the user to the main Summaries flow instead.
BUNDLE_SUMMARIZE_CAP = int(os.environ.get("BUNDLE_SUMMARIZE_CAP", 40))

# Wide suggest net (measured 2026-07-09): every adjacent boundary is a merge-suggestion
# candidate - confident over-splits carry no computable signature, so the old triggers
# alone caught ~1-3 per case vs 5-17 for the full net. The cap bounds huge bundles to
# O(cap) verify calls; above it the measured high-yield triggers keep their slots first.
VERIFY_SUSPECT_CAP = int(os.environ.get("VERIFY_SUSPECT_CAP", 200))

# Boundary-page OCR text as extra oracle evidence (continuation sentences, "page N of M"
# pagination). Off restores the image-only oracle: a clean A/B switch and an instant
# rollback for the text change.
VERIFY_USE_TEXT = os.environ.get("VERIFY_USE_TEXT", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


def validate_env():
    """Raise if required secrets are missing (see .env.example)."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )
