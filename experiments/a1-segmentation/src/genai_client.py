"""google-genai client + a token/$ accountant for the Phase 0 oracles.

Replaces the spike's old `google.generativeai` usage. The key is read from the Flask
app's .env (not hard-coded). Every call records usage_metadata so we can report the real
Gemini bill per method (cost objective = total tokens, per the plan).
"""

import asyncio
import concurrent.futures
import contextlib
import json
import os
import random
import threading
import time
from contextvars import ContextVar

import httpx
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
# galloping/binary search is inherently sequential so it stays synchronous. Lowered to 4 and made
# env-tunable: gemini-2.5-flash runs on dynamic shared quota, so fewer in-flight requests means
# fewer 429s. Raise GENAI_CONCURRENCY if the shared pool has headroom; lower it if 429s persist.
DEFAULT_CONCURRENCY = int(os.environ.get("GENAI_CONCURRENCY", 4))

# Retry/backoff tuning for transient 429 (dynamic shared quota) and 5xx overload. Env-tunable so a
# heavy run can ride out longer contention without a code change. Default raised to 10: the
# image-heavy adjacent method (sol2) is the most 429-prone, so it needs more rope under DSQ.
MAX_RETRIES = int(os.environ.get("GENAI_MAX_RETRIES", 10))
RETRY_BASE_DELAY = float(os.environ.get("GENAI_RETRY_BASE_DELAY", 2.0))
RETRY_MAX_DELAY = float(os.environ.get("GENAI_RETRY_MAX_DELAY", 60.0))

# PROACTIVE throttle (prevent 429 rather than only recover from it). Every request waits until a
# min interval has elapsed since the previous send, so a long sequential run never machine-guns the
# shared-quota pool. The interval is ADAPTIVE (AIMD): a 429 multiplies it up (back off the whole
# run, not just the one call); sustained success decays it back toward the floor. Jittered so the
# spacing is not perfectly periodic. All env-tunable.
MIN_INTERVAL = float(os.environ.get("GENAI_MIN_INTERVAL", 3.0))          # seconds between sends (floor)
MIN_INTERVAL_MAX = float(os.environ.get("GENAI_MIN_INTERVAL_MAX", 30.0))  # adaptive ceiling
_INTERVAL_BACKOFF = 1.5   # multiply interval on a 429 (additive-increase surrogate: fast widen)
_INTERVAL_DECAY = 0.97    # multiply interval on success (slow return to the floor)
_throttle_lock = threading.Lock()
_next_send_at = 0.0            # monotonic clock: earliest time the next request may be sent
_current_interval = MIN_INTERVAL

# Per-call HTTP timeout (milliseconds). A hung socket in a ~230-call sequential run would otherwise
# stall the whole experiment indefinitely (observed once: a call hung at 100/104). google-genai
# raises httpx.TimeoutException on expiry, which is an httpx.TransportError subclass -> already
# caught and RETRIED by the backoff loop below.
TIMEOUT_MS = int(os.environ.get("GENAI_TIMEOUT_MS", 120_000))

# Hard wall-clock deadline per attempt (seconds). The SDK's own HttpOptions.timeout has been
# observed NOT to fire on some stuck Vertex sockets (a call once hung ~58 min). So enforce our
# own deadline by running each generate_content on a worker thread and abandoning it if it blows
# the deadline - then retrying on a fresh connection. Kept above the normal call latency
# (~5-25s) with headroom for the SDK-internal retries.
CALL_DEADLINE = float(os.environ.get("GENAI_CALL_DEADLINE", 90.0))


def _reserve_send_slot():
    """Reserve the next send slot; return seconds to sleep before sending (0 if none). The time
    bookkeeping is done under the lock WITHOUT sleeping, so concurrent callers each get a distinct,
    non-overlapping slot and the actual wait happens outside the lock."""
    global _next_send_at
    with _throttle_lock:
        now = time.monotonic()
        send_at = max(now, _next_send_at)
        interval = _current_interval * random.uniform(0.85, 1.15)  # +-15% jitter
        _next_send_at = send_at + interval
        return max(0.0, send_at - now)


def _widen_interval():
    """A 429 was seen: multiplicatively widen the throttle so the rest of the run slows down."""
    global _current_interval
    with _throttle_lock:
        _current_interval = min(MIN_INTERVAL_MAX, _current_interval * _INTERVAL_BACKOFF)


def _relax_interval():
    """A call succeeded: decay the throttle slowly back toward the floor."""
    global _current_interval
    with _throttle_lock:
        _current_interval = max(MIN_INTERVAL, _current_interval * _INTERVAL_DECAY)

# Per-token rates ($/token). Defaults are Gemini Flash; the Vertex gemini-2.5-flash tier differs,
# so allow an env override (GENAI_USD_PER_INPUT_TOKEN / _OUTPUT_TOKEN) when reporting Vertex cost.
# Sources: https://ai.google.dev/gemini-api/docs/pricing , https://cloud.google.com/vertex-ai/generative-ai/pricing
USD_PER_INPUT_TOKEN = float(os.environ.get("GENAI_USD_PER_INPUT_TOKEN", 0.10 / 1_000_000))
USD_PER_OUTPUT_TOKEN = float(os.environ.get("GENAI_USD_PER_OUTPUT_TOKEN", 0.40 / 1_000_000))

_client = None

# Async path only: the google-genai client in use for the CURRENT event loop. The module-global
# `_client` caches an httpx AsyncClient that binds to the first loop touching `.aio`; reusing it
# across asyncio.run() calls raises "Event loop is closed" (python-genai #1518 -- httpx connection
# pools cannot span loops). The async bake-off path therefore builds a fresh client per loop inside
# async_client_scope() and publishes it here, leaving the synchronous global untouched.
_scoped_async_client = ContextVar("genai_scoped_async_client", default=None)


def _build_client():
    """Construct a google-genai client routed per GOOGLE_GENAI_USE_VERTEXAI (no caching).

    Vertex (BAA): set GOOGLE_GENAI_USE_VERTEXAI=true. Auth is either
      - service account / ADC: set GOOGLE_CLOUD_PROJECT [+ GOOGLE_CLOUD_LOCATION] and point
        GOOGLE_APPLICATION_CREDENTIALS at the SA JSON; or
      - a GCP API key: leave GOOGLE_CLOUD_PROJECT unset and it uses GEMINI_API_KEY against the
        Vertex endpoint (the key carries its own project).
    AI Studio (non-PHI dev only): leave the flag unset; uses GEMINI_API_KEY on the Developer API.
    """
    http_options = types.HttpOptions(timeout=TIMEOUT_MS)  # per-call ceiling; expiry is retryable
    if USE_VERTEX:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:  # service-account / ADC path
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
            return genai.Client(vertexai=True, project=project, location=location,
                                http_options=http_options)
        # GCP API-key path
        return genai.Client(vertexai=True, api_key=os.environ["GEMINI_API_KEY"],
                            http_options=http_options)
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=http_options)


def client():
    """The cached client for the SYNCHRONOUS path (sol1/sol4/4b + Phase 0).

    The async path must NOT reuse this instance across event loops; it uses a per-loop client
    built by async_client_scope() instead (see _scoped_async_client).
    """
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _reset_client():
    """Drop the cached sync client so the NEXT call builds a fresh connection pool.

    Root cause of the stuck-call hangs: Vertex under load can leave a pooled socket wedged, and
    because client() caches one client for the whole run, every retry reused the SAME dead pool
    and re-hung (the hard-deadline watchdog abandoned each attempt but kept retrying into the
    same stuck connection). Rebuilding after an abandonment / transport disconnect gives the
    retry a clean pool - the missing piece that makes stuck calls actually recoverable.
    """
    global _client
    _client = None


def _active_async_client():
    """Client the async generate path must use: the per-loop scoped client when a scope is active,
    else the cached sync client (covers a stray async call made outside any scope)."""
    scoped = _scoped_async_client.get()
    return scoped if scoped is not None else client()


@contextlib.asynccontextmanager
async def async_client_scope():
    """Bind a FRESH google-genai client to the running event loop for the async bake-off path.

    Each asyncio.run() spins up its own loop, and an httpx AsyncClient cannot be reused across loops
    (python-genai #1518). Building the client here -- inside the loop -- and closing it on exit keeps
    each loop's connection pool self-contained, so sol2/sol3 async survive a case-after-case run
    instead of dying with "Event loop is closed" on the second case.
    """
    c = _build_client()
    token = _scoped_async_client.set(c)
    try:
        yield c
    finally:
        _scoped_async_client.reset(token)
        try:
            await c.aio.aclose()  # release this loop's httpx pool deterministically
        except Exception as exc:  # noqa: BLE001 -- cleanup must not mask the run's real outcome
            print(f"async client aclose failed (non-fatal): {exc}")


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


def _backoff_delay(attempt):
    """Full-jitter exponential backoff delay (seconds) for retry `attempt` (0-indexed).

    Returns a random wait in [0, min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * 2**attempt)]. The jitter
    is the point under dynamic shared quota: without it, many parallel calls that all hit 429 retry
    in lockstep and re-collide on the shared pool. Full jitter spreads them out (AWS "Exponential
    Backoff And Jitter").
    """
    ceiling = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** attempt))
    return random.uniform(0.0, ceiling)


def _generate_once(**kwargs):
    """One generate_content under a HARD wall-clock deadline enforced OUTSIDE the SDK.

    Runs the call on a single-use worker thread; if it blows CALL_DEADLINE we raise
    TimeoutError and move on, leaving the stuck thread to die on its own (it cannot be force-
    killed in Python, but shutdown(wait=False) frees us to retry on a fresh connection). A
    bounded run tolerates a handful of such zombie threads.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: client().models.generate_content(**kwargs))
    try:
        return future.result(timeout=CALL_DEADLINE)
    finally:
        executor.shutdown(wait=False)


def _generate_with_retry(**kwargs):
    """Call generate_content, retrying transient overload/rate-limit errors with jittered backoff.

    Gemini routinely returns 503 UNAVAILABLE ("high demand") and 429 RESOURCE_EXHAUSTED (dynamic
    shared quota) under load; a long run must ride those out rather than crash. Up to MAX_RETRIES
    attempts with full-jitter backoff. A per-day / free-tier quota won't recover in our window, so
    those fail fast.
    """
    last = None
    for attempt in range(MAX_RETRIES):
        time.sleep(_reserve_send_slot())  # proactive throttle: never machine-gun the shared pool
        try:
            response = _generate_once(**kwargs)
            _relax_interval()
            return response
        except concurrent.futures.TimeoutError as exc:  # our hard deadline: stuck socket
            _reset_client()  # the pooled connection is wedged - retry on a FRESH pool, not this one
            last = exc
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry transient 429 (rate limit) + 499 (cancelled)
            code = getattr(exc, "code", None)
            if code not in (429, 499):
                raise
            # A per-day / free-tier quota won't recover within our backoff window: fail fast.
            if code == 429 and ("PerDay" in str(exc) or "free_tier" in str(exc)):
                raise
            if code == 429:
                _widen_interval()  # DSQ contention: slow the WHOLE run down, not just this retry
            # 499 CANCELLED: a slow (usually big-payload) request the server/timeout cut off under
            # load. It is transient, so ride it out rather than let it kill a whole solution.
            last = exc
        except httpx.TransportError as exc:  # transient transport disconnect OR per-call timeout
            # httpx.TimeoutException (our per-call ceiling) + RemoteProtocolError "server
            # disconnected" / ReadError (TCP reset). Vertex under dynamic shared quota sometimes
            # drops the connection instead of returning 429; without this a single disconnect in a
            # long sequential loop aborts the whole run.
            _reset_client()  # a dropped/disconnected socket means the pool is suspect - rebuild
            last = exc
        if attempt < MAX_RETRIES - 1:
            time.sleep(_backoff_delay(attempt))
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


def generate_json(contents, system_instruction, cost, response_schema=None):
    """One JSON-mode call (for the window oracle that returns many boundaries); cost-tracked.

    `response_schema` (optional) enforces the output shape server-side - the reliable path per
    the structured-output docs; prose-only JSON instructions stay as the fallback when None.
    Returns the parsed object, or None on a parse failure (degrade, don't crash).
    """
    response = _generate_with_retry(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=system_instruction,
        ),
    )
    cost.add(response.usage_metadata)
    text = (response.text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


# ----- async path (wall-clock speedup for the parallel solutions) ---------------------------


async def _generate_with_retry_async(**kwargs):
    """Async twin of _generate_with_retry using client.aio (the documented async API)."""
    last = None
    for attempt in range(MAX_RETRIES):
        await asyncio.sleep(_reserve_send_slot())  # proactive throttle (shared with the sync path)
        try:
            response = await _active_async_client().aio.models.generate_content(**kwargs)
            _relax_interval()
            return response
        except errors.ServerError as exc:  # 5xx incl. 503 high-demand
            last = exc
        except errors.ClientError as exc:  # retry transient 429 (rate limit) + 499 (cancelled)
            code = getattr(exc, "code", None)
            if code not in (429, 499):
                raise
            if code == 429 and ("PerDay" in str(exc) or "free_tier" in str(exc)):
                raise  # quota won't recover: fail fast
            if code == 429:
                _widen_interval()
            last = exc
        except httpx.TransportError as exc:  # transient transport disconnect / timeout (see sync twin)
            last = exc
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(_backoff_delay(attempt))
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
