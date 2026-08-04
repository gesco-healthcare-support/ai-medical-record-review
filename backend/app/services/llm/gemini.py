"""Gemini provider: translates the neutral interface onto google-genai.

This wraps ``genai_retry.generate_with_retry`` rather than calling the client directly, so retry,
backoff, the shared rate-limit bucket and the per-model metrics all keep working exactly as they do
today. That is the whole point of landing this provider first: the abstraction ships with zero
behaviour change, and if the shape is wrong we find out while everything still runs on Gemini.
"""

from typing import Any

from google.genai import types

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.llm.base import LLMResponse
from app.services.llm.parts import DocumentPart, ImagePart, Part, TextPart
from app.services.llm.tokens import estimate_tokens

# google-genai dict schemas use UPPERCASE type names; ordinary JSON Schema is lowercase. Callers
# write lowercase (see base.generate_structured) and this maps it, so no vendor dialect leaks into
# shared code.
_TYPE_NAMES = {
    "object": "OBJECT",
    "string": "STRING",
    "array": "ARRAY",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "null": "NULL",
}


def _names_a_type(value: Any) -> bool:
    """True when a `type` value is a type NAME rather than a nested schema.

    JSON Schema allows a property to be called "type"; the audit schema has one. Distinguishing the
    keyword from the property name is what stops a nested schema being fed to the name table.
    """
    if isinstance(value, str):
        return True
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def to_gemini_schema(schema: Any) -> Any:
    """Rewrite a JSON Schema's type names into google-genai's dialect, recursively.

    Only `type` is translated. `properties`, `items`, `required` and `enum` already share a spelling,
    and anything else is passed through untouched rather than silently dropped - a schema key this
    function does not understand is likelier to be new than to be wrong.

    A union type (`["string", "null"]`, which OpenAI strict mode requires for optional fields) is
    collapsed to its first non-null member: Gemini expresses optionality by omission from `required`,
    not by a nullable union, so keeping the union would produce a schema it rejects.
    """
    if isinstance(schema, list):
        return [to_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        # "type" is only the JSON Schema keyword when its value NAMES a type. A schema may also
        # carry a PROPERTY called "type" whose value is itself a schema - the audit's issue objects
        # have exactly that - and translating it as a type name raised TypeError on the dict.
        if key == "type" and _names_a_type(value):
            if isinstance(value, list):
                concrete = [t for t in value if t != "null"] or ["string"]
                out[key] = _TYPE_NAMES.get(concrete[0], concrete[0])
            else:
                out[key] = _TYPE_NAMES.get(value, value)
        elif key == "additionalProperties":
            continue  # OpenAI strict mode requires it; google-genai rejects the key outright
        else:
            out[key] = to_gemini_schema(value)
    return out


def _to_contents(parts: list[Part]) -> list[Any]:
    """Neutral parts -> google-genai contents, order preserved.

    Order is load-bearing for the multimodal summary call: images, then OCR text, then the
    instruction last.
    """
    contents: list[Any] = []
    for part in parts:
        if isinstance(part, TextPart):
            contents.append(part.text)
        elif isinstance(part, (ImagePart, DocumentPart)):
            contents.append(types.Part.from_bytes(data=part.data, mime_type=part.mime_type))
        else:  # pragma: no cover - the union is closed; this guards a future addition
            raise TypeError(f"unsupported part type: {type(part).__name__}")
    return contents


def _hit_token_cap(response) -> bool:
    """True when the model stopped because it exhausted max_output_tokens.

    Read defensively (name or str) so a client-library enum change cannot crash a summary. Moved
    here from summarize_engine unchanged - the audit path needs the same check, and truncation is a
    provider detail rather than a summarization one.
    """
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        if str(getattr(reason, "name", reason)).upper().endswith("MAX_TOKENS"):
            return True
    return False


def _usage(response) -> tuple[int | None, int | None]:
    """(input, output) token counts when the response reports them, else (None, None)."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None, None
    return (
        getattr(meta, "prompt_token_count", None),
        getattr(meta, "candidates_token_count", None),
    )


class GeminiProvider:
    """LLMProvider over google-genai."""

    name = "gemini"

    def _call(self, *, model, system, parts, temperature, max_output_tokens, schema=None):
        settings = get_settings()
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            # summary_model is a thinking model (2.5-pro) that rejects the retry seam's default
            # budget of 0 with a 400. Setting it explicitly here is what stopped that rejection
            # being silent - see summary_doi, where the same omission made every call return "-".
            "thinking_config": types.ThinkingConfig(
                thinking_budget=settings.summary_thinking_budget
            ),
        }
        if system:
            config_kwargs["system_instruction"] = system
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = to_gemini_schema(schema)
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=_to_contents(parts),
            config=types.GenerateContentConfig(**config_kwargs),
            # Charged to the pacer's token meter. A 30,000-token segmentation window and a 500-token
            # title call cost the shared pool very differently, so counting both as "one request"
            # would mis-pace whichever way the traffic mix leans.
            _est_tokens=estimate_tokens(parts, system, "gemini"),
        )
        input_tokens, output_tokens = _usage(response)
        return LLMResponse(
            text=(response.text or "").strip(),
            truncated=_hit_token_cap(response),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

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
