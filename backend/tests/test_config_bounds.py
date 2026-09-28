"""Settings that have a lower bound refuse to boot below it.

`Settings` reads the ENVIRONMENT; uppercase constructor kwargs are silently ignored as extras, so
each case sets a real env var (the same trap `test_openai_config_guards.py` documents).
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE = {
    "SECRET_KEY": "x" * 32,
    "SECURITY_PASSWORD_SALT": "y" * 16,
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "ENVIRONMENT": "dev",
}


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    for name, value in _BASE.items():
        monkeypatch.setenv(name, value)


def test_a_retry_count_below_one_refuses_to_boot(monkeypatch):
    """IF GENAI_MAX_RETRIES is below 1, THEN THE SYSTEM SHALL refuse to boot, naming the setting.

    The value is the number of ATTEMPTS, not extra retries: at 0 `generate_with_retry` never enters
    its loop and `raise last` raises None - a TypeError on every model call, far from the cause.
    """
    monkeypatch.setenv("GENAI_MAX_RETRIES", "0")
    with pytest.raises(ValidationError, match="genai_max_retries"):
        Settings()  # type: ignore[call-arg]


def test_a_single_attempt_is_allowed(monkeypatch):
    monkeypatch.setenv("GENAI_MAX_RETRIES", "1")
    assert Settings().genai_max_retries == 1  # type: ignore[call-arg]
