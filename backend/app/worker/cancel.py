"""The cancel channel: a Redis flag the API sets and the worker reads.

Why not just `Job.cancel_requested`? Because the signal has to reach `generate_with_retry`, which
runs deep inside a `ThreadPoolExecutor` worker with no session, no job argument, and eight call sites
between it and anything that knows a job id. A `GET` on a short key costs nothing and needs no
plumbing; the column remains the durable record of what was asked.

Why a module-level current job is safe: RQ's `Worker.execute_job` FORKS a work-horse per job (verified
against the installed rq 2.10.0), so one process only ever runs one job and the global cannot bleed
between them. A `contextvar` would be the more modern instinct and would be WRONG here -
`ThreadPoolExecutor` does not copy context into its worker threads, so the segmentation pools would
never see it.

This module is deliberately import-light and knows nothing about the ORM, so the API, the worker and
the retry loop can all use it without an import cycle.
"""

import logging

from redis.exceptions import RedisError

from app.config import get_settings
from app.worker.queues import get_redis

logger = logging.getLogger(__name__)

# The job this process is currently running, or None. Set by _run, cleared when it finishes.
_CURRENT_JOB_ID: int | None = None


def set_current_job(job_id) -> None:
    global _CURRENT_JOB_ID
    _CURRENT_JOB_ID = int(job_id)


def clear_current_job() -> None:
    global _CURRENT_JOB_ID
    _CURRENT_JOB_ID = None


def current_job_id() -> int | None:
    return _CURRENT_JOB_ID


def cancel_key(job_id) -> str:
    return f"mrr:cancel:{int(job_id)}"


def request_cancel(job_id) -> None:
    """Flag ``job_id`` as cancelled, with a TTL so the key can never outlive its usefulness.

    The TTL is generous relative to the grace period: it only has to cover the window between the
    request and the worker noticing, and a key that expires early simply means the cooperative stop
    misses and the reviewer presses Force stop.
    """
    ttl = max(60, get_settings().job_cancel_grace_seconds * 60)
    try:
        # set(..., ex=) rather than setex(): the latter is deprecated in this redis-py.
        get_redis().set(cancel_key(job_id), "1", ex=ttl)
    except RedisError:
        # The DB column is still set by the caller, so a progress tick will not see this but the
        # operator record survives. Do not raise: the API must not 500 because Redis blipped.
        logger.warning("could not publish cancel for job %s (Redis unreachable)", job_id)


def is_cancel_requested(job_id) -> bool:
    """True when a cancel is pending for ``job_id``.

    Fails CLOSED on a Redis error - i.e. reports NOT cancelled. Treating an unreachable Redis as
    "cancelled" would let one blip abort every job running anywhere, which is far worse than a stop
    button that briefly does nothing.
    """
    try:
        return get_redis().get(cancel_key(job_id)) is not None
    except RedisError:
        logger.warning("cancel check skipped for job %s (Redis unreachable)", job_id)
        return False


def clear_cancel(job_id) -> None:
    try:
        get_redis().delete(cancel_key(job_id))
    except RedisError:
        logger.warning("could not clear cancel key for job %s (Redis unreachable)", job_id)


def current_job_cancelled() -> bool:
    """Whether THIS process's job has been cancelled.

    Returns False without touching Redis when no job is current, because `generate_with_retry` calls
    this on every backoff slice - including from the API process, where nothing is running.
    """
    if _CURRENT_JOB_ID is None:
        return False
    return is_cancel_requested(_CURRENT_JOB_ID)
