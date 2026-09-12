"""Provider registry.

`get_provider()` with no argument returns the provider the summarize stage is configured to use, so
call sites do not each re-read config and cannot drift apart on which vendor they picked.

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
