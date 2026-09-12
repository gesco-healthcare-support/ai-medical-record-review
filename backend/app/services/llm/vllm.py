"""Self-hosted vLLM provider: chat completions against our own box.

Built from ``llm/openai.py`` because vLLM serves the OpenAI wire dialect, and it reuses that
module's two pure translators (``_to_messages``, ``_strict_schema``) for the same reason - they
encode the OpenAI request shape, which is precisely what this server accepts. The retry loop is
NOT shared, following the reasoning already recorded in that file: the loops differ in policy, and
a shared loop would have to branch on provider anyway.

FOUR DELIBERATE DIVERGENCES from the OpenAI provider. Each is a thing that module sends and this
one must not, and none of them is a style choice:

1. **No ``store``.** OpenAI's is a PHI control - it makes a misconfigured org fail safe. vLLM has no
   such field, and it does NOT reject the unknown key: ``OpenAIBaseModel`` sets
   ``ConfigDict(extra="allow")`` (changed from ``forbid`` in vLLM PR #10463), so an unrecognised
   field is accepted and silently ignored. Sending it would therefore look like a control and be
   nothing at all, which is worse than not sending it. The control that actually applies here is
   the approved-destination check in ``config._validate_vllm_backend``: this endpoint is our own
   box, reached through an SSH tunnel, and where it points is what decides where PHI goes.
2. **No ``strict``.** ``JsonSchemaResponseFormat`` does declare the field, but
   ``structured_outputs_from_response_format()`` passes only the schema through, so strictness set
   that way is dropped. It has to travel INSIDE the schema instead, which ``_strict_schema`` already
   does - every property required, ``additionalProperties`` false.
3. **Thinking is sent explicitly OFF, and no budget is ever sent.** Measured 2026-09-11 on a
   1,314-page record: with thinking on, 25 of 476 rows (5.3%) returned an EMPTY summary -
   ``finish_reason=length`` at exactly the 8,192 cap, 23,878 to 30,891 characters of reasoning and
   no summary at all. The model spends the output allowance on reasoning. A *bounded* budget is not
   the fix either; see the note on ``thinking_token_budget`` below.
4. **A client-side timeout is NOT retried.** See ``_retryable``.

NO RATE-LIMIT FEEDBACK EXISTS ON THIS PATH, which changes what pacing means. vLLM emits no
``x-ratelimit-*`` headers, so ``pacing.observe_limits`` never fires; and it QUEUES rather than
returning 429, so ``pacing.record_rejection`` - gated on that status - never fires either. The AIMD
controller therefore has no signal, seeds at the ceiling and can never lower itself. The configured
ceiling is the sole control, which is why ``vllm_max_rpm`` and ``vllm_max_tpm`` ship at 0 (off)
until a sweep that measures a constrained configuration can set them honestly.
"""

import logging
import random
import time
from typing import Any

from app.config import get_settings
from app.services import genai_metrics
from app.services.llm import pacing
from app.services.llm.base import _DEFAULT_STAGE, DelegatingProvider
from app.services.llm.openai import _strict_schema, _to_messages, _to_response
from app.services.llm.tokens import estimate_tokens
from app.worker.cancel import current_job_cancelled
from app.worker.failures import JobCancelled

logger = logging.getLogger(__name__)

_PROVIDER = "vllm"
_CANCEL_POLL_SECONDS = 1.0
# Connect is separate from read and stays short. A pod that is gone should be reported in seconds,
# while a segmentation window legitimately takes minutes - collapsing both into one number is what
# forces a choice between "slow to notice a dead pod" and "kills real work".
_CONNECT_TIMEOUT_S = 10.0
# The OpenAI SDK refuses to build a client with an empty api_key. vLLM does not require one at all
# (and `--api-key`, when set, guards only the /v1, /v2 and /inference prefixes), so an unset key is
# normal rather than an error. Send a placeholder rather than making operators invent a secret.
_PLACEHOLDER_API_KEY = "not-required-by-vllm"

# Thinking OFF, expressed the way vLLM expects: a chat-template keyword argument, not a sampling
# parameter. Never send `thinking_token_budget` alongside it - a bounded budget does not solve the
# empty-summary failure, it relocates it, because the reasoning is then written INTO the reply.
_THINKING_OFF: dict[str, Any] = {"chat_template_kwargs": {"enable_thinking": False}}


def _client():
    """Lazily built, cached vLLM client.

    The SDK import sits inside ``_cached_client`` so that selecting Gemini never requires the openai
    package to be installed, matching the OpenAI provider.
    """
    settings = get_settings()
    return _cached_client(
        settings.vllm_api_key or _PLACEHOLDER_API_KEY,
        settings.vllm_base_url,
        float(settings.vllm_read_timeout_s),
        _CONNECT_TIMEOUT_S,
    )


# Keyed on SCALAR floats rather than on a prepared timeout object: `httpx.Timeout` is unhashable, so
# a cache keyed on one raises TypeError the first time a second client is requested.
_CLIENTS: dict[tuple[str, str, float, float], Any] = {}


def _cached_client(api_key: str, base_url: str, read_s: float, connect_s: float):
    key = (api_key, base_url, read_s, connect_s)
    if key not in _CLIENTS:
        import httpx
        from openai import OpenAI

        # max_retries=0 for the same reason as the OpenAI provider: this module owns retrying,
        # because the SDK's own backoff cannot see our cancellation flag and would make the stop
        # button look broken on a wedged call.
        _CLIENTS[key] = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(read_s, connect=connect_s),
            max_retries=0,
        )
    return _CLIENTS[key]


def _retryable(exc) -> bool:
    """Whether this failure is worth another attempt.

    A PLAIN BOOL, unlike the OpenAI provider's ``(bool, delay)`` pair, and that is a correction. This
    returned the pair too, for symmetry - but every one of its branches set the delay to None,
    because vLLM sends no ``Retry-After`` (see ``_backoff``: the condition that header describes is
    not how vLLM sheds load). So the caller's ``advised if advised is not None else _backoff(...)``
    could never take its first arm. Symmetry with a sibling is not worth an unreachable branch.

    THE DEADLINE CARVE-OUT is the real difference from the OpenAI provider, and it is about money
    rather than correctness. That module retries ``APITimeoutError``, which is right against an API
    that has probably dropped the request. It is wrong against a GPU we are renting: vLLM keeps
    generating after our client gives up, so a retry starts a SECOND generation while the first still
    occupies the card. At ``genai_max_retries`` of 8 that is eight concurrent generations of one
    prompt, roughly sixteen minutes of rented GPU, and no answer at the end of it. A read timeout
    means the deadline was wrong, and no amount of retrying fixes a deadline.

    A CONNECTION error is different and stays retryable: nothing was ever generating, and a tunnel
    that blinked during a pod restart is exactly what a retry is for.
    """
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return False
    if isinstance(exc, openai.APIConnectionError):
        return True
    status = getattr(exc, "status_code", None)
    # vLLM queues rather than rate-limiting, so a 429 should not arrive at all. Handled anyway in
    # case something sits in front of the endpoint, since treating it as fatal would be worse.
    if status == 429:
        return True
    return status is not None and 500 <= status < 600


def _cancellable_sleep(total: float) -> None:
    """Sleep in <=1s slices, abandoning it if this job has been cancelled.

    Mirrors the OpenAI provider and ``genai_retry``: without it, a job wedged in backoff cannot
    notice the stop button until the whole wait is served.
    """
    remaining = total
    while remaining > 0:
        if current_job_cancelled():
            raise JobCancelled(0, 0)
        slice_seconds = min(_CANCEL_POLL_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds


def _backoff(attempt: int) -> float:
    """Full-jitter exponential backoff.

    No server-advised delay branch, unlike the OpenAI provider: vLLM sends no ``Retry-After``,
    because the condition that header describes - a rate limit - is not how it sheds load.
    """
    settings = get_settings()
    ceiling = min(settings.genai_retry_max_delay, settings.genai_retry_base_delay * (2**attempt))
    return random.uniform(0.0, ceiling)


def _request_kwargs(
    *,
    model,
    system,
    parts,
    temperature,
    max_output_tokens=None,
    schema=None,
    choices=None,
) -> dict[str, Any]:
    """The request body for one call.

    Lifted out of ``_call``, which SonarCloud measured at cognitive complexity 16 against a ceiling
    of 15 (python:S3776). The branches below were most of that, and they are a different concern
    from the retry loop that stays behind - what to ASK for, rather than how many times to ask.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _to_messages(system, parts),
        "temperature": temperature,
        # Thinking off on every call. See the module docstring: with it on, 5.3% of rows on a long
        # record returned nothing at all.
        "extra_body": dict(_THINKING_OFF),
    }
    # Optional, unlike the OpenAI provider. Four of the seven services that will route here set no
    # cap today, and a seam that demands one forces callers to invent a number - a bug this repo has
    # already shipped twice.
    if max_output_tokens is not None:
        kwargs["max_completion_tokens"] = max_output_tokens
    if choices is not None:
        # vLLM's native constrained choice, and a direct replacement for Gemini's enum mode - so the
        # two services that need it are a parameter swap rather than a rewrite. It rides in
        # extra_body beside the thinking flag; `guided_choice` was the old spelling and was REMOVED
        # in v0.12.0, well below the version we serve.
        kwargs["extra_body"]["structured_outputs"] = {"choice": list(choices)}
    elif schema is not None:
        # No "strict" key: vLLM drops it (see the module docstring). _strict_schema is what actually
        # enforces the shape, by writing the constraint into the schema itself.
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "structured_output", "schema": _strict_schema(schema)},
        }
    return kwargs


class VLLMProvider(DelegatingProvider):
    """LLMProvider over a self-hosted vLLM chat-completions endpoint.

    The three public methods come from DelegatingProvider; only ``_call`` differs between backends.
    """

    name = "vllm"

    def _call(
        self,
        *,
        model,
        system,
        parts,
        temperature,
        stage=_DEFAULT_STAGE,
        max_output_tokens=None,
        schema=None,
        choices=None,
    ):
        # `stage` is accepted and unused, and unlike the OpenAI provider that is not a deferral. On
        # Gemini the stage selects a thinking budget; here there is no budget to select, because
        # thinking is off unconditionally and for a measured reason. A stage-dependent budget on this
        # backend would reintroduce exactly the failure the module docstring records.
        del stage
        settings = get_settings()
        kwargs = _request_kwargs(
            model=model,
            system=system,
            parts=parts,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            schema=schema,
            choices=choices,
        )
        est_tokens = estimate_tokens(parts, system, _PROVIDER)
        client = _client()
        last = None
        # One pacer budget for the whole logical call, matching the other two providers: a
        # per-attempt budget would let genai_max_retries multiply the wait.
        pacer_deadline = time.monotonic() + pacing.MAX_ACQUIRE_WAIT_S
        for attempt in range(settings.genai_max_retries):
            pacing.acquire(
                _PROVIDER,
                model,
                est_tokens,
                max_wait_s=max(0.0, pacer_deadline - time.monotonic()),
            )
            try:
                completion = client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                genai_metrics.record(model, genai_metrics.OUTCOME_SERVER_ERROR)
                if not _retryable(exc):
                    raise
                last = exc
                if attempt < settings.genai_max_retries - 1:
                    _cancellable_sleep(_backoff(attempt))
                continue
            genai_metrics.record(model, genai_metrics.OUTCOME_ACCEPTED)
            # Recorded for symmetry with the other providers, though it can only ever raise the rate
            # toward a ceiling it already starts at - nothing on this path can lower it.
            pacing.record_success(_PROVIDER, model)
            return _to_response(completion)
        genai_metrics.record(model, genai_metrics.OUTCOME_EXHAUSTED)
        raise last
