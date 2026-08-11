"""Retry wrapper for google-genai generate_content calls.

Vertex gemini-2.5-flash runs on dynamic shared quota: under load it returns 429
RESOURCE_EXHAUSTED / 503 UNAVAILABLE, or drops the connection without a status. The
production call path must ride those out with full-jitter exponential backoff rather
than crash mid-case. Adapted from experiments/a1-segmentation/src/genai_client.py,
which proved this behavior on live Vertex traffic.
"""

import random
import time

import httpx
from google.genai import errors

from mrr_ai.config import GENAI_MAX_RETRIES, GENAI_RETRY_BASE_DELAY, GENAI_RETRY_MAX_DELAY


def _backoff_delay(attempt):
    """Full-jitter exponential backoff delay in seconds for 0-indexed retry `attempt`.

    Random wait in [0, min(max_delay, base * 2**attempt)]: without jitter, parallel
    callers that all hit 429 retry in lockstep and re-collide on the shared quota pool.
    """
    ceiling = min(GENAI_RETRY_MAX_DELAY, GENAI_RETRY_BASE_DELAY * (2**attempt))
    return random.uniform(0.0, ceiling)


def generate_with_retry(client, **kwargs):
    """Call client.models.generate_content, retrying transient failures.

    Retries 5xx, transient 429, and transport disconnects. Re-raises immediately on
    non-429 client errors and on per-day/free-tier quota exhaustion (backoff cannot
    fix those inside a request's lifetime). The client is passed explicitly so route
    modules keep a single patchable client seam.
    """
    last = None
    for attempt in range(GENAI_MAX_RETRIES):
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
        if attempt < GENAI_MAX_RETRIES - 1:
            time.sleep(_backoff_delay(attempt))
    raise last
