"""The provider interface and its response type.

Small on purpose. It covers exactly what the pipeline asks of a model - free text, or JSON matching
a schema - and nothing else. An interface that anticipates capabilities nobody uses is an interface
nobody can change.

Two things deliberately do NOT appear here:

- Thinking budgets. They are a Gemini concept with no OpenAI equivalent, so they live inside the
  Gemini provider rather than being emulated or exposed as a no-op elsewhere.
- Retry and pacing policy. Those wrap the provider (see services.genai_retry and
  services.llm.pacing), so a provider implementation stays a translation layer and one adaptive
  pacer keeps bounding every vendor.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.services.llm.parts import Part


@dataclass(frozen=True)
class LLMResponse:
    """One completion, normalized.

    `truncated` matters more than it looks: a reply cut off at the token cap is a half summary, and
    storing it as finished is the failure this flag exists to prevent. Each provider derives it from
    its own finish reason (Gemini MAX_TOKENS, OpenAI "length") so callers never see either.

    Token counts are optional because not every provider reports them on every path; callers use
    them for accounting, never for control flow.
    """

    text: str
    truncated: bool
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    """What the pipeline needs from a model vendor."""

    name: str

    def generate_text(
        self,
        *,
        model: str,
        system: str | None,
        parts: list[Part],
        temperature: float,
        max_output_tokens: int,
    ) -> LLMResponse:
        """Free-text completion. `parts` are sent in the order given - which is load-bearing for the
        multimodal summary call, where images must precede the OCR text and the instruction must
        come last (G-03, matching Google's context-first / instruction-last guidance)."""
        ...

    def generate_structured(
        self,
        *,
        model: str,
        system: str | None,
        parts: list[Part],
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
    ) -> LLMResponse:
        """JSON completion constrained by `schema`.

        `schema` is ordinary JSON Schema with lowercase type names ("object", "string", "array").
        Each provider translates to its own dialect, because the alternative - writing the schema in
        one vendor's dialect and translating for the other - silently makes that vendor the default
        and the other the special case.

        The returned `text` is the raw JSON string; parsing stays with the caller, which already
        knows what shape it expects and how to fail safe.
        """
        ...
