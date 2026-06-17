"""google-genai client + a token/$ accountant for the Phase 0 oracles.

Replaces the spike's old `google.generativeai` usage. The key is read from the Flask
app's .env (not hard-coded). Every call records usage_metadata so we can report the real
Gemini bill per method (cost objective = total tokens, per the plan).
"""

import asyncio
import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# The app's .env holds GEMINI_API_KEY (fail-fast validated by the app at runtime).
load_dotenv(r"P:\MRR_AI_Source\mrr-line_source\.env")

# Route to Vertex AI (BAA-covered, aiplatform.googleapis.com) when GOOGLE_GENAI_USE_VERTEXAI is
# truthy; otherwise the AI Studio Developer API (generativelanguage.googleapis.com, which is NOT
# under a Google Cloud BAA). PHI runs MUST use Vertex. Vertex model ids differ from AI Studio's
# "-latest" aliases, so the default model is chosen per endpoint (override with GENAI_MODEL).
USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("1", "true", "yes")
MODEL = os.environ.get("GENAI_MODEL") or ("gemini-2.5-flash" if USE_VERTEX else "gemini-flash-latest")

# Max Gemini requests in flight at once for the async bake-off path. Async cuts wall-clock for
# the embarrassingly-parallel solutions (1/2/3); it does NOT change token cost, and sol4's
# galloping/binary search is inherently sequential so it stays synchronous.
DEFAULT_CONCURRENCY = 8

# Per-token rates ($/token). Defaults are Gemini Flash; the Vertex gemini-2.5-flash tier differs,
# so allow an env override (GENAI_USD_PER_INPUT_TOKEN / _OUTPUT_TOKEN) when reporting Vertex cost.
# Sources: https://ai.google.dev/gemini-api/docs/pricing , https://cloud.google.com/vertex-ai/generative-ai/pricing
USD_PER_INPUT_TOKEN = float(os.environ.get("GENAI_USD_PER_INPUT_TOKEN", 0.10 / 1_000_000))
USD_PER_OUTPUT_TOKEN = float(os.environ.get("GENAI_USD_PER_OUTPUT_TOKEN", 0.40 / 1_000_000))

_client = None


def client():
    """Build the google-genai client once, routed per GOOGLE_GENAI_USE_VERTEXAI.

    Vertex (BAA): set GOOGLE_GENAI_USE_VERTEXAI=true. Auth is either
      - service account / ADC: set GOOGLE_CLOUD_PROJECT [+ GOOGLE_CLOUD_LOCATION] and point
        GOOGLE_APPLICATION_CREDENTIALS at the SA JSON; or
      - a GCP API key: leave GOOGLE_CLOUD_PROJECT unset and it uses GEMINI_API_KEY against the
        Vertex endpoint (the key carries its own project).
    AI Studio (non-PHI dev only): leave the flag unset; uses GEMINI_API_KEY on the Developer API.
    """
    global _client
    if _client is None:
        if USE_VERTEX:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            if project:  # service-account / ADC path
                location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
                _client = genai.Client(vertexai=True, project=project, location=location)
            else:  # GCP API-key path (key carries its project)
                _client = genai.Client(vertexai=True, api_key=os.environ["GEMINI_API_KEY"])
        else:
            _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


class Cost:
    """Accumulates token usage + dollars across calls (the cost objective)."""

    def __init__(self):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage):
        self.calls += 1
        if usage is not None:
            self.input_tokens += usage.prompt_token_count or 0
            self.output_tokens += usage.candidates_token_count or 0

    @property
    def usd(self):
        return self.input_tokens * USD_PER_INPUT_TOKEN + self.output_tokens * USD_PER_OUTPUT_TOKEN

    @property
    def tokens_per_call(self):
        return (self.input_tokens + self.output_tokens) / self.calls if self.calls else 0.0

    def summary(self):
        return (
            f"calls={self.calls}  in_tok={self.input_tokens}  out_tok={self.output_tokens}  "
            f"tok/call={self.tokens_per_call:.0f}  ${self.usd:.4f}"
        )


def _generate_with_retry(**kwargs):
    """Call generate_content, retrying transient overload/rate-limit errors with backoff.

    Gemini routinely returns 503 UNAVAILABLE ("high demand") and 429 under load; a long
    sequential run must ride those out rather than crash. Up to 6 attempts, 2s -> 32s.
    """
    delay = 2.0
    last = None
    for _ in range(6):
        try:
            return client().models.generate_content(**kwargs)
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry only transient 429 rate limiting
            if getattr(exc, "code", None) != 429:
                raise
            # A per-day / free-tier quota won't recover within our backoff window: fail fast.
            if "PerDay" in str(exc) or "free_tier" in str(exc):
                raise
            last = exc
        time.sleep(delay)
        delay = min(delay * 2, 32.0)
    raise last


def classify_enum(contents, enum_values, system_instruction, cost):
    """One constrained-enum call; returns the chosen value (or None) and records cost.

    Uses response_mime_type text/x.enum so Gemini can only emit one of enum_values
    (no invalid output). temperature 0 for the most deterministic answer we can get.
    """
    response = _generate_with_retry(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="text/x.enum",
            response_schema={"type": "STRING", "enum": list(enum_values)},
            system_instruction=system_instruction,
        ),
    )
    cost.add(response.usage_metadata)
    value = (response.text or "").strip()
    return value if value in enum_values else None


def generate_json(contents, system_instruction, cost):
    """One JSON-mode call (for the window oracle that returns many boundaries); cost-tracked.

    Returns the parsed object, or None on a parse failure (degrade, don't crash).
    """
    response = _generate_with_retry(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            system_instruction=system_instruction,
        ),
    )
    cost.add(response.usage_metadata)
    text = (response.text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def upload_file(path):
    """Upload a (sub-)PDF and block until it is ACTIVE (for the window oracle)."""
    f = client().files.upload(file=path)
    while f.state.name == "PROCESSING":
        time.sleep(2)
        f = client().files.get(name=f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"file upload failed: {f.state.name}")
    return f


# ----- async path (wall-clock speedup for the parallel solutions) ---------------------------


async def _generate_with_retry_async(**kwargs):
    """Async twin of _generate_with_retry using client.aio (the documented async API)."""
    delay = 2.0
    last = None
    for _ in range(6):
        try:
            return await client().aio.models.generate_content(**kwargs)
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry only transient 429 rate limiting
            if getattr(exc, "code", None) != 429:
                raise
            if "PerDay" in str(exc) or "free_tier" in str(exc):  # quota won't recover: fail fast
                raise
            last = exc
        await asyncio.sleep(delay)
        delay = min(delay * 2, 32.0)
    raise last


async def classify_enum_async(contents, enum_values, system_instruction, cost):
    """Async constrained-enum call; identical semantics to classify_enum, cost-tracked."""
    response = await _generate_with_retry_async(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="text/x.enum",
            response_schema={"type": "STRING", "enum": list(enum_values)},
            system_instruction=system_instruction,
        ),
    )
    cost.add(response.usage_metadata)
    value = (response.text or "").strip()
    return value if value in enum_values else None


async def gather_bounded(factories, limit=DEFAULT_CONCURRENCY):
    """Run zero-arg coroutine factories with at most `limit` in flight at once, preserving order.

    factories: a list of callables each returning a fresh coroutine (e.g. lambda: call(...)).
    A semaphore caps concurrency so a 700-page record does not open 700 sockets at once.
    """
    sem = asyncio.Semaphore(limit)

    async def _run(make):
        async with sem:
            return await make()

    return await asyncio.gather(*(_run(f) for f in factories))
