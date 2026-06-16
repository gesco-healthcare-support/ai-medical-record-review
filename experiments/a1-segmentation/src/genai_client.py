"""google-genai client + a token/$ accountant for the Phase 0 oracles.

Replaces the spike's old `google.generativeai` usage. The key is read from the Flask
app's .env (not hard-coded). Every call records usage_metadata so we can report the real
Gemini bill per method (cost objective = total tokens, per the plan).
"""

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# The app's .env holds GEMINI_API_KEY (fail-fast validated by the app at runtime).
load_dotenv(r"P:\MRR_AI_Source\mrr-line_source\.env")

MODEL = "gemini-flash-latest"

# Gemini Flash standard-tier rates ($ per token), 2026-06. Centralized so a model/tier
# change is one edit. Source: https://ai.google.dev/gemini-api/docs/pricing
USD_PER_INPUT_TOKEN = 0.10 / 1_000_000
USD_PER_OUTPUT_TOKEN = 0.40 / 1_000_000

_client = None


def client():
    global _client
    if _client is None:
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
