"""Provider registry.

TWO ENTRY POINTS, AND PICKING THE WRONG ONE FAILS SILENTLY. `get_provider()` with no argument
resolves through `backend_for("summarize")`, so it is correct ONLY for the summarize stage - which
was every caller that existed until the non-PDF services began crossing this seam. Any other stage
asks `provider_for_stage(stage)`. A stage that takes its model from `model_for_stage(stage)` and its
transport from a bare `get_provider()` resolves the two through DIFFERENT stages, and they agree
only while nothing is routed independently.

Cached per name because a provider is a stateless translator over a cached client; building one per
call would rebuild nothing useful.
"""

from functools import lru_cache

from app.config import get_settings
from app.services.llm.base import LLMProvider, LLMResponse
from app.services.llm.parts import DocumentPart, ImagePart, Part, TextPart

__all__ = [
    "DocumentPart",
    "ImagePart",
    "LLMProvider",
    "LLMResponse",
    "Part",
    "TextPart",
    "get_provider",
    "provider_for_stage",
]


@lru_cache
def get_provider(name: str | None = None) -> LLMProvider:
    """The provider called `name`, or the one configured for the summarize stage when omitted.

    A NAMED request is answered literally, whatever the configuration says. That is not an
    oversight: callers that name a provider are asking for that vendor specifically rather than for
    "whatever the app is using", and the benchmark harness depends on it - a judge that silently
    followed llm_backend would end up scoring its own arm.

    An OMITTED name resolves through `backend_for("summarize")`, so a per-stage override is honoured
    here too. `summary_provider == "openai"` still wins while it exists: deployments set that key
    today, and having a new setting silently override a live one would move traffic to a different
    vendor on upgrade with nothing in the diff saying so.

    Imports are deferred into the branches so selecting Gemini never imports the OpenAI SDK, and
    vice versa - which keeps a missing optional dependency from breaking an unrelated path.
    """
    if name is None:
        settings = get_settings()
        name = (
            "openai" if settings.summary_provider == "openai" else settings.backend_for("summarize")
        )
    name = (name or "gemini").strip().lower()
    if name == "gemini":
        from app.services.llm.gemini import GeminiProvider

        return GeminiProvider()
    if name == "openai":
        from app.services.llm.openai import OpenAIProvider

        return OpenAIProvider()
    if name == "vllm":
        from app.services.llm.vllm import VLLMProvider

        return VLLMProvider()
    raise ValueError(f"unknown LLM provider: {name!r} (expected 'gemini', 'openai' or 'vllm')")


def provider_for_stage(stage: str) -> LLMProvider:
    """The provider answering ONE stage's calls, resolved for the backend that stage selects.

    PAIRS WITH `Settings.model_for_stage`, and the pairing is the point. A caller taking its model
    from `model_for_stage(stage)` and its transport from a bare `get_provider()` resolves the two
    through different stages - its own for the model, summarize for the transport. They agree only
    while nothing is routed independently, which is exactly the condition this seam exists to end.
    That is not hypothetical: it shipped in #318 and was found in review, not by a test.

    `stage=` on the generate_* methods does NOT do this. `gemini.py` uses it only for
    `thinking_for(stage)`, and `vllm.py` and `openai.py` both `del stage` - it picks a thinking
    budget, never a transport.

    SUMMARIZE IS REFUSED, and asymmetrically with the rest on purpose. Bare `get_provider()` also
    honours `summary_provider == "openai"`, a selector deployments set today and one this function
    cannot see; routing summarize through here would drop it silently. Summarize keeps
    `get_provider()`, which is correct for it and only for it.

    Unknown stages raise from `backend_for`, so there is no second list to keep in step.
    """
    if stage == "summarize":
        raise KeyError(
            "summarize keeps get_provider(): it also honours summary_provider, which this cannot see"
        )
    return get_provider(get_settings().backend_for(stage))
