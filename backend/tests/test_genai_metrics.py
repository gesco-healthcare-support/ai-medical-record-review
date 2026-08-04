"""Vertex call accounting: outcomes are counted per attempt, and never break the caller.

The point of these counters is to make "did rejections rise when we raised the concurrency?"
answerable, so the tests that matter are: a retried 429 is still counted (it used to vanish), and a
Redis failure degrades to silence rather than killing a summarize job.
"""

import httpx
import pytest
from google.genai import errors

from app.services import genai_metrics, genai_retry


class FakeRedis:
    """Minimal hash store standing in for Redis; records writes so tests can assert on them."""

    def __init__(self, fail=False):
        self.hashes: dict[str, dict[str, int]] = {}
        self.fail = fail

    def hincrby(self, key, field, amount=1):
        if self.fail:
            raise RuntimeError("redis down")
        self.hashes.setdefault(key, {})
        self.hashes[key][field] = self.hashes[key].get(field, 0) + amount
        return self.hashes[key][field]

    def scan_iter(self, match=None):
        if self.fail:
            raise RuntimeError("redis down")
        return iter(list(self.hashes))

    def hgetall(self, key):
        return self.hashes.get(key, {})

    def delete(self, *keys):
        for key in keys:
            self.hashes.pop(key, None)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(genai_metrics, "get_redis", lambda: fake)
    return fake


def test_records_outcome_per_model(redis):
    genai_metrics.record("gemini-2.5-pro", genai_metrics.OUTCOME_ACCEPTED)
    genai_metrics.record("gemini-2.5-pro", genai_metrics.OUTCOME_RATE_LIMITED)
    genai_metrics.record("gemini-2.5-flash", genai_metrics.OUTCOME_ACCEPTED)

    snapshot = genai_metrics.snapshot()
    assert snapshot["gemini-2.5-pro"][genai_metrics.OUTCOME_ACCEPTED] == 1
    assert snapshot["gemini-2.5-pro"][genai_metrics.OUTCOME_RATE_LIMITED] == 1
    assert snapshot["gemini-2.5-flash"][genai_metrics.OUTCOME_ACCEPTED] == 1
    # Separate hashes: a per-model rate limit can only be set from per-model evidence.
    assert genai_metrics.OUTCOME_RATE_LIMITED not in snapshot["gemini-2.5-flash"]


def test_reset_clears_counters(redis):
    genai_metrics.record("m", genai_metrics.OUTCOME_ACCEPTED)
    genai_metrics.reset()
    assert genai_metrics.snapshot() == {}


def test_record_never_raises_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(genai_metrics, "get_redis", lambda: FakeRedis(fail=True))
    # Accounting that breaks a pipeline job would cost far more than the measurement is worth.
    genai_metrics.record("m", genai_metrics.OUTCOME_ACCEPTED)
    genai_metrics.record_wait("m", 1.0)
    assert genai_metrics.snapshot() == {}


def _client(responses):
    """A client whose generate_content yields each item in turn; exceptions are raised."""

    class Models:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **_kwargs):
            item = responses[self.calls]
            self.calls += 1
            if isinstance(item, Exception):
                raise item
            return item

    class Client:
        def __init__(self):
            self.models = Models()

    return Client()


def _rate_limited():
    """A 429 shaped the way generate_with_retry checks for it (code attribute)."""
    exc = errors.ClientError.__new__(errors.ClientError)
    exc.code = 429
    exc.details = None
    exc.message = "RESOURCE_EXHAUSTED"
    return exc


@pytest.fixture
def no_sleep_no_limiter(monkeypatch):
    monkeypatch.setattr(genai_retry, "_cancellable_sleep", lambda _s: None)
    monkeypatch.setattr(genai_retry.rate_limit, "acquire", lambda: True)


def test_retried_rate_limit_is_still_counted(redis, no_sleep_no_limiter, monkeypatch):
    monkeypatch.setattr(genai_metrics, "get_redis", lambda: redis)
    client = _client([_rate_limited(), _rate_limited(), "ok"])

    result = genai_retry.generate_with_retry(client, model="gemini-2.5-pro")

    assert result == "ok"
    fields = genai_metrics.snapshot()["gemini-2.5-pro"]
    # The whole point: two rejections preceded a success and both are visible. Before this existed
    # they left no trace, so a 60%-rejection run looked identical to a clean one.
    assert fields[genai_metrics.OUTCOME_RATE_LIMITED] == 2
    assert fields[genai_metrics.OUTCOME_ACCEPTED] == 1


def test_transport_and_server_errors_are_distinguished(redis, no_sleep_no_limiter, monkeypatch):
    monkeypatch.setattr(genai_metrics, "get_redis", lambda: redis)
    server = errors.ServerError.__new__(errors.ServerError)
    server.code = 503
    client = _client([server, httpx.ConnectError("dropped"), "ok"])

    genai_retry.generate_with_retry(client, model="m")

    fields = genai_metrics.snapshot()["m"]
    assert fields[genai_metrics.OUTCOME_SERVER_ERROR] == 1
    assert fields[genai_metrics.OUTCOME_TRANSPORT] == 1
    assert fields[genai_metrics.OUTCOME_ACCEPTED] == 1


def test_exhausted_call_is_counted_and_still_raises(redis, no_sleep_no_limiter, monkeypatch):
    monkeypatch.setattr(genai_metrics, "get_redis", lambda: redis)
    monkeypatch.setattr(genai_retry, "get_settings", _settings_with_retries(2))
    client = _client([_rate_limited(), _rate_limited()])

    with pytest.raises(errors.ClientError):
        genai_retry.generate_with_retry(client, model="m")

    fields = genai_metrics.snapshot()["m"]
    assert fields[genai_metrics.OUTCOME_RATE_LIMITED] == 2
    # `exhausted` marks a logical call that never succeeded; it is deliberately NOT an attempt, so
    # the rejection rate denominator stays honest.
    assert fields[genai_metrics.OUTCOME_EXHAUSTED] == 1


def _settings_with_retries(count):
    from app.config import get_settings

    real = get_settings()

    class Stub:
        genai_max_retries = count
        genai_retry_base_delay = real.genai_retry_base_delay
        genai_retry_max_delay = real.genai_retry_max_delay
        gemini_thinking_budget = real.gemini_thinking_budget

    return lambda: Stub()
