"""Global Redis token-bucket rate limiter for the shared Vertex quota.

Vertex gemini runs on dynamic shared quota: bursts trip 429 RESOURCE_EXHAUSTED. The segment and
summarize workers are SEPARATE processes, so an in-process semaphore cannot bound the aggregate
rate - the bucket must live in Redis. Refill-then-consume runs as one atomic Lua script so
concurrent workers never race the read-modify-write.

Design choices:
- Rate = vertex_max_rpm / 60 tokens/sec; capacity = a few seconds of burst so parallel windows can
  start together while the long-run rate stays capped (a token bucket's average is the refill rate
  regardless of capacity).
- FAIL OPEN: if Redis is unreachable the call is allowed (with a logged warning) rather than
  halting all AI - the existing backoff still rides out the occasional 429 that slips through.
- The blocking wait is bounded (MAX_ACQUIRE_WAIT_S) so a single call can never monopolize a worker
  past its RQ job timeout; on timeout it proceeds and lets retry/backoff absorb any 429.
"""

import logging
import time

from app.config import get_settings
from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

_TOKENS_KEY = "vertex:ratelimit:tokens"
_TS_KEY = "vertex:ratelimit:ts"

# Seconds of burst the bucket tolerates before it throttles to the sustained refill rate.
_BURST_SECONDS = 4.0
# Hard ceiling on a single acquire()'s blocking wait; the RQ job timeout is the outer bound, this
# just stops one call from waiting forever if the bucket is starved.
MAX_ACQUIRE_WAIT_S = 300.0
# Poll granularity while waiting for the bucket to refill.
_MIN_SLEEP_S = 0.05
_MAX_SLEEP_S = 1.0

# Atomic refill-then-consume. KEYS: tokens, ts. ARGV: rate_per_sec, capacity, now_ms, cost.
# Returns {allowed(0/1), wait_ms} where wait_ms is the time until `cost` tokens are available.
_LUA = """
local tokens = tonumber(redis.call('get', KEYS[1]))
local ts = tonumber(redis.call('get', KEYS[2]))
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
if tokens == nil then
  tokens = capacity
  ts = now
end
local elapsed = math.max(0, now - ts) / 1000.0
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
local wait_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  wait_ms = math.ceil(((cost - tokens) / rate) * 1000)
end
redis.call('set', KEYS[1], tokens)
redis.call('set', KEYS[2], now)
local ttl = math.max(60, math.ceil(capacity / rate) * 2)
redis.call('expire', KEYS[1], ttl)
redis.call('expire', KEYS[2], ttl)
return {allowed, wait_ms}
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _capacity(rate_per_sec: float) -> float:
    """Burst allowance in tokens (never below 1)."""
    return max(1.0, rate_per_sec * _BURST_SECONDS)


def try_acquire(
    conn, rate_per_sec: float, capacity: float, cost: int = 1, now_ms: int | None = None
):
    """One atomic refill+consume against `conn`. Returns (allowed: bool, wait_ms: int).

    `now_ms` is injectable so tests can advance time deterministically.
    """
    now_ms = _now_ms() if now_ms is None else now_ms
    allowed, wait_ms = conn.eval(
        _LUA, 2, _TOKENS_KEY, _TS_KEY, rate_per_sec, capacity, now_ms, cost
    )
    return bool(allowed), int(wait_ms)


def acquire(max_wait_s: float = MAX_ACQUIRE_WAIT_S) -> bool:
    """Block (short sleeps) until a Vertex token is available. Returns True once consumed.

    Returns True immediately when the limiter is disabled (vertex_max_rpm <= 0) or Redis is
    unreachable (fail open). Returns False only if `max_wait_s` elapses without a token, so the
    caller proceeds rather than exceeding its job timeout.
    """
    settings = get_settings()
    rpm = settings.vertex_max_rpm
    if rpm <= 0:
        return True
    rate = rpm / 60.0
    capacity = _capacity(rate)
    try:
        conn = get_redis()
    except Exception as exc:
        logger.warning("rate limiter: Redis unavailable, failing open: %s", exc)
        return True

    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            allowed, wait_ms = try_acquire(conn, rate, capacity)
        except Exception as exc:
            logger.warning("rate limiter: Redis error, failing open: %s", exc)
            return True
        if allowed:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning("rate limiter: waited %.0fs without a token, proceeding", max_wait_s)
            return False
        time.sleep(min(max(wait_ms / 1000.0, _MIN_SLEEP_S), _MAX_SLEEP_S, remaining))
