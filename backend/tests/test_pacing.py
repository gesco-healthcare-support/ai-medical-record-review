"""Adaptive pacing: dual meters plus an AIMD controller.

These drive the controller through explicit success/rejection sequences rather than sleeping on a
real clock, so the convergence behaviour is pinned rather than hoped for. No network, no Redis - a
fake stands in for both.

Why the design under test looks like this, in one line each (full reasoning in pacing.py):
- Vertex publishes NOTHING on a 429, so the rate has to be discovered by probing.
- The serviceable rate moved >4x across two days, so a constant cannot be right.
- Whether DSQ meters requests or tokens could not be established, so both are metered.
"""

import pytest

from app.services.llm import pacing


class FakeRedis:
    """Enough Redis for the pacer: string get/set with nx/ex, eval of the bucket script, scan."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    def scan_iter(self, match=None):
        prefix = (match or "").rstrip("*")
        suffix = None
        if match and match.endswith(":rate"):
            prefix, suffix = match.split("*", 1)
        for key in list(self.store):
            if key.startswith(prefix) and (suffix is None or key.endswith(suffix)):
                yield key

    def eval(self, _script, _nkeys, tokens_key, ts_key, rate, capacity, now_ms, cost):
        # Faithful re-implementation of the Lua: refill by elapsed time, then consume if possible.
        tokens = float(self.store.get(tokens_key, capacity))
        ts = float(self.store.get(ts_key, now_ms))
        tokens = min(capacity, tokens + max(0.0, now_ms - ts) / 1000.0 * rate)
        if tokens >= cost:
            self.store[tokens_key] = str(tokens - cost)
            self.store[ts_key] = str(now_ms)
            return [1, 0]
        self.store[tokens_key] = str(tokens)
        self.store[ts_key] = str(now_ms)
        return [0, int(((cost - tokens) / rate) * 1000)]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(pacing, "get_redis", lambda: fake)
    return fake


def _rate(redis, meter, provider="gemini", model="m"):
    return float(redis.store[pacing._key(provider, model, meter, "rate")])


def test_first_use_starts_at_the_ceiling(redis):
    # A cold worker should not be slow for no reason: the ceiling is already a deliberate bound.
    req_ceiling, _ = pacing.ceilings("gemini")
    assert pacing._get_rate(redis, "gemini", "m", "req", req_ceiling) == pytest.approx(
        req_ceiling / 60.0
    )


def test_rejection_halves_both_meters(redis):
    req_ceiling, tok_ceiling = pacing.ceilings("gemini")
    pacing.record_rejection("gemini", "m")
    # Both, because a 429 does not say WHICH limit was hit.
    assert _rate(redis, "req") == pytest.approx(req_ceiling / 60.0 * 0.5)
    assert _rate(redis, "tok") == pytest.approx(tok_ceiling / 60.0 * 0.5)


def test_a_burst_of_rejections_backs_off_once_not_once_each(redis):
    # Under concurrency N one overload trips many in-flight calls; halving per rejection would
    # collapse the rate by 2^N for what is really a single event.
    req_ceiling, _ = pacing.ceilings("gemini")
    for _ in range(5):
        pacing.record_rejection("gemini", "m")
    assert _rate(redis, "req") == pytest.approx(req_ceiling / 60.0 * 0.5)


def test_success_increases_gradually_and_never_exceeds_the_ceiling(redis):
    req_ceiling, _ = pacing.ceilings("gemini")
    pacing.record_rejection("gemini", "m")
    halved = _rate(redis, "req")
    pacing.record_success("gemini", "m")
    climbed = _rate(redis, "req")
    assert climbed > halved
    for _ in range(2000):
        pacing.record_success("gemini", "m")
    assert _rate(redis, "req") == pytest.approx(req_ceiling / 60.0)


def test_increase_is_additive_so_recovery_does_not_depend_on_how_far_it_fell(redis):
    # REGRESSION: an earlier version added a fraction of the CURRENT rate - multiplicative increase,
    # which self-traps. Observed live on 2026-08-05: 2.5-pro sat at the 2% floor and needed ~198
    # successes to recover, roughly eight hours at the rate the floor allowed.
    req_ceiling, _ = pacing.ceilings("gemini")
    per_second = req_ceiling / 60.0
    # Drive it to the floor.
    for _ in range(40):
        redis.store.pop(pacing._key("gemini", "m", "any", "cooldown"), None)
        pacing.record_rejection("gemini", "m")
    floored = _rate(redis, "req")
    pacing.record_success("gemini", "m")
    step = _rate(redis, "req") - floored
    # The step is a fraction of the CEILING, so it is the same size wherever the rate currently sits.
    assert step == pytest.approx(per_second * pacing._INCREASE_FRACTION, rel=1e-6)
    # Which means full recovery takes a bounded, linear number of successes rather than hundreds.
    needed = 0
    while _rate(redis, "req") < per_second and needed < 500:
        pacing.record_success("gemini", "m")
        needed += 1
    assert needed <= 60


def test_rate_never_falls_to_zero(redis):
    # Converging to zero would stall the pipeline outright.
    req_ceiling, _ = pacing.ceilings("gemini")
    for i in range(40):
        redis.store.pop(pacing._key("gemini", "m", "any", "cooldown"), None)
        pacing.record_rejection("gemini", "m")
    floor = req_ceiling / 60.0 * pacing._MIN_RATE_FRACTION
    assert _rate(redis, "req") >= floor


def test_token_meter_admits_a_request_larger_than_the_burst_allowance(redis):
    # A 30,000-token segmentation window must be admittable even though it dwarfs a 1s burst.
    assert pacing.acquire("gemini", "m", est_tokens=30_000, max_wait_s=0.1) is True


def test_token_meter_throttles_a_second_huge_request(redis):
    tok_ceiling = pacing.ceilings("gemini")[1]
    huge = int(tok_ceiling)  # a full minute of token budget in one call
    assert pacing.acquire("gemini", "m", est_tokens=huge, max_wait_s=0.1) is True
    # The bucket is now drained, so the next one cannot be admitted within a tenth of a second.
    assert pacing.acquire("gemini", "m", est_tokens=huge, max_wait_s=0.1) is False


def test_vertex_gets_a_smaller_burst_than_openai():
    # Google's DSQ guidance is to avoid sharp second-level spikes; OpenAI publishes fixed limits and
    # says nothing about burst shape.
    assert pacing._BURST_SECONDS["gemini"] < pacing._BURST_SECONDS["openai"]


def test_openai_headers_override_the_controller(redis):
    # Where the provider states remaining capacity, probing for it is how you cause avoidable 429s.
    pacing.observe_limits("openai", "gpt-4.1-mini", 600, 60, 120_000, 60)
    assert _rate(redis, "req", provider="openai", model="gpt-4.1-mini") == pytest.approx(10.0)
    assert _rate(redis, "tok", provider="openai", model="gpt-4.1-mini") == pytest.approx(2000.0)


def test_observe_limits_ignores_missing_or_zero_values(redis):
    pacing.observe_limits("openai", "m2", None, 60, 100, 0)
    assert pacing._key("openai", "m2", "req", "rate") not in redis.store
    assert pacing._key("openai", "m2", "tok", "rate") not in redis.store


def test_models_do_not_share_a_budget(redis):
    # The whole point: flash was previously throttled by pro's scarcity through one global bucket.
    pacing.record_rejection("gemini", "gemini-2.5-pro")
    assert pacing._key("gemini", "gemini-2.5-flash", "req", "rate") not in redis.store


def test_fails_open_when_redis_is_unreachable(monkeypatch):
    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(pacing, "get_redis", boom)
    # Halting all inference because the pacer is unavailable is worse than a burst of 429s.
    assert pacing.acquire("gemini", "m", 100) is True
    pacing.record_rejection("gemini", "m")  # must not raise
    pacing.record_success("gemini", "m")


def test_disabled_when_ceilings_are_zero(monkeypatch, redis):
    monkeypatch.setattr(pacing, "ceilings", lambda provider: (0.0, 0.0))
    assert pacing.acquire("gemini", "m", 5_000_000) is True


def test_snapshot_reports_per_minute_rates(redis):
    pacing.record_rejection("gemini", "m")
    snap = pacing.snapshot()
    assert "gemini:m:req" in snap
    assert snap["gemini:m:req"] > 0
