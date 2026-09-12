"""Seam-level cross-cutting logic in genai_retry: the thinking-default applied to every call, and
which failures the retry loop rides out versus re-raises immediately.

Pure-Python (no Vertex, no DB, no Redis - the pacer, metrics and backoff sleep are patched out).
Proves the thinking default is applied centrally, that an explicit per-call thinking_config always
wins, that a request's deadline SCALES with its size, and that a deadline 504 costs TWO attempts -
one at that deadline and one at a multiple of it - rather than the whole budget it once burned.
"""

import pytest
from google.genai import errors, types

from app.config import get_settings
from app.services import genai_retry
from app.services.genai_retry import (
    _apply_thinking_default,
    _parse_duration,
    _retry_delay_seconds,
    _set_deadline,
    _sleep_for,
    generate_with_retry,
)
from app.worker.failures import classify_failure


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


# --- the seam and the worker must agree on which failures are transient ------------------------


def _client_error(code, message):
    return errors.ClientError(code, {"error": {"code": code, "message": message, "status": "x"}})


@pytest.mark.parametrize(
    ("message", "retried", "outcome"),
    [
        ("Quota exceeded: PerDay limit for this model", False, "permanent"),
        ("free_tier quota exhausted", False, "permanent"),
        ("RESOURCE_EXHAUSTED: no shared-quota capacity right now", True, "transient"),
    ],
)
def test_the_seam_and_classify_failure_agree_on_a_429(quiet_seam, message, retried, outcome):
    """One test over BOTH halves of a coupling neither side's own tests can cover.

    `genai_retry`'s comment promises `worker.failures.classify_failure` mirrors its retryable set
    "or the two disagree", and the consequence of disagreeing is not cosmetic: transient means the
    summarize job PAUSES and auto-resumes, permanent means it ends `needs_attention`. So a 429 the
    seam gives up on but the worker calls transient would be retried forever by a job that has
    already stopped trying, and the reverse would end a job the seam was still riding out.

    They agreed before this change too - `is_daily_quota` is exactly the string test the seam
    re-implemented inline. The point is that nothing FAILED when it was duplicated. #281 is the same
    shape: two tests each pinning one side of a hand-off, and neither covering the hand-off.
    """
    exc = _client_error(429, message)
    client = _FakeClient(exc)

    with pytest.raises(errors.ClientError):
        generate_with_retry(client, model="gemini-2.5-flash")

    attempts = get_settings().genai_max_retries if retried else 1
    assert client.calls == attempts
    assert classify_failure(exc) == outcome


# --- a deadline gets ONE longer retry ------------------------------------------------------------
#
# Retrying a 504 at the SAME deadline is futile and the suite still pins that (job 1000174 burned
# eight identical 504s over 17.5 minutes). Retrying at a LONGER one is a different request, and
# without it the row is simply lost - job 1000308, an 18-page row carrying 33,058 characters plus
# the 15-image cap, while the other 19 rows of the same document succeeded.


class _RecordingClient:
    """Raises ``exc`` until ``succeed_on`` attempts, recording each call's config."""

    def __init__(self, exc, succeed_on=None):
        outer = self
        self.calls = 0
        self.timeouts = []

        class _Models:
            def generate_content(self, **kwargs):
                outer.calls += 1
                config = kwargs.get("config")
                options = getattr(config, "http_options", None)
                outer.timeouts.append(getattr(options, "timeout", None))
                if succeed_on is not None and outer.calls >= succeed_on:
                    return "ok"
                raise exc

        self.models = _Models()


def _expected_retry_deadline() -> int:
    """The deadline the ONE retry runs under: this call's own deadline times the multiplier."""
    s = get_settings()
    return int(s.effective_genai_timeout_ms(1) * s.genai_deadline_retry_multiplier)


def _config():
    return types.GenerateContentConfig(temperature=0.0)


def test_a_deadline_504_is_retried_once_at_a_longer_deadline(quiet_seam):
    """WHEN a call exceeds its deadline, THE SYSTEM SHALL retry it once with a longer one.

    This REPLACES an earlier pin asserting exactly one attempt. That pin was right about its own
    reason - a retry at the same limit re-runs a doomed call - and wrong as a general rule, which is
    the distinction the escalation turns on. The first attempt carries the client's deadline (None
    here, since the seam does not set one), the second carries the escalation.
    """
    client = _RecordingClient(_deadline_error(), succeed_on=2)
    assert generate_with_retry(client, model="gemini-2.5-flash", config=_config()) == "ok"
    assert client.calls == 2
    assert client.timeouts[0] == get_settings().effective_genai_timeout_ms(1)  # the scaled floor
    assert client.timeouts[1] == _expected_retry_deadline()


def test_a_row_that_exceeds_even_the_longer_deadline_still_fails(quiet_seam):
    """WHEN the escalated attempt also times out, THE SYSTEM SHALL fail rather than escalate again.

    The bound is what keeps the old measurement from coming back: two attempts, not eight. A row too
    large for both deadlines ends exactly where it does today, only after one more try.
    """
    client = _RecordingClient(_deadline_error())
    config = _config()
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="gemini-2.5-flash", config=config)
    assert client.calls == 2
    assert client.timeouts[1] == _expected_retry_deadline()


def test_the_escalation_can_be_turned_off(quiet_seam, monkeypatch):
    """WHEN the escalation is 0, THE SYSTEM SHALL fail on the first deadline.

    This is the pre-2026-09-11 behaviour exactly, kept reachable so a box can get it back without a
    deploy if a longer deadline ever turns out to cost more than the lost row.

    Passes on main, but VACUOUSLY - there a deadline fails on the first attempt whatever the setting
    says, so the assertion is not exercising the off switch until the switch exists.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("GENAI_DEADLINE_RETRY_MULTIPLIER", "1")
    try:
        client = _RecordingClient(_deadline_error())
        config = _config()
        with pytest.raises(errors.ServerError):
            generate_with_retry(client, model="gemini-2.5-flash", config=config)
        assert client.calls == 1
    finally:
        get_settings.cache_clear()


def test_a_deadline_is_not_backed_off_like_a_transient_error(quiet_seam, monkeypatch):
    """WHEN a deadline is retried, THE SYSTEM SHALL NOT ride out the full retry budget.

    A GUARD on the half of the old rule that survives: the escalation is one extra attempt, not a
    re-entry into the 5xx backoff path, which is what the 17.5-minute measurement was about.
    """
    client = _RecordingClient(_deadline_error())
    config = _config()
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="gemini-2.5-flash", config=config)
    assert client.calls < get_settings().genai_max_retries


def test_a_config_the_seam_cannot_patch_still_fails_fast(quiet_seam):
    """WHEN there is no config to carry a deadline, THE SYSTEM SHALL fail on the first attempt.

    Every production caller passes one, so this is the defensive path rather than an observed one -
    but silently doing nothing and then riding out eight attempts is precisely the old behaviour the
    carve-out existed to prevent.

    Passes on main VACUOUSLY, for the same reason as the off-switch test above: it pins that the
    no-config path did not gain a retry it cannot use, not that it ever had one.
    """
    client = _RecordingClient(_deadline_error())
    with pytest.raises(errors.ServerError):
        generate_with_retry(client, model="gemini-2.5-flash")
    assert client.calls == 1


def test_the_escalated_deadline_reaches_the_request_as_http_options(quiet_seam):
    """The escalation must land where google-genai reads it, not merely on our own object.

    `_api_client.patch_http_options` merges per-request HttpOptions over the client's field by field
    and a non-None patch wins, and the timeout becomes Vertex's `X-Server-Timeout` - so this is the
    field that makes the retry a genuinely longer call rather than the same one.
    """
    config = _config()
    assert _set_deadline(config, 300000) is True
    assert isinstance(config.http_options, types.HttpOptions)
    assert config.http_options.timeout == 300000
    assert _set_deadline(None, 300000) is False
    assert _set_deadline(_config(), 0) is False


# --- the deadline scales with the request --------------------------------------------------------
#
# The half that makes this hold for records larger than any we have seen. A fixed deadline is safe
# only while something bounds the request: segmentation has `window_max_pages`, summarize has
# nothing, so a row is however many pages the segmenter drew.


def _ok_client():
    """Records the deadline each attempt ran under and succeeds immediately."""

    class _C:
        def __init__(self):
            outer = self
            self.timeouts = []

            class _Models:
                def generate_content(self, **kwargs):
                    options = getattr(kwargs.get("config"), "http_options", None)
                    outer.timeouts.append(getattr(options, "timeout", None))
                    return "ok"

            self.models = _Models()

    return _C()


def test_an_ordinary_request_keeps_the_flat_deadline(quiet_seam):
    """WHEN a request is small, THE SYSTEM SHALL use `genai_http_timeout_ms` unchanged.

    The floor is what makes this change invisible to everything that works today - a title call, a
    classify call and a normal summarize row all sit far below it.
    """
    client = _ok_client()
    generate_with_retry(client, model="m", config=_config(), _est_tokens=1000)
    assert client.timeouts[0] == get_settings().genai_http_timeout_ms


def test_a_large_request_gets_a_proportionally_longer_deadline(quiet_seam):
    """WHEN a request is large, THE SYSTEM SHALL scale its deadline with its size.

    This is the durable half: the wall moves with the request instead of staying where a bounded
    path put it. Twice the tokens, twice the allowance - so a record larger than anything measured
    so far does not need a new constant, which is exactly what a fixed ceiling would have required.
    """
    settings = get_settings()
    # Comfortably past the floor, so the scaling rather than the floor is what is being asserted.
    big = int(settings.genai_http_timeout_ms / settings.genai_timeout_per_1k_tokens_ms * 1000) * 4
    client = _ok_client()
    generate_with_retry(client, model="m", config=_config(), _est_tokens=big)
    assert client.timeouts[0] == settings.effective_genai_timeout_ms(big)
    assert client.timeouts[0] > settings.genai_http_timeout_ms

    doubled = _ok_client()
    generate_with_retry(doubled, model="m", config=_config(), _est_tokens=big * 2)
    assert doubled.timeouts[0] == pytest.approx(client.timeouts[0] * 2, rel=0.01)


def test_the_retry_multiplies_the_deadline_that_call_actually_had(quiet_seam):
    """WHEN a large request times out, THE SYSTEM SHALL retry at a multiple of ITS OWN deadline.

    Not of the flat floor. A big row that exceeds its already-scaled deadline is the case with the
    least margin left, so taking the multiplier off the floor instead would hand it barely more time
    than a small row gets - the mistake that makes a size-aware limit stop being size-aware.
    """
    settings = get_settings()
    big = int(settings.genai_http_timeout_ms / settings.genai_timeout_per_1k_tokens_ms * 1000) * 4
    scaled = settings.effective_genai_timeout_ms(big)
    client = _RecordingClient(_deadline_error(), succeed_on=2)
    generate_with_retry(client, model="m", config=_config(), _est_tokens=big)
    assert client.timeouts[0] == scaled
    assert client.timeouts[1] == int(scaled * settings.genai_deadline_retry_multiplier)
