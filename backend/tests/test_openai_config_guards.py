"""Startup guards for the OpenAI provider.

These fail at BOOT rather than on the first summary on purpose: a worker that starts and then errors
per row burns a job and leaves the reviewer with a half-processed document.

The ZDR guard is the one that matters most. A signed BAA does not by itself permit sending PHI to
OpenAI - Zero Data Retention (or Modified Abuse Monitoring / Eyes Off) must ALSO be approved on the
organization. The flag is a human acknowledgement that someone checked, so a future box cannot start
sending medical records to an org whose retention setting nobody looked at.
"""

import pytest

from app.config import Settings

# Settings is a pydantic-settings model: it reads the ENVIRONMENT, and uppercase constructor kwargs
# are silently ignored as extras. So these must be set as real env vars or the guards never fire -
# which is exactly how an earlier version of this file passed while testing nothing.
_BASE = {
    "SECRET_KEY": "x" * 32,
    "SECURITY_PASSWORD_SALT": "y" * 16,
    "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "ENVIRONMENT": "dev",
}
_OPENAI_KEYS = (
    "OPENAI_API_KEY",
    "SUMMARY_BODY_MODEL",
    "SUMMARY_TITLE_MODEL",
    "AUDIT_MODEL",
    "OPENAI_ZDR_ACKNOWLEDGED",
    "SUMMARY_PROVIDER",
    "SUMMARY_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from a known env, so a developer's .env cannot mask a guard."""
    for name in _OPENAI_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in _BASE.items():
        monkeypatch.setenv(name, value)


def _settings(monkeypatch, **overrides):
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    return Settings()  # type: ignore[call-arg]


def test_gemini_provider_needs_none_of_the_openai_keys(monkeypatch):
    # The default path must stay unaffected by anything added for OpenAI.
    assert _settings(monkeypatch, SUMMARY_PROVIDER="gemini").summary_provider == "gemini"


@pytest.mark.parametrize(
    "missing",
    ["OPENAI_API_KEY", "SUMMARY_BODY_MODEL", "SUMMARY_TITLE_MODEL", "AUDIT_MODEL"],
)
def test_openai_provider_refuses_to_start_without_each_required_key(missing, monkeypatch):
    keys = {
        "OPENAI_API_KEY": "sk-test",
        "SUMMARY_BODY_MODEL": "a",
        "SUMMARY_TITLE_MODEL": "b",
        "AUDIT_MODEL": "c",
    }
    keys.pop(missing)
    with pytest.raises(RuntimeError, match=missing):
        _settings(monkeypatch, SUMMARY_PROVIDER="openai", **keys)


def test_the_error_says_why_there_is_no_default_model(monkeypatch):
    with pytest.raises(RuntimeError, match="no default model on purpose"):
        _settings(monkeypatch, SUMMARY_PROVIDER="openai", OPENAI_API_KEY="sk-test")


def test_production_refuses_openai_without_the_zdr_acknowledgement(monkeypatch):
    with pytest.raises(RuntimeError, match="OPENAI_ZDR_ACKNOWLEDGED"):
        _settings(
            monkeypatch,
            SUMMARY_PROVIDER="openai",
            ENVIRONMENT="prod",
            OPENAI_API_KEY="sk-test",
            SUMMARY_BODY_MODEL="a",
            SUMMARY_TITLE_MODEL="b",
            AUDIT_MODEL="c",
        )


def test_production_accepts_openai_once_zdr_is_acknowledged(monkeypatch):
    settings = _settings(
        monkeypatch,
        SUMMARY_PROVIDER="openai",
        ENVIRONMENT="prod",
        OPENAI_API_KEY="sk-test",
        SUMMARY_BODY_MODEL="a",
        SUMMARY_TITLE_MODEL="b",
        AUDIT_MODEL="c",
        OPENAI_ZDR_ACKNOWLEDGED="true",
    )
    assert settings.summary_provider == "openai"


def test_model_for_returns_the_configured_model_per_call_type(monkeypatch):
    settings = _settings(
        monkeypatch,
        SUMMARY_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        SUMMARY_BODY_MODEL="body-model",
        SUMMARY_TITLE_MODEL="title-model",
        AUDIT_MODEL="audit-model",
    )
    assert settings.model_for("body") == "body-model"
    assert settings.model_for("title") == "title-model"
    assert settings.model_for("audit") == "audit-model"


def test_model_for_on_gemini_ignores_the_openai_keys(monkeypatch):
    settings = _settings(
        monkeypatch, SUMMARY_PROVIDER="gemini", SUMMARY_BODY_MODEL="should-be-ignored"
    )
    assert settings.model_for("body") == settings.summary_model


def test_provider_name_is_normalised(monkeypatch):
    assert _settings(monkeypatch, SUMMARY_PROVIDER="  GEMINI  ").summary_provider == "gemini"
