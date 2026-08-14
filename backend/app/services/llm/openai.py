"""OpenAI provider: chat completions, with its own retry loop.

WHY ITS OWN RETRY rather than sharing ``genai_retry``: that function catches google-genai exception
types by class and is the path every working stage uses today - segmentation, classification, dedup,
extraction, the verify pass and DOI. Generalising it would put the only working pipeline at risk to
save duplication in a provider nobody has run yet. The duplication is deliberate and is the cheaper
mistake; unify once OpenAI is proven.

PHI CONSTRAINTS - these are not style choices:

- **/v1/chat/completions only.** It is zero-data-retention eligible. Assistants, threads, vector
  stores, conversations, fine-tuning and the **Batch API** are NOT, so none may ever carry a record.
  That also means the Batch API's 50% discount is unavailable to this pipeline.
- **``store=False`` on every request.** An org with ZDR approved has it forced server-side anyway,
  but sending it explicitly means a misconfigured org fails safe instead of silently retaining PHI.
- Processing PHI needs ZDR (or Modified Abuse Monitoring / Eyes Off) approved on the organization
  IN ADDITION to a signed BAA. ``config`` enforces an explicit acknowledgement in production.
"""

import base64
import logging
import random
import time
from typing import Any

from app.config import get_settings
from app.services import genai_metrics
from app.services.llm import pacing
from app.services.llm.base import LLMResponse
from app.services.llm.parts import DocumentPart, ImagePart, Part, TextPart
from app.services.llm.tokens import estimate_tokens
from app.worker.cancel import current_job_cancelled
from app.worker.failures import JobCancelled

logger = logging.getLogger(__name__)

_PROVIDER = "openai"
# Poll granularity while sleeping through backoff, so a cancelled job does not sit out the full wait.
_CANCEL_POLL_SECONDS = 1.0


def _client():
    """Lazily built, cached OpenAI client.

    The SDK is imported inside _cached_client rather than at module scope so that selecting Gemini
    never requires the OpenAI package to be installed.
    """
    settings = get_settings()
    return _cached_client(settings.openai_api_key, settings.genai_http_timeout_ms / 1000.0)


_CLIENTS: dict[tuple[str, float], Any] = {}


def _cached_client(api_key: str, timeout_s: float):
    key = (api_key, timeout_s)
    if key not in _CLIENTS:
        from openai import OpenAI

        # max_retries=0: this module owns retrying, because the SDK's own backoff cannot see our
        # cancellation flag and would make the stop button look broken on a wedged call.
        _CLIENTS[key] = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)
    return _CLIENTS[key]


def _to_messages(system: str | None, parts: list[Part]) -> list[dict[str, Any]]:
    """Neutral parts -> chat messages, order preserved.

    Order is load-bearing for the multimodal summary call: images, then OCR text, then the
    instruction last. Everything goes in ONE user message so that order survives - splitting parts
    across messages would let the API's own ordering rules reshape the payload.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    content: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            content.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            encoded = base64.b64encode(part.data).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{part.mime_type};base64,{encoded}"},
                }
            )
        elif isinstance(part, DocumentPart):
            # Chat completions has no inline-PDF part. Nothing routed here sends one today (the PDF
            # paths - segmentation and DOI - stay on Gemini), so refuse loudly rather than silently
            # dropping a document the caller believes was sent.
            raise TypeError(
                "OpenAI chat completions cannot take an inline PDF; keep document parts on Gemini"
            )
    messages.append({"role": "user", "content": content})
    return messages


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema -> OpenAI strict mode.

    Strict mode requires EVERY property to appear in `required` (optionality is expressed as a null
    union) and `additionalProperties: false` on every object. Our shared schema is already written
    that way - see summary_verify._RESPONSE_SCHEMA - so this only fills in what a future schema might
    omit, rather than transforming the dialect.
    """
    if not isinstance(schema, dict):
        return schema
    out = {k: _strict_schema(v) if isinstance(v, (dict, list)) else v for k, v in schema.items()}
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _strict_schema(v) for k, v in out["properties"].items()}
        out.setdefault("additionalProperties", False)
        out["required"] = list(out["properties"].keys())
    if isinstance(out.get("items"), dict):
        out["items"] = _strict_schema(out["items"])
    return out


def _retryable(exc) -> tuple[bool, float | None]:
    """(should_retry, server_advised_delay_seconds).

    Retryable: 429 rate limiting, 5xx, timeouts, connection errors. NOT retryable: 4xx that backoff
    cannot fix, and a 429 whose body says the quota itself is exhausted rather than the rate - the
    same carve-out genai_retry makes for Gemini's PerDay/free_tier, because no amount of waiting
    inside one request refills a spent budget.
    """
    import openai

    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True, None
    status = getattr(exc, "status_code", None)
    if status == 429:
        body = str(getattr(exc, "message", "") or exc)
        if "insufficient_quota" in body or "billing" in body.lower():
            return False, None
        return True, _retry_after(exc)
    if status is not None and 500 <= status < 600:
        return True, _retry_after(exc)
    return False, None


def _retry_after(exc) -> float | None:
    """OpenAI's advised wait, in seconds, when the response carries one.

    Unlike Vertex - whose 429 body was measured to contain nothing but code/message/status - OpenAI
    does send Retry-After, so honouring it beats guessing with backoff.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _observe(model: str, response) -> None:
    """Feed OpenAI's published remaining-capacity headers into the pacer.

    Where a provider states what is left, use it: probing for a number the server already tells you
    is how you cause avoidable 429s. Vertex publishes nothing comparable, which is why its rate has
    to be discovered by an AIMD controller instead.
    """
    headers = getattr(response, "headers", None) or {}

    def _num(name):
        raw = headers.get(name)
        try:
            return float(str(raw).rstrip("s")) if raw is not None else None
        except (TypeError, ValueError):
            return None

    pacing.observe_limits(
        _PROVIDER,
        model,
        _num("x-ratelimit-remaining-requests"),
        _num("x-ratelimit-reset-requests"),
        _num("x-ratelimit-remaining-tokens"),
        _num("x-ratelimit-reset-tokens"),
    )


def _cancellable_sleep(total: float) -> None:
    """Sleep in <=1s slices, abandoning it if this job has been cancelled.

    Mirrors genai_retry: without it, a job wedged in backoff cannot notice the stop button until the
    whole wait is served, which is exactly the case a reviewer wants to kill.
    """
    remaining = total
    while remaining > 0:
        if current_job_cancelled():
            raise JobCancelled(0, 0)
        slice_seconds = min(_CANCEL_POLL_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds


class OpenAIProvider:
    """LLMProvider over the OpenAI chat completions API."""

    name = "openai"

    def _call(self, *, model, system, parts, temperature, max_output_tokens, schema=None):
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _to_messages(system, parts),
            "temperature": temperature,
            "max_completion_tokens": max_output_tokens,
            # PHI: never retain. ZDR forces this server-side; sending it means a misconfigured org
            # fails safe rather than silently storing a medical record.
            "store": False,
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }

        est_tokens = estimate_tokens(parts, system, _PROVIDER)
        client = _client()
        last = None
        # One pacer budget for the whole logical call - see the note in services/genai_retry.py. The
        # two retry loops have to agree: a per-attempt budget lets genai_max_retries multiply the
        # wait, here just as much as on the Gemini path.
        pacer_deadline = time.monotonic() + pacing.MAX_ACQUIRE_WAIT_S
        for attempt in range(settings.genai_max_retries):
            pacing.acquire(
                _PROVIDER,
                model,
                est_tokens,
                max_wait_s=max(0.0, pacer_deadline - time.monotonic()),
            )
            try:
                raw = client.chat.completions.with_raw_response.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                retry, advised = _retryable(exc)
                genai_metrics.record(
                    model,
                    genai_metrics.OUTCOME_RATE_LIMITED
                    if getattr(exc, "status_code", None) == 429
                    else genai_metrics.OUTCOME_SERVER_ERROR,
                )
                if getattr(exc, "status_code", None) == 429:
                    pacing.record_rejection(_PROVIDER, model)
                if not retry:
                    raise
                last = exc
                if attempt < settings.genai_max_retries - 1:
                    _cancellable_sleep(_backoff(attempt, advised))
                continue
            genai_metrics.record(model, genai_metrics.OUTCOME_ACCEPTED)
            pacing.record_success(_PROVIDER, model)
            _observe(model, raw)
            return _to_response(raw.parse())
        genai_metrics.record(model, genai_metrics.OUTCOME_EXHAUSTED)
        raise last

    def generate_text(self, *, model, system, parts, temperature, max_output_tokens):
        return self._call(
            model=model,
            system=system,
            parts=parts,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    def generate_structured(self, *, model, system, parts, schema, temperature, max_output_tokens):
        return self._call(
            model=model,
            system=system,
            parts=parts,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            schema=schema,
        )


def _backoff(attempt: int, advised: float | None) -> float:
    """Server-advised delay when given, else full-jitter exponential backoff - same policy as Gemini."""
    settings = get_settings()
    if advised is not None:
        return min(advised + random.uniform(0.0, 1.0), settings.genai_retry_max_delay)
    ceiling = min(settings.genai_retry_max_delay, settings.genai_retry_base_delay * (2**attempt))
    return random.uniform(0.0, ceiling)


def _to_response(completion) -> LLMResponse:
    """Chat completion -> the neutral response, including the truncation flag."""
    choice = completion.choices[0] if completion.choices else None
    text = ""
    finish = None
    if choice is not None:
        text = (getattr(choice.message, "content", None) or "").strip()
        finish = getattr(choice, "finish_reason", None)
    usage = getattr(completion, "usage", None)
    return LLMResponse(
        text=text,
        # "length" is OpenAI's MAX_TOKENS: the reply is cut off and must never be stored as a
        # finished summary.
        truncated=finish == "length",
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
    )
