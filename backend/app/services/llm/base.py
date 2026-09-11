"""The provider interface and its response type.

Small on purpose. It covers exactly what the pipeline asks of a model - free text, or JSON matching
a schema - and nothing else. An interface that anticipates capabilities nobody uses is an interface
nobody can change.

WHAT DOES NOT APPEAR HERE, and one correction to an earlier version of this list:

- Thinking budgets, still. But NOT for the reason previously given. This used to say they are "a
  Gemini concept with no OpenAI equivalent"; that is no longer true - vLLM takes `enable_thinking`
  as a chat-template argument and treats `reasoning_effort` as a first-class field. The reason they
  stay out is now the opposite one: every backend has a notion of thinking and no two express it
  alike, and the RIGHT value is a property of the stage rather than of the vendor. So callers pass a
  `stage` and each provider resolves thinking for itself - Gemini through `settings.thinking_for`,
  vLLM by sending it explicitly off. An interface carrying a budget would force one vendor's
  spelling on the others.
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


# The historical seam default. Both call sites that existed before stages were introduced -
# summarize_engine and summary_verify - took `summary_thinking_budget`, which is exactly what
# `thinking_for("summarize")` returns, so this default reproduces today's behaviour to the value.
#
# IT IS A DEFAULT, NOT A FALLBACK. Every new caller passes its own stage; a service that takes this
# one by omission gets the summarize budget, which is right for summarize and wrong for the other
# seven. The alternative - no default - would have broken the thirty-odd monkeypatched seams this
# repo warns about in summary_verify, for no gain in a codebase where the tests pin the argument.
_DEFAULT_STAGE = "summarize"


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
        stage: str = _DEFAULT_STAGE,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """Free-text completion. `parts` are sent in the order given - which is load-bearing for the
        multimodal summary call, where images must precede the OCR text and the instruction must
        come last (G-03, matching Google's context-first / instruction-last guidance).

        `max_output_tokens` is OPTIONAL, and that is a correction rather than a convenience. Four of
        the services that cross this seam set no cap at all today, so an interface demanding one
        forces the caller to invent a number - a bug this repo has already shipped twice, most
        recently where an audit carried eleven times the budget its largest real answer had needed.
        """
        ...

    def generate_structured(
        self,
        *,
        model: str,
        system: str | None,
        parts: list[Part],
        schema: dict[str, Any],
        temperature: float,
        stage: str = _DEFAULT_STAGE,
        max_output_tokens: int | None = None,
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

    def generate_choice(
        self,
        *,
        model: str,
        system: str | None,
        parts: list[Part],
        choices: list[str],
        temperature: float,
        stage: str = _DEFAULT_STAGE,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        """One value from a fixed list, constrained by the backend rather than by hope.

        HERE BECAUSE THE INTERFACE COULD NOT EXPRESS IT, and two services were therefore unable to
        cross this seam at all. Categorization and the boundary verify pass both want exactly one
        label from a closed set, which Gemini enforces natively with
        `response_mime_type="text/x.enum"`. There was no way to say that through `generate_text`
        (unconstrained) or `generate_structured` (an object, not a bare value), so both called
        google-genai directly and stayed outside the abstraction.

        Every backend can enforce it: Gemini via the enum mime type, vLLM via
        `structured_outputs: {"choice": [...]}`, OpenAI by a single-property schema. Returning
        `text` as the chosen string keeps callers identical across all three.
        """
        ...
