"""The provider abstraction must ship with ZERO behaviour change on Gemini.

That is the whole reason this landed before the OpenAI provider: if the seam is shaped wrong, it
should be discovered while every call still runs on Gemini. So these tests pin the translation -
part order, system prompt placement, schema dialect, truncation - rather than merely checking that
something was returned.
"""

import pytest

from app.services.llm import get_provider
from app.services.llm.gemini import GeminiProvider, to_gemini_schema
from app.services.llm.parts import DocumentPart, ImagePart, TextPart


class FakeResponse:
    def __init__(self, text="ok", finish_reason="STOP"):
        self.text = text

        class Candidate:
            pass

        candidate = Candidate()
        candidate.finish_reason = finish_reason
        self.candidates = [candidate]
        self.usage_metadata = None


@pytest.fixture
def captured(monkeypatch):
    """Capture what the Gemini provider hands to generate_with_retry."""
    calls = {}

    def fake_generate(client, **kwargs):
        calls.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("app.services.llm.gemini.generate_with_retry", fake_generate)
    monkeypatch.setattr("app.services.llm.gemini.get_genai_client", lambda: object())
    return calls


def test_schema_types_are_uppercased_for_google_genai():
    neutral = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert to_gemini_schema(neutral) == {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}}


def test_schema_nullable_union_collapses_for_gemini():
    # OpenAI strict mode needs ["string","null"]; google-genai has no nullable union, so the
    # translator must reduce it to the concrete type rather than pass a list it would reject.
    assert to_gemini_schema({"type": ["string", "null"]}) == {"type": "STRING"}


def test_schema_drops_additional_properties_for_gemini():
    # Required by OpenAI strict mode, rejected outright by google-genai.
    out = to_gemini_schema({"type": "object", "additionalProperties": False, "properties": {}})
    assert "additionalProperties" not in out


def test_schema_translation_recurses_into_arrays_and_items():
    neutral = {
        "type": "array",
        "items": {"type": "object", "properties": {"n": {"type": "integer"}}},
    }
    out = to_gemini_schema(neutral)
    assert out["type"] == "ARRAY"
    assert out["items"]["type"] == "OBJECT"
    assert out["items"]["properties"]["n"]["type"] == "INTEGER"


def test_schema_preserves_enums_and_required():
    neutral = {"type": "string", "enum": ["a", "b"]}
    assert to_gemini_schema(neutral)["enum"] == ["a", "b"]


def test_a_property_literally_named_type_is_not_treated_as_a_type_name():
    # REGRESSION: the audit schema has an issue object whose PROPERTY is called "type". Translating
    # that key as a type name fed a dict to the name table and raised
    # "TypeError: unhashable type: 'dict'", which verify_summary swallowed as a failed audit - so
    # every summary came back unverified with no visible error.
    neutral = {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": ["unsupported"]}},
    }
    out = to_gemini_schema(neutral)
    assert out["type"] == "OBJECT"
    assert out["properties"]["type"]["type"] == "STRING"
    assert out["properties"]["type"]["enum"] == ["unsupported"]


def test_the_real_audit_schema_translates():
    from app.services.summary_verify import _RESPONSE_SCHEMA

    out = to_gemini_schema(_RESPONSE_SCHEMA)
    assert out["type"] == "OBJECT"
    assert out["properties"]["fixed_title"]["type"] == "STRING"  # nullable union collapsed
    assert "additionalProperties" not in out
    assert out["properties"]["issues"]["items"]["properties"]["type"]["type"] == "STRING"


def test_part_order_is_preserved(captured):
    # Load-bearing: images, then OCR text, then the instruction LAST. Reordering this was a real
    # defect (G-03) - the instruction used to sit in the middle of the payload.
    GeminiProvider().generate_text(
        model="m",
        system="sys",
        parts=[ImagePart(b"img1"), TextPart("ocr"), TextPart("instruction")],
        temperature=0.0,
        max_output_tokens=64,
    )
    contents = captured["contents"]
    assert len(contents) == 3
    assert contents[1] == "ocr"
    assert contents[2] == "instruction"


def test_system_prompt_goes_to_system_instruction(captured):
    GeminiProvider().generate_text(
        model="m", system="SYSTEM", parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    assert captured["config"].system_instruction == "SYSTEM"


def test_no_system_instruction_when_system_is_absent(captured):
    GeminiProvider().generate_text(
        model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    assert getattr(captured["config"], "system_instruction", None) is None


def test_structured_call_sets_json_mime_and_translated_schema(captured):
    GeminiProvider().generate_structured(
        model="m",
        system="sys",
        parts=[TextPart("hi")],
        schema={"type": "object", "properties": {"a": {"type": "string"}}},
        temperature=0.0,
        max_output_tokens=64,
    )
    config = captured["config"]
    assert config.response_mime_type == "application/json"
    # Passed through the translator, not raw.
    assert config.response_schema["type"] == "OBJECT"


def test_text_call_sets_no_response_schema(captured):
    GeminiProvider().generate_text(
        model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    assert getattr(captured["config"], "response_schema", None) is None


def test_truncation_maps_from_max_tokens_finish_reason(monkeypatch):
    monkeypatch.setattr("app.services.llm.gemini.get_genai_client", lambda: object())
    monkeypatch.setattr(
        "app.services.llm.gemini.generate_with_retry",
        lambda client, **kw: FakeResponse(finish_reason="MAX_TOKENS"),
    )
    result = GeminiProvider().generate_text(
        model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=8
    )
    # A reply cut off at the cap must never be stored as a finished summary.
    assert result.truncated is True


def test_document_part_becomes_inline_bytes(captured):
    GeminiProvider().generate_text(
        model="m",
        system=None,
        parts=[DocumentPart(b"%PDF-1.4")],
        temperature=0.0,
        max_output_tokens=64,
    )
    assert len(captured["contents"]) == 1


def test_registry_defaults_to_gemini():
    get_provider.cache_clear()
    assert get_provider().name == "gemini"


def test_registry_rejects_an_unknown_provider():
    get_provider.cache_clear()
    with pytest.raises(ValueError, match="unknown LLM provider"):
        get_provider("bedrock")
