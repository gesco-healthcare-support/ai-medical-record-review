"""Retry wrapper for google-genai generate_content calls.

Vertex gemini runs on dynamic shared quota: under load it returns 429 RESOURCE_EXHAUSTED / 503
UNAVAILABLE, or drops the connection without a status. Ride those out with full-jitter
exponential backoff. Re-raise immediately on non-429 client errors and per-day/free-tier quota
exhaustion (backoff cannot fix those inside a request). Retry knobs come from config.
"""

import random
import time

import httpx
from google.genai import errors, types

from app.config import get_settings
from app.services import rate_limit


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


def generate_with_retry(client, **kwargs):
    """Call client.models.generate_content, retrying transient failures. Client passed explicitly
    so route/worker modules keep a single patchable client seam."""
    settings = get_settings()
    _apply_thinking_default(kwargs.get("config"))
    last = None
    for attempt in range(settings.genai_max_retries):
        # Bound the GLOBAL request rate across all processes before every attempt (retries also
        # consume quota). Fails open if Redis is down; never blocks past the job timeout.
        rate_limit.acquire()
        retry_after = None
        try:
            return client.models.generate_content(**kwargs)
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry only transient 429 rate limiting
            if getattr(exc, "code", None) != 429:
                raise
            if "PerDay" in str(exc) or "free_tier" in str(exc):
                raise
            last = exc
            retry_after = _retry_delay_seconds(exc)  # honor the server's RetryInfo when present
        except httpx.TransportError as exc:  # disconnect without an HTTP status
            last = exc
        if attempt < settings.genai_max_retries - 1:
            time.sleep(_sleep_for(attempt, retry_after))
    raise last
