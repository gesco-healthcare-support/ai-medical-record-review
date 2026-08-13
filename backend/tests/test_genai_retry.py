"""Seam-level cross-cutting logic in genai_retry: the thinking-default applied to every call, and
which failures the retry loop rides out versus re-raises immediately.

Pure-Python (no Vertex, no DB, no Redis - the pacer, metrics and backoff sleep are patched out).
Proves the thinking default is applied centrally, that an explicit per-call thinking_config always
wins, and that a deterministic deadline 504 costs ONE attempt instead of the whole retry budget.
"""

import pytest
from google.genai import errors, types

from app.config import get_settings
from app.services import genai_retry
from app.services.genai_retry import (
    _apply_thinking_default,
    _parse_duration,
    _retry_delay_seconds,
    _sleep_for,
    generate_with_retry,
)


class _FakeExc(Exception):
    """Stand-in for a google.genai ClientError carrying a parsed error body in .details."""

    def __init__(self, details):
        self.details = details


def test_thinking_default_applied_when_config_has_none():
    config = types.GenerateContentConfig(temperature=0.0)
    assert config.thinking_config is None
    _apply_thinking_default(config)
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == get_settings().gemini_thinking_budget


def test_explicit_thinking_config_is_preserved():
    # A call that opts into thinking (e.g. a validated-to-need-it task) must not be overridden.
    config = types.GenerateContentConfig(
        temperature=0.0, thinking_config=types.ThinkingConfig(thinking_budget=512)
    )
    _apply_thinking_default(config)
    assert config.thinking_config.thinking_budget == 512


def test_mapping_config_gets_thinking_default():
    config: dict = {"temperature": 0.0}
    _apply_thinking_default(config)
    assert isinstance(config["thinking_config"], types.ThinkingConfig)
    assert config["thinking_config"].thinking_budget == get_settings().gemini_thinking_budget


def test_mapping_config_with_thinking_is_preserved():
    sentinel = types.ThinkingConfig(thinking_budget=256)
    config = {"temperature": 0.0, "thinking_config": sentinel}
    _apply_thinking_default(config)
    assert config["thinking_config"] is sentinel


def test_none_config_is_a_noop():
    _apply_thinking_default(None)  # must not raise


# --- retryDelay parsing (google.rpc.RetryInfo) ------------------------------------------------


def test_parse_duration_string_and_dict():
    assert _parse_duration("17s") == 17.0
    assert _parse_duration("1.500s") == 1.5
    assert _parse_duration({"seconds": 5, "nanos": 500_000_000}) == 5.5
    assert _parse_duration({"seconds": 2}) == 2.0


def test_parse_duration_bad_values_return_none():
    assert _parse_duration("nope") is None
    assert _parse_duration("17") is None  # no trailing 's'
    assert _parse_duration(None) is None
    assert _parse_duration(42) is None


def test_retry_delay_from_error_wrapped_details():
    exc = _FakeExc(
        {
            "error": {
                "code": 429,
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}
                ],
            }
        }
    )
    assert _retry_delay_seconds(exc) == 17.0


def test_retry_delay_from_unwrapped_details():
    exc = _FakeExc(
        {
            "details": [
                {"@type": ".../google.rpc.RetryInfo", "retryDelay": {"seconds": 3, "nanos": 0}}
            ]
        }
    )
    assert _retry_delay_seconds(exc) == 3.0


def test_retry_delay_absent_or_malformed_returns_none():
    assert _retry_delay_seconds(_FakeExc({"error": {"code": 429, "details": []}})) is None


# --- what the loop rides out vs re-raises -----------------------------------------------------


class _NullTimer:
    """Stand-in for genai_metrics.WaitTimer: context manager plus flush, no Redis."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def flush(self):
        pass


class _FakeClient:
    """Minimal genai client whose generate_content always raises ``exc``, counting attempts."""

    def __init__(self, exc):
        outer = self
        self.exc = exc
        self.calls = 0

        class _Models:
            def generate_content(self, **kwargs):
                outer.calls += 1
                raise outer.exc

        self.models = _Models()


def _deadline_error() -> errors.ServerError:
    """The 504 Vertex returns when the per-request deadline WE set is exceeded."""
    return errors.ServerError(
        504,
        {
            "error": {
                "code": 504,
                "message": "Deadline expired before operation could complete.",
                "status": "DEADLINE_EXCEEDED",
            }
        },
    )


@pytest.fixture
def quiet_seam(monkeypatch):
    """Patch out the Redis-backed pacer/metrics and the backoff sleep so the loop is pure-Python."""
    monkeypatch.setattr(genai_retry.pacing, "acquire", lambda *a, **k: None)
    monkeypatch.setattr(genai_retry.pacing, "record_rejection", lambda *a, **k: None)
    monkeypatch.setattr(genai_retry.pacing, "record_success", lambda *a, **k: None)
    monkeypatch.setattr(genai_retry.genai_metrics, "record", lambda *a, **k: None)
    monkeypatch.setattr(genai_retry.genai_metrics, "WaitTimer", lambda model: _NullTimer())
    monkeypatch.setattr(genai_retry, "_cancellable_sleep", lambda total: None)


def test_deadline_504_is_not_retried(quiet_seam):
    """The deadline is OURS and binds every attempt identically, so a retry re-runs the same doomed
    call. Measured on the server 2026-08-12: eight identical 504s over 17.5 minutes before the
    reviewer saw anything. One attempt is the entire point of the carve-out."""
    client = _FakeClient(_deadline_error())
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="gemini-2.5-flash")
    assert client.calls == 1


def test_other_5xx_still_rides_out_the_full_budget(quiet_seam):
    # A 503 really is transient high demand, so the carve-out must not swallow the rest of the 5xx.
    client = _FakeClient(errors.ServerError(503, {"error": {"code": 503, "message": "overloaded"}}))
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="gemini-2.5-flash")
    assert client.calls == get_settings().genai_max_retries
    assert _retry_delay_seconds(_FakeExc({"error": {"message": "x"}})) is None
    assert _retry_delay_seconds(_FakeExc("not a dict")) is None
    assert _retry_delay_seconds(_FakeExc(None)) is None


# --- delay selection --------------------------------------------------------------------------


def test_sleep_for_honors_retry_after_with_small_jitter():
    # retry_after 17s, max_delay 30 -> in [17, 18] (server delay + <=1s jitter, under the cap).
    for _ in range(20):
        d = _sleep_for(0, 17.0)
        assert 17.0 <= d <= 18.0


def test_sleep_for_caps_at_max_delay():
    # A server delay beyond the configured ceiling is clamped.
    assert _sleep_for(0, 100.0) == get_settings().genai_retry_max_delay


def test_sleep_for_falls_back_to_backoff():
    # No server delay -> full-jitter backoff in [0, min(max_delay, base*2**attempt)].
    ceiling = min(
        get_settings().genai_retry_max_delay, get_settings().genai_retry_base_delay * (2**0)
    )
    for _ in range(20):
        d = _sleep_for(0, None)
        assert 0.0 <= d <= ceiling
