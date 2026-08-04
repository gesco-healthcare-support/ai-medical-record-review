"""OpenAI provider: payload shape, PHI constraints, retry policy, and startup guards.

No network. The client is stubbed, so these pin the translation and the policy rather than
confirming that OpenAI is reachable.

The PHI tests are not style checks. Only /v1/chat/completions is zero-data-retention eligible, and
`store=False` is what makes a misconfigured organization fail safe instead of silently retaining a
medical record.
"""

import base64

import pytest

from app.services.llm import pacing
from app.services.llm.openai import (
    OpenAIProvider,
    _backoff,
    _strict_schema,
    _to_messages,
    _to_response,
)
from app.services.llm.parts import DocumentPart, ImagePart, TextPart


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 123
    completion_tokens = 45


class _Completion:
    def __init__(self, content="ok", finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage()


class _Raw:
    """Mimics with_raw_response: headers plus a parse() returning the completion."""

    def __init__(self, completion, headers=None):
        self._completion = completion
        self.headers = headers or {}

    def parse(self):
        return self._completion


@pytest.fixture
def captured(monkeypatch):
    """Capture the kwargs the provider sends, and neutralise pacing/metrics."""
    calls = {}

    # Mirrors the real shape verified against openai 2.53.0: the provider reads rate-limit headers,
    # so it must go through with_raw_response.create() and then parse().
    class _WithRawResponse:
        def create(self, **kwargs):
            calls.update(kwargs)
            return _Raw(
                _Completion(),
                {"x-ratelimit-remaining-requests": "600", "x-ratelimit-reset-requests": "60s"},
            )

    class _Completions:
        with_raw_response = _WithRawResponse()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr("app.services.llm.openai._client", lambda: _Client())
    monkeypatch.setattr(pacing, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(pacing, "record_success", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "record_rejection", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "observe_limits", lambda *a, **k: None)
    return calls


# --- PHI constraints ------------------------------------------------------------------------------


def test_store_is_always_false(captured):
    OpenAIProvider().generate_text(
        model="m", system="s", parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    # ZDR forces this server-side, but sending it explicitly means a misconfigured org fails safe.
    assert captured["store"] is False


def test_a_pdf_part_is_refused_rather_than_silently_dropped():
    # Chat completions has no inline-PDF part. Dropping it would send a request the caller believes
    # contained a document - the PDF paths (segmentation, DOI) stay on Gemini for this reason.
    with pytest.raises(TypeError, match="inline PDF"):
        _to_messages(None, [DocumentPart(b"%PDF-1.4")])


# --- payload shape --------------------------------------------------------------------------------


def test_system_prompt_becomes_a_system_message():
    messages = _to_messages("SYSTEM", [TextPart("body")])
    assert messages[0] == {"role": "system", "content": "SYSTEM"}
    assert messages[1]["role"] == "user"


def test_no_system_message_when_absent():
    assert _to_messages(None, [TextPart("body")])[0]["role"] == "user"


def test_part_order_survives_in_one_user_message():
    # Load-bearing: images, then OCR text, then the instruction LAST (G-03). Splitting parts across
    # messages would let the API's own ordering rules reshape the payload.
    messages = _to_messages(None, [ImagePart(b"img"), TextPart("ocr"), TextPart("instruction")])
    content = messages[-1]["content"]
    assert len(messages) == 1
    assert content[0]["type"] == "image_url"
    assert content[1]["text"] == "ocr"
    assert content[2]["text"] == "instruction"


def test_images_are_base64_data_urls():
    content = _to_messages(None, [ImagePart(b"abc", "image/jpeg")])[0]["content"]
    expected = base64.b64encode(b"abc").decode()
    assert content[0]["image_url"]["url"] == f"data:image/jpeg;base64,{expected}"


# --- structured output ----------------------------------------------------------------------------


def test_structured_call_uses_strict_json_schema(captured):
    OpenAIProvider().generate_structured(
        model="m",
        system="s",
        parts=[TextPart("hi")],
        schema={"type": "object", "properties": {"a": {"type": "string"}}},
        temperature=0.0,
        max_output_tokens=64,
    )
    fmt = captured["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_strict_schema_requires_every_property():
    # Strict mode has no optional fields: optionality is a null union, and every key must be listed.
    out = _strict_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": ["string", "null"]}},
        }
    )
    assert set(out["required"]) == {"a", "b"}
    assert out["additionalProperties"] is False


def test_the_real_audit_schema_passes_strict_mode():
    from app.services.summary_verify import _RESPONSE_SCHEMA

    out = _strict_schema(_RESPONSE_SCHEMA)
    assert set(out["required"]) == {"fixed_text", "fixed_title", "issues"}
    # fixed_title stays nullable - that is how "no title change" is expressed under strict mode.
    assert out["properties"]["fixed_title"]["type"] == ["string", "null"]
    assert out["properties"]["issues"]["items"]["additionalProperties"] is False


def test_text_call_sends_no_response_format(captured):
    OpenAIProvider().generate_text(
        model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    assert "response_format" not in captured


# --- responses ------------------------------------------------------------------------------------


def test_truncation_maps_from_finish_reason_length():
    # OpenAI's "length" is Gemini's MAX_TOKENS: a cut-off reply must never be stored as finished.
    assert _to_response(_Completion("half", "length")).truncated is True
    assert _to_response(_Completion("whole", "stop")).truncated is False


def test_usage_is_reported():
    result = _to_response(_Completion())
    assert (result.input_tokens, result.output_tokens) == (123, 45)


def test_an_empty_choice_list_does_not_crash():
    empty = _Completion()
    empty.choices = []
    assert _to_response(empty).text == ""


# --- retry policy ---------------------------------------------------------------------------------


def test_server_advised_delay_beats_backoff(monkeypatch):
    monkeypatch.setattr("app.services.llm.openai.random.uniform", lambda a, b: 0.0)
    # Unlike Vertex - whose 429 body was measured to carry nothing - OpenAI sends Retry-After.
    assert _backoff(0, advised=5.0) == 5.0


def test_backoff_is_bounded_by_the_configured_maximum(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr("app.services.llm.openai.random.uniform", lambda a, b: b)
    assert _backoff(20, advised=None) <= get_settings().genai_retry_max_delay
