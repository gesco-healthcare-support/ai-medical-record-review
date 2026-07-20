"""Redis token-bucket limiter: refill, consume, and block, against live docker Redis.

Time is injected (now_ms) so refill is deterministic without sleeping. The bucket keys are cleaned
around each test so runs are idempotent.
"""

import pytest

from app.services import rate_limit
from app.services.rate_limit import _TOKENS_KEY, _TS_KEY, acquire, try_acquire
from app.worker.queues import get_redis


@pytest.fixture
def conn():
    c = get_redis()
    c.delete(_TOKENS_KEY, _TS_KEY)
    yield c
    c.delete(_TOKENS_KEY, _TS_KEY)


def test_bucket_starts_full_then_blocks(conn):
    # capacity 3, rate 1/s, all at t=0: three consume, the fourth blocks for ~1s of refill.
    for _ in range(3):
        allowed, wait = try_acquire(conn, 1.0, 3.0, now_ms=0)
        assert allowed and wait == 0
    allowed, wait = try_acquire(conn, 1.0, 3.0, now_ms=0)
    assert not allowed
    assert wait == 1000  # one token needed, refills at 1/s


def test_refills_over_elapsed_time(conn):
    for _ in range(3):
        try_acquire(conn, 1.0, 3.0, now_ms=0)
    assert try_acquire(conn, 1.0, 3.0, now_ms=0)[0] is False
    # 2 seconds later two tokens have refilled.
    assert try_acquire(conn, 1.0, 3.0, now_ms=2000)[0] is True
    assert try_acquire(conn, 1.0, 3.0, now_ms=2000)[0] is True
    assert try_acquire(conn, 1.0, 3.0, now_ms=2000)[0] is False


def test_refill_is_capped_at_capacity(conn):
    try_acquire(conn, 1.0, 3.0, now_ms=0)  # init + consume one -> 2 left
    # A long idle must not accumulate beyond capacity (no unbounded burst later).
    for _ in range(3):
        assert try_acquire(conn, 1.0, 3.0, now_ms=10_000_000)[0] is True
    assert try_acquire(conn, 1.0, 3.0, now_ms=10_000_000)[0] is False


def test_acquire_consumes_when_available(conn):
    # Default rpm (60) against a fresh bucket: a token is immediately available.
    assert acquire() is True


def test_acquire_disabled_fails_open(monkeypatch):
    class _S:
        vertex_max_rpm = 0

    monkeypatch.setattr(rate_limit, "get_settings", lambda: _S)
    assert acquire() is True
