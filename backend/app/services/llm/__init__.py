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
    """The provider called `name`, or the configured summarize provider when omitted.

    Imports are deferred into the branches so selecting Gemini never imports the OpenAI SDK, and
    vice versa - which keeps a missing optional dependency from breaking an unrelated path.
    """
    name = (name or get_settings().summary_provider or "gemini").strip().lower()
    if name == "gemini":
        from app.services.llm.gemini import GeminiProvider

        return GeminiProvider()
    if name == "openai":
        from app.services.llm.openai import OpenAIProvider

        return OpenAIProvider()
    raise ValueError(f"unknown LLM provider: {name!r} (expected 'gemini' or 'openai')")
