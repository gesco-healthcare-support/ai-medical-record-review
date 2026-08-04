"""Self-tuning request pacing: dual token buckets per model, driven by an AIMD controller.

Replaces a single hand-set requests-per-minute number, which measurement showed cannot be right.

WHY IT HAS TO BE ADAPTIVE. Vertex's dynamic shared quota publishes nothing: the 429 body is just
``{"code":429,"message":"Resource exhausted. Please try again later.","status":"RESOURCE_EXHAUSTED"}``
with no RetryInfo and no remaining-capacity header. And the serviceable rate moves: 2.5-pro rejected
75-93% at concurrency 2 on 2026-08-03, yet ran clean at concurrency 4 two days later, needing
concurrency 16 before it rejected. A constant tuned on Monday is wrong on Wednesday.

WHY TWO BUCKETS. Whether DSQ meters requests or tokens could not be established. A paired experiment
(same concurrency, same request count, 5-token vs 20,000-token payloads) was confounded: the SECOND
arm was punished either way - 12% then 50% in one order, 19% then 75% reversed - so pool DEPLETION
dominates payload size and the test could not separate the two. Metering both is correct under
either answer, and the depletion effect is itself the argument for decreasing fast and increasing
slowly.

WHY AIMD. It is the shape TCP uses to find the capacity of a link it cannot see: climb gently while
things work, back off hard the moment they do not. Occasional 429s are the FEEDBACK SIGNAL, not a
fault - the controller has to probe upward to discover it may go faster.

Where a provider does publish its limits (OpenAI returns ``x-ratelimit-remaining-*`` and reset times
on every response) that ground truth overrides the controller: see ``observe_limits``.

Fail-open throughout. If Redis is unreachable the call proceeds - halting all inference because the
pacer is unavailable would be a far worse failure than a burst of 429s.
"""

import logging
import time

from app.config import get_settings
from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

_PREFIX = "llm:pace"

# --- AIMD constants ------------------------------------------------------------------------------
# Halve on rejection. The standard multiplicative-decrease factor, and it suits a pool whose capacity
# visibly degrades under sustained load: overshooting costs a retry storm, undershooting costs a
# little latency.
_DECREASE_FACTOR = 0.5
# Additive increase per SUCCESS, as a fraction of the current rate. Deliberately small so recovery is
# gradual: the measured depletion means a fast climb would simply re-trip the limit.
_INCREASE_FRACTION = 0.02
# Never converge to zero (that would stall the pipeline) and never exceed the configured ceiling.
_MIN_RATE_FRACTION = 0.02
# After a rejection, ignore further rejections briefly. Under concurrency N a single overload trips
# many in-flight calls at once, and halving once per rejection would collapse the rate by 2^N for
# what is really one event.
_DECREASE_COOLDOWN_S = 2.0

# Seconds of burst the bucket tolerates before throttling to the sustained rate, PER PROVIDER.
#
# Vertex gets almost none. Google's own DSQ guidance is to avoid "sharp, second-level spikes" even
# when the per-minute average is within budget, and states that steady traffic is prioritised over
# bursty traffic. A 4-second burst allowance - which is what this was - lets a pool of workers fire
# exactly the spike that guidance warns against, and the token bucket would still report the average
# as fine. OpenAI publishes fixed per-project limits and says nothing about burst shape, so a small
# burst there is harmless and avoids stalling on jitter.
_BURST_SECONDS = {"gemini": 1.0, "openai": 4.0}
_BURST_SECONDS_DEFAULT = 1.0
MAX_ACQUIRE_WAIT_S = 300.0
_MIN_SLEEP_S = 0.05
_MAX_SLEEP_S = 1.0

# Atomic refill-then-consume, identical in shape to the previous single-bucket script.
# KEYS: tokens, ts. ARGV: rate_per_sec, capacity, now_ms, cost. Returns {allowed, wait_ms}.
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


def _key(provider, model, meter, suffix) -> str:
    return f"{_PREFIX}:{provider}:{model}:{meter}:{suffix}"


def ceilings(provider: str) -> tuple[float, float]:
    """(requests/min, tokens/min) upper bounds for a provider.

    These are SAFETY BOUNDS, not tuning knobs: the controller finds the working rate on its own and
    can only ever sit at or below these. OpenAI's are its published per-project limits; Vertex's
    request ceiling reuses vertex_max_rpm so an operator keeps a hard cap.
    """
    settings = get_settings()
    if provider == "openai":
        return float(settings.openai_max_rpm), float(settings.openai_max_tpm)
    return float(settings.vertex_max_rpm), float(settings.vertex_max_tpm)


def _get_rate(conn, provider, model, meter, ceiling) -> float:
    """Current per-second rate for one meter, seeded at the ceiling on first use.

    Seeding AT the ceiling rather than below it is deliberate: the ceiling is already a
    deliberately-set bound, and starting under it would make every cold worker slow for no reason.
    The first rejection halves it, which is how the controller finds the real level.
    """
    raw = conn.get(_key(provider, model, meter, "rate"))
    per_second = ceiling / 60.0
    if raw is None:
        return per_second
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return per_second
    return max(per_second * _MIN_RATE_FRACTION, min(value, per_second))


def _set_rate(conn, provider, model, meter, value) -> None:
    # A day's TTL: state that outlives a deploy is useful, state that outlives the workload is not.
    conn.set(_key(provider, model, meter, "rate"), value, ex=86400)


def record_rejection(provider: str, model: str) -> None:
    """Multiplicative decrease on BOTH meters after a 429.

    Both, because the rejection does not say which limit was hit - and under either answer, backing
    off the other one too is safe.
    """
    try:
        conn = get_redis()
        guard = _key(provider, model, "any", "cooldown")
        # set-if-absent with a TTL: the first rejection in a burst wins, the rest are absorbed.
        if not conn.set(guard, "1", nx=True, ex=int(_DECREASE_COOLDOWN_S)):
            return
        for meter, ceiling in (("req", ceilings(provider)[0]), ("tok", ceilings(provider)[1])):
            floor = ceiling / 60.0 * _MIN_RATE_FRACTION
            current = _get_rate(conn, provider, model, meter, ceiling)
            _set_rate(conn, provider, model, meter, max(floor, current * _DECREASE_FACTOR))
        logger.info("pacing: backed off %s/%s after a rejection", provider, model)
    except Exception as exc:  # noqa: BLE001 - pacing must never break a pipeline job
        logger.debug("pacing: record_rejection failed: %s", exc)


def record_success(provider: str, model: str) -> None:
    """Additive increase on both meters, capped at the ceiling."""
    try:
        conn = get_redis()
        for meter, ceiling in (("req", ceilings(provider)[0]), ("tok", ceilings(provider)[1])):
            per_second = ceiling / 60.0
            current = _get_rate(conn, provider, model, meter, ceiling)
            if current >= per_second:
                continue
            _set_rate(
                conn, provider, model, meter, min(per_second, current * (1 + _INCREASE_FRACTION))
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pacing: record_success failed: %s", exc)


def observe_limits(
    provider: str,
    model: str,
    remaining_requests,
    reset_requests_s,
    remaining_tokens,
    reset_tokens_s,
) -> None:
    """Set rates from a provider's own published headers, where it publishes them.

    OpenAI returns remaining requests/tokens and their reset windows on EVERY response, which is
    ground truth the AIMD controller can only approximate. Where that exists, use it: probing for a
    number the server is already telling you is how you cause avoidable 429s.

    `remaining / reset_seconds` is an APPROXIMATION, deliberately. OpenAI documents reset as "the
    time until the rate limit resets to its initial state", but the replenishment behaves as a
    rolling window rather than an all-at-once refill, so treating reset as a hard clock would pace
    slightly too fast near the boundary. Dividing spreads the remaining allowance across the window,
    which errs slow - the right direction, since a 429 also consumes quota (OpenAI: "unsuccessful
    requests still contribute to your per-minute limit").

    Vertex publishes nothing comparable, so nothing calls this for gemini and the AIMD controller
    carries that provider alone.
    """
    try:
        conn = get_redis()
        req_ceiling, tok_ceiling = ceilings(provider)
        for meter, remaining, reset_s, ceiling in (
            ("req", remaining_requests, reset_requests_s, req_ceiling),
            ("tok", remaining_tokens, reset_tokens_s, tok_ceiling),
        ):
            if remaining is None or not reset_s or reset_s <= 0:
                continue
            per_second = ceiling / 60.0
            floor = per_second * _MIN_RATE_FRACTION
            _set_rate(
                conn,
                provider,
                model,
                meter,
                max(floor, min(per_second, float(remaining) / float(reset_s))),
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("pacing: observe_limits failed: %s", exc)


def _try_acquire_one(conn, provider, model, meter, cost, ceiling):
    rate = _get_rate(conn, provider, model, meter, ceiling)
    burst = _BURST_SECONDS.get(provider, _BURST_SECONDS_DEFAULT)
    # Floor at `cost` so a single request larger than the burst allowance can still ever be admitted
    # - otherwise a 30,000-token segmentation window would wait forever against a small bucket.
    capacity = max(float(cost), rate * burst)
    allowed, wait_ms = conn.eval(
        _LUA,
        2,
        _key(provider, model, meter, "tokens"),
        _key(provider, model, meter, "ts"),
        rate,
        capacity,
        _now_ms(),
        cost,
    )
    return bool(allowed), int(wait_ms)


def acquire(
    provider: str, model: str, est_tokens: int = 1, max_wait_s: float = MAX_ACQUIRE_WAIT_S
) -> bool:
    """Block until BOTH meters admit this request. True once admitted.

    Returns True immediately when pacing is disabled (a ceiling of 0 or less) or Redis is
    unreachable. Returns False only when max_wait_s elapses, so the caller proceeds and lets
    retry/backoff absorb whatever happens - never blocking past the job timeout.
    """
    req_ceiling, tok_ceiling = ceilings(provider)
    if req_ceiling <= 0 and tok_ceiling <= 0:
        return True
    try:
        conn = get_redis()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pacing: Redis unavailable, failing open: %s", exc)
        return True

    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            wait_ms = 0
            for meter, cost, ceiling in (
                ("req", 1, req_ceiling),
                ("tok", max(1, int(est_tokens)), tok_ceiling),
            ):
                if ceiling <= 0:
                    continue
                allowed, meter_wait = _try_acquire_one(conn, provider, model, meter, cost, ceiling)
                if not allowed:
                    wait_ms = max(wait_ms, meter_wait)
            if wait_ms == 0:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("pacing: Redis error, failing open: %s", exc)
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning("pacing: waited %.0fs without capacity, proceeding", max_wait_s)
            return False
        time.sleep(min(max(wait_ms / 1000.0, _MIN_SLEEP_S), _MAX_SLEEP_S, remaining))


def snapshot() -> dict[str, float]:
    """Current per-minute rates keyed "provider:model:meter", for scripts/eval/vertex_stats.py."""
    out: dict[str, float] = {}
    try:
        conn = get_redis()
        for key in conn.scan_iter(match=f"{_PREFIX}:*:rate"):
            name = key.decode() if isinstance(key, bytes) else key
            # Key shape is "llm:pace:{provider}:{model}:{meter}:rate". Strip the known prefix and
            # suffix and split what is left, rather than counting colons from the front - the prefix
            # itself contains one, which is what made an earlier version report "pace:gemini:m".
            middle = name[len(_PREFIX) + 1 : -len(":rate")]
            bits = middle.split(":")
            if len(bits) != 3:
                continue
            provider, model, meter = bits
            raw = conn.get(name)
            if raw is not None:
                out[f"{provider}:{model}:{meter}"] = round(float(raw) * 60.0, 1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pacing: snapshot failed: %s", exc)
    return out
