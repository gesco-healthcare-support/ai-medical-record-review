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
        except httpx.TransportError as exc:  # disconnect without an HTTP status
            last = exc
        if attempt < settings.genai_max_retries - 1:
            time.sleep(_backoff_delay(attempt))
    raise last
