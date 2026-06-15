"""Configuration and fail-fast secret validation."""

import os

from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = ("GEMINI_API_KEY", "OPENAI_API_KEY")

MAX_CONTENT_LENGTH = 1024 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf"}
# Relative base dir for individual-MRR patient folders (matches original behavior).
UPLOAD_BASE_DIR = "uploads"

# Absolute uploads dir at the repo root (one level above this package), preserving the
# original location used when everything lived in app.py.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(_REPO_ROOT, "uploads")


def validate_env():
    """Raise if required secrets are missing (see .env.example)."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )
