"""Retry wrapper for google-genai generate_content calls.

Vertex gemini runs on dynamic shared quota: under load it returns 429 RESOURCE_EXHAUSTED / 503
UNAVAILABLE, or drops the connection without a status. Ride those out with full-jitter
exponential backoff. Re-raise immediately on non-429 client errors and per-day/free-tier quota
exhaustion (backoff cannot fix either inside a request). A deadline 504 is not backed off either -
backoff does not buy a slow call more time - but it does get ONE retry at a longer deadline, which
is a different request rather than a repeat. Retry knobs come from config.
"""

import logging
import random
import time

import httpx
from google.genai import errors, types

from app.config import get_settings
from app.errors import is_daily_quota, is_deadline_exceeded
from app.services import genai_metrics
from app.services.llm import pacing
from app.worker.cancel import current_job_cancelled
from app.worker.failures import JobCancelled

logger = logging.getLogger(__name__)


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


def _set_deadline(config, timeout_ms: int) -> bool:
    """Give THIS request its own deadline. False when there is no config to carry one.

    Per-request `HttpOptions` beat the client's: google-genai merges them field by field and a
    non-None patch value wins (`_api_client.patch_http_options`), and the timeout becomes Vertex's
    own `X-Server-Timeout`. So this changes the limit the SERVER enforces rather than only the
    client's patience - without that it would be a no-op that reads like a fix.

    Mutates the config in place, the same way `_apply_thinking_default` above does, because the
    object belongs to the single logical call being made.
    """
    if config is None or timeout_ms <= 0:
        return False
    options = types.HttpOptions(timeout=timeout_ms)
    if isinstance(config, dict):
        config["http_options"] = options
    else:
        config.http_options = options
    return True


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


def _note_client_error(exc, model) -> float | None:
    """Record a 429 and decide whether the retry loop may have it. RAISES when it may not.

    Named "note", but that name understates it and the docstring is here to stop it misleading a
    reader: this function makes a CONTROL-FLOW decision. A non-429, and a 429 whose body says the
    daily budget is spent, both propagate out of ``generate_with_retry`` from here and end the loop.
    Only a transient 429 returns.

    The bare ``raise`` re-raises the exception being handled by the caller's ``except`` block, which
    is what keeps the original traceback intact.

    Returns the server-advised retry delay when the response carries one, else None.
    """
    if getattr(exc, "code", None) != 429:
        raise
    genai_metrics.record(model, genai_metrics.OUTCOME_RATE_LIMITED)
    # Feed the controller BEFORE the PerDay carve-out below: a spent daily budget is
    # still evidence that this model is unavailable right now.
    pacing.record_rejection("gemini", model)
    # Through the shared predicate, not an inline copy of it. The comment in the caller promises
    # `worker.failures.classify_failure` mirrors this set "or the two disagree", and that one
    # already asks `is_daily_quota`; a second reading of the same rule is how they would come to
    # disagree - change either side and the other keeps the old answer, which decides whether a job
    # PAUSES and auto-resumes or ends needs_attention.
    if is_daily_quota(exc):
        raise
    # Vertex does not populate RetryInfo in practice (measured 2026-08-05: the 429 body carries only
    # code/message/status), so this returns None and backoff takes over. Kept because it costs
    # nothing and other endpoints do send it.
    return _retry_delay_seconds(exc)


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
    # The deadline SCALES with the request. `est_tokens` is already here for the pacer, so the size
    # signal costs nothing - and it is the whole fix for large records: a fixed limit is safe only
    # while something bounds the request, which is true of segmentation (window_max_pages) and false
    # of summarize, where a row is however many pages the segmenter drew. Below the floor this is
    # exactly genai_http_timeout_ms, so ordinary calls are untouched.
    deadline_ms = settings.effective_genai_timeout_ms(est_tokens)
    _set_deadline(kwargs.get("config"), deadline_ms)
    last = None
    escalated = False  # a deadline 504 gets ONE longer retry, then fails for good
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
                # A deadline 504 is OUR limit (the deadline above, which google-genai forwards to
                # Vertex as its server deadline) coming back as a server status.
                #
                # It gets ONE retry at a multiple of that limit, then fails for good. Two attempts,
                # never the eight that job 1000174 burned over 17.5 minutes - THAT is the
                # measurement
                # the old fail-fast rule rests on, and it is still respected here.
                #
                # What the old rule got wrong is calling a 504 purely deterministic. Job 1000308
                # lost
                # an 18-page row to one, and re-running that row on 2026-09-11 took 51.7s, 50.1s
                # and 77.5s against a 120s limit - it was never too large, it hit a slow moment and
                # was discarded permanently for it. A retry recovers that; the scaled deadline above
                # covers the different case of a row that genuinely is too large.
                # See errors.is_deadline_exceeded; worker.failures.classify_failure mirrors this or
                # the two disagree.
                if is_deadline_exceeded(exc):
                    longer = int(deadline_ms * settings.genai_deadline_retry_multiplier)
                    if (
                        escalated
                        or longer <= deadline_ms
                        or not _set_deadline(kwargs.get("config"), longer)
                    ):
                        raise
                    escalated = True
                    logger.warning(
                        "deadline 504 on %s after %sms; retrying once at %sms",
                        model,
                        deadline_ms,
                        longer,
                    )
                    deadline_ms = longer
                last = exc
            except errors.ClientError as exc:  # retry only transient 429 rate limiting
                # Raises out of the loop for a non-429 and for a spent daily quota; see the helper.
                retry_after = _note_client_error(exc, model)
                last = exc
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
