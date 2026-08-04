"""Per-model Vertex call accounting: attempts accepted, attempts rejected, and limiter wait.

Why this exists: ``genai_retry.generate_with_retry`` catches 429/5xx/transport failures, backs off
and retries, so a call rejected three times and then accepted is indistinguishable from one accepted
immediately. Nothing counted rejections and nothing logged them, so the only evidence a 429 ever
happened was somebody watching container logs live - and those are ephemeral. The summarize workers
restarted at 20:51 UTC on 2026-07-31 and took the only record of that day's job with them.

That makes the question every throughput experiment asks - "did rejections rise when we raised the
concurrency?" - unanswerable. This module answers it.

Counters live in Redis rather than in-process because the api and the two worker tiers are separate
processes sharing one Vertex budget (the same reason ``llm.pacing`` lives there), and because the
Redis container outlives worker restarts.

Counting is per ATTEMPT, not per logical call: a rejection IS an attempt, and it costs a bucket
token, a worker-second and a retry slot whether or not the retry later succeeds. So
``rejection rate = rate_limited / (accepted + rate_limited + server_error + transport)`` reads
directly off these fields. ``exhausted`` counts logical calls that never succeeded at all and is
therefore NOT an attempt - it is recorded alongside the final attempt's own outcome.

Fail-safe by construction: every function swallows its own errors. Accounting that breaks a
summarize job would cost far more than the measurement is worth.
"""

import logging
import time

from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

# One hash per model, so a per-model rate limit can be set from evidence rather than guesswork:
# 2.5-pro and 2.5-flash draw on differently sized shared pools and currently contend for one bucket.
_PREFIX = "vertex:metrics"
# Cumulative and cheap. No TTL - losing an experiment's numbers to an expiry midway would be worse
# than the bytes. An experiment brackets a run with reset() and snapshot().

OUTCOME_ACCEPTED = "accepted"
OUTCOME_RATE_LIMITED = "rate_limited"  # 429 RESOURCE_EXHAUSTED
OUTCOME_SERVER_ERROR = "server_error"  # 5xx, including 503 high-demand
OUTCOME_TRANSPORT = "transport"  # connection dropped without an HTTP status
OUTCOME_EXHAUSTED = "exhausted"  # logical call gave up after genai_max_retries

# The four fields above that represent a real request to Vertex.
ATTEMPT_OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_RATE_LIMITED, OUTCOME_SERVER_ERROR, OUTCOME_TRANSPORT)

# Wall-clock blocked inside llm.pacing.acquire(), milliseconds. This separates "Vertex is rejecting
# us" from "our own limiter is holding us back" - opposite problems with opposite fixes, and telling
# them apart is the entire point of the pipeline_workers ramp.
FIELD_WAIT_MS = "limiter_wait_ms"


def _key(model) -> str:
    """Redis hash key for one model. An absent model name buckets under '-' rather than raising:
    an uncounted call is a smaller problem than a crashed one."""
    return f"{_PREFIX}:{str(model) if model else '-'}"


def record(model, outcome: str) -> None:
    """Count one attempt outcome (or one ``exhausted`` logical call) against ``model``."""
    try:
        get_redis().hincrby(_key(model), outcome, 1)
    except Exception as exc:  # noqa: BLE001 - never let accounting break a pipeline job
        logger.debug("genai metrics: record failed: %s", exc)


def record_wait(model, waited_seconds: float) -> None:
    """Add time spent blocked in the rate limiter before a call to ``model``."""
    if waited_seconds <= 0:
        return
    try:
        get_redis().hincrby(_key(model), FIELD_WAIT_MS, int(waited_seconds * 1000))
    except Exception as exc:  # noqa: BLE001
        logger.debug("genai metrics: record_wait failed: %s", exc)


def snapshot() -> dict[str, dict[str, int]]:
    """Every model's counters as {model: {field: count}}; empty when Redis is unreachable.

    Read by ``scripts/eval/vertex_stats.py``. Deliberately not exposed over the API: this is an
    operator measurement, not product surface.
    """
    out: dict[str, dict[str, int]] = {}
    try:
        conn = get_redis()
        for key in conn.scan_iter(match=f"{_PREFIX}:*"):
            name = key.decode() if isinstance(key, bytes) else key
            model = name.split(":", 2)[-1]
            raw = conn.hgetall(key) or {}
            out[model] = {
                (k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("genai metrics: snapshot failed: %s", exc)
    return out


def reset() -> None:
    """Clear all counters, to bracket one experiment run. Never called by the pipeline."""
    try:
        conn = get_redis()
        keys = list(conn.scan_iter(match=f"{_PREFIX}:*"))
        if keys:
            conn.delete(*keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("genai metrics: reset failed: %s", exc)


class WaitTimer:
    """Accumulates limiter wait across one logical call's attempts, flushed once at the end.

    Separate from ``record`` because a logical call can block in ``acquire()`` on every retry, and
    writing to Redis inside that loop would add a round trip to the path whose latency is being
    measured.
    """

    def __init__(self, model):
        self.model = model
        self.waited = 0.0
        self._started = 0.0

    def __enter__(self):
        self._started = time.monotonic()
        return self

    def __exit__(self, *_exc):
        if self._started:
            self.waited += time.monotonic() - self._started
            self._started = 0.0
        return False

    def flush(self) -> None:
        record_wait(self.model, self.waited)
