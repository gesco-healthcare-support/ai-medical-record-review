"""Unit tests for the jittered-backoff Gemini retry wrapper (no network, sleep patched)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from mrr_ai.services import genai_retry
from mrr_ai.services.genai_retry import generate_with_retry


def _flaky_client(failures):
    """Client whose generate_content raises each exception in `failures`, then succeeds."""
    remaining = list(failures)

    def generate_content(**kwargs):
        if remaining:
            raise remaining.pop(0)
        return SimpleNamespace(text="ok")

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(genai_retry.time, "sleep", lambda s: None)


def _server_error():
    return errors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})


def _client_error(code, message):
    return errors.ClientError(code, {"error": {"message": message, "status": "X"}})


def test_retries_transient_503_then_succeeds():
    client = _flaky_client([_server_error(), _server_error()])
    assert generate_with_retry(client, model="m", contents="c").text == "ok"


def test_retries_transient_429_then_succeeds():
    client = _flaky_client([_client_error(429, "rate limited")])
    assert generate_with_retry(client, model="m", contents="c").text == "ok"


def test_non_429_client_error_raises_immediately():
    client = _flaky_client([_client_error(404, "model not found")])
    with pytest.raises(errors.ClientError):
        generate_with_retry(client, model="m", contents="c")


def test_per_day_quota_fails_fast():
    client = _flaky_client([_client_error(429, "GenerateRequestsPerDayPerProject exceeded")])
    with pytest.raises(errors.ClientError):
        generate_with_retry(client, model="m", contents="c")


def test_exhausted_retries_raise_last_error():
    client = _flaky_client([_server_error()] * 99)
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="m", contents="c")
