"""Retry wrapper for google-genai generate_content calls.

Vertex gemini runs on dynamic shared quota: under load it returns 429 RESOURCE_EXHAUSTED / 503
UNAVAILABLE, or drops the connection without a status. Ride those out with full-jitter
exponential backoff. Re-raise immediately on non-429 client errors, per-day/free-tier quota
exhaustion, and a deadline 504 (backoff cannot fix any of those inside a request). Retry knobs come
from config.
"""

import random
import time

import httpx
from google.genai import errors, types

from app.config import get_settings
from app.errors import is_deadline_exceeded
from app.services import genai_metrics
from app.services.llm import pacing
from app.worker.cancel import current_job_cancelled
from app.worker.failures import JobCancelled


def _backoff_delay(attempt: int) -> float:
    """Full-jitter backoff in [0, min(max_delay, base * 2**attempt)] seconds."""
    settings = get_settings()
    ceiling = min(settings.genai_retry_max_delay, settings.genai_retry_base_delay * (2**attempt))
    return random.uniform(0.0, ceiling)


def _parse_duration(value) -> float | None:
    """Parse a protobuf Duration - '17s'/'1.500s' or {'seconds':int,'nanos':int} - to seconds."""
    if isinstance(value, str) and value.endswith("s"):
        try:
            return float(value[:-1])
        except ValueError:
            return None
    if isinstance(value, dict):
        try:
            return float(value.get("seconds", 0)) + float(value.get("nanos", 0)) / 1e9
        except (TypeError, ValueError):
            return None
    return None


def _retry_delay_seconds(exc) -> float | None:
    """Server-advised delay from a 429's google.rpc.RetryInfo (details[].retryDelay), or None.

    The server knows how long its shared-quota window needs; honoring RetryInfo beats guessing with
    backoff. exc.details is the parsed error JSON; RetryInfo sits in error.details[] (or details[]).
    Any shape we cannot parse yields None so the caller falls back to exponential backoff.
    """
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    error = details.get("error")
    entries = error.get("details") if isinstance(error, dict) else details.get("details")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and "RetryInfo" in str(entry.get("@type", "")):
            return _parse_duration(entry.get("retryDelay"))
    return None


def _sleep_for(attempt: int, retry_after: float | None) -> float:
    """Seconds to wait before the next attempt: the server's retryDelay (+ small jitter, capped)
    when present, else full-jitter exponential backoff."""
    if retry_after is None:
        return _backoff_delay(attempt)
    settings = get_settings()
    jitter = random.uniform(0.0, min(1.0, settings.genai_retry_base_delay))
    return min(retry_after + jitter, settings.genai_retry_max_delay)


def _apply_thinking_default(config) -> None:
    """Disable thinking by default (config-driven) unless the call already set a thinking_config.

    Thinking tokens are overhead for our structured extraction/segmentation calls and silently
    consume max_output_tokens on 2.5-flash. Applied here so every seam call inherits it; a call
    that sets its own thinking_config (e.g. a validated-to-need-it task) always wins. Mutates the
    GenerateContentConfig in place; a mapping config is handled too.
    """
    if config is None:
        return
    budget = types.ThinkingConfig(thinking_budget=get_settings().gemini_thinking_budget)
    if isinstance(config, dict):
        if config.get("thinking_config") is None:
            config["thinking_config"] = budget
    elif getattr(config, "thinking_config", None) is None:
        config.thinking_config = budget


_CANCEL_POLL_SECONDS = 1.0


def _cancellable_sleep(total: float) -> None:
    """Sleep ``total`` seconds in <= 1s slices, abandoning it if this job has been cancelled.

    This is the change that makes the stop button usable. Eight retries with jitter plus rate-limiter
    waits can park a job here for something like 17 minutes, and those wedged jobs are precisely the
    ones a reviewer wants to kill. A bare time.sleep() means the cooperative check in report() cannot
    run until the whole backoff has been served, so the button would appear broken on the only case
    that motivated it.

    The check is a Redis GET against a per-process job id (see worker/cancel.py), so it costs nothing
    and needs no session - which matters because this runs on pool threads several frames below any
    code that knows a job exists. Raising JobCancelled here unwinds through the pool and reaches
    _run's handler exactly like a report()-raised cancel.
    """
    remaining = total
    while remaining > 0:
        if current_job_cancelled():
            raise JobCancelled(0, 0)  # the finalizer takes progress from the job row, not this
        slice_seconds = min(_CANCEL_POLL_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds


def generate_with_retry(client, **kwargs):
    """Call client.models.generate_content, retrying transient failures. Client passed explicitly
    so route/worker modules keep a single patchable client seam.

    Every attempt is counted per model via genai_metrics. A retried 429 used to leave no trace at
    all, which made "did rejections rise?" unanswerable - see that module's docstring.
    """
    settings = get_settings()
    _apply_thinking_default(kwargs.get("config"))
    model = kwargs.get("model")
    est_tokens = kwargs.pop("_est_tokens", 1)
    last = None
    timer = genai_metrics.WaitTimer(model)
    # ONE pacer budget for the whole logical call, not a fresh one per attempt. acquire() defaults to
    # MAX_ACQUIRE_WAIT_S each time it is called, so at genai_max_retries=8 a single call could sit in
    # the pacer for 8 x 300s = 40 minutes - while the comment inside the loop promised it never blocks
    # past the job timeout. Once this budget is gone the remaining attempts still ASK for capacity,
    # they just stop waiting for it, which keeps the module's fail-open contract intact.
    pacer_deadline = time.monotonic() + pacing.MAX_ACQUIRE_WAIT_S
    try:
        for attempt in range(settings.genai_max_retries):
            # Pace the request across all processes before every attempt (a retry consumes quota
            # too). The rate is self-tuning: 429s halve it, successes nudge it back up. Fails open if
            # Redis is down, and the shared deadline above bounds the total wait for this call.
            with timer:
                pacing.acquire(
                    "gemini",
                    model,
                    est_tokens,
                    max_wait_s=max(0.0, pacer_deadline - time.monotonic()),
                )
            retry_after = None
            try:
                response = client.models.generate_content(**kwargs)
            except errors.ServerError as exc:  # 5xx incl. 503 high-demand
                genai_metrics.record(model, genai_metrics.OUTCOME_SERVER_ERROR)
                # A deadline 504 is OUR timeout (genai_http_timeout_ms, which google-genai forwards
                # to Vertex as the server deadline) coming back as a server status. It binds every
                # attempt identically, so retrying only burns the job's budget - measured on job
                # 1000174: eight identical 504s over 17.5 minutes before the reviewer saw anything.
                # Fail fast so the real cause surfaces in one attempt. See errors.is_deadline_exceeded;
                # worker.failures.classify_failure mirrors this or the two disagree.
                if is_deadline_exceeded(exc):
                    raise
                last = exc
            except errors.ClientError as exc:  # retry only transient 429 rate limiting
                if getattr(exc, "code", None) != 429:
                    raise
                genai_metrics.record(model, genai_metrics.OUTCOME_RATE_LIMITED)
                # Feed the controller BEFORE the PerDay carve-out below: a spent daily budget is
                # still evidence that this model is unavailable right now.
                pacing.record_rejection("gemini", model)
                if "PerDay" in str(exc) or "free_tier" in str(exc):
                    raise
                last = exc
                # Vertex does not populate RetryInfo in practice (measured 2026-08-05: the 429 body
                # carries only code/message/status), so this returns None and backoff takes over.
                # Kept because it costs nothing and other endpoints do send it.
                retry_after = _retry_delay_seconds(exc)
            except httpx.TransportError as exc:  # disconnect without an HTTP status
                last = exc
                genai_metrics.record(model, genai_metrics.OUTCOME_TRANSPORT)
            else:
                genai_metrics.record(model, genai_metrics.OUTCOME_ACCEPTED)
                pacing.record_success("gemini", model)
                return response
            if attempt < settings.genai_max_retries - 1:
                _cancellable_sleep(_sleep_for(attempt, retry_after))
        genai_metrics.record(model, genai_metrics.OUTCOME_EXHAUSTED)
        raise last
    finally:
        # One Redis write per logical call rather than one per attempt: this is the latency path
        # being measured, so the accounting must not inflate it.
        timer.flush()
