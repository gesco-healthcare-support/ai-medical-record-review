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


# --- retry classification and the loop ------------------------------------------------------------
#
# These are the paths that matter most and were the ones left uncovered: whether a failure is
# retried at all decides between riding out a rate limit and hammering a dead endpoint.


def _status_error(status, message=""):
    """An exception shaped the way _retryable inspects one (status_code + message)."""
    import openai

    exc = openai.APIStatusError.__new__(openai.APIStatusError)
    exc.status_code = status
    exc.message = message
    exc.response = None
    return exc


def _with_headers(status, headers):
    exc = _status_error(status)

    class _Response:
        pass

    response = _Response()
    response.headers = headers
    exc.response = response
    return exc


def test_rate_limit_and_server_errors_are_retryable():
    from app.services.llm.openai import _retryable

    assert _retryable(_status_error(429))[0] is True
    assert _retryable(_status_error(500))[0] is True
    assert _retryable(_status_error(503))[0] is True


def test_client_errors_are_not_retryable():
    from app.services.llm.openai import _retryable

    # Backoff cannot fix a bad request, a bad key, or a missing model.
    for status in (400, 401, 403, 404):
        assert _retryable(_status_error(status))[0] is False


def test_an_exhausted_quota_is_not_retryable_even_though_it_is_a_429():
    from app.services.llm.openai import _retryable

    # Mirrors the PerDay/free_tier carve-out genai_retry makes for Gemini: no amount of waiting
    # inside one request refills a spent budget.
    assert (
        _retryable(_status_error(429, "insufficient_quota: you exceeded your current quota"))[0]
        is False
    )


def test_timeouts_and_connection_errors_are_retryable():
    import openai

    from app.services.llm.openai import _retryable

    timeout = openai.APITimeoutError.__new__(openai.APITimeoutError)
    conn = openai.APIConnectionError.__new__(openai.APIConnectionError)
    assert _retryable(timeout)[0] is True
    assert _retryable(conn)[0] is True


def test_retry_after_header_is_read_when_present():
    from app.services.llm.openai import _retryable

    retry, advised = _retryable(_with_headers(429, {"retry-after": "7"}))
    assert (retry, advised) == (True, 7.0)


def test_a_missing_or_unparseable_retry_after_falls_back_to_backoff():
    from app.services.llm.openai import _retry_after

    assert _retry_after(_with_headers(429, {})) is None
    assert _retry_after(_with_headers(429, {"retry-after": "soon"})) is None


def test_headers_feed_the_pacer(monkeypatch):
    from app.services.llm import openai as provider

    seen = {}
    monkeypatch.setattr(
        provider.pacing,
        "observe_limits",
        lambda p, m, rr, resr, rt, rest: seen.update(
            provider=p, model=m, rem_req=rr, reset_req=resr, rem_tok=rt, reset_tok=rest
        ),
    )

    class _Raw2:
        headers = {
            "x-ratelimit-remaining-requests": "4999",
            "x-ratelimit-reset-requests": "12s",
            "x-ratelimit-remaining-tokens": "1999990",
            "x-ratelimit-reset-tokens": "6s",
        }

    provider._observe("gpt-4.1-mini", _Raw2())
    # The "s" suffix must be stripped, or the rate lands as None and the controller keeps guessing.
    assert seen["rem_req"] == 4999.0
    assert seen["reset_req"] == 12.0
    assert seen["rem_tok"] == 1999990.0


def _provider_with(monkeypatch, sequence):
    """A provider whose create() yields each item: an exception is raised, anything else returned."""
    from app.services.llm import openai as provider

    state = {"i": 0}

    class _WithRaw:
        def create(self, **_kwargs):
            item = sequence[state["i"]]
            state["i"] += 1
            if isinstance(item, BaseException):
                raise item
            return item

    class _Completions:
        with_raw_response = _WithRaw()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(provider, "_client", lambda: _Client())
    monkeypatch.setattr(provider, "_cancellable_sleep", lambda _s: None)
    monkeypatch.setattr(provider.pacing, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(provider.pacing, "record_success", lambda *a, **k: None)
    monkeypatch.setattr(provider.pacing, "record_rejection", lambda *a, **k: None)
    monkeypatch.setattr(provider.pacing, "observe_limits", lambda *a, **k: None)
    return state


def test_a_rate_limited_call_is_retried_and_then_succeeds(monkeypatch):
    state = _provider_with(monkeypatch, [_status_error(429), _Raw(_Completion("done"))])
    result = OpenAIProvider().generate_text(
        model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=16
    )
    assert result.text == "done"
    assert state["i"] == 2


def test_a_non_retryable_error_raises_immediately(monkeypatch):
    import openai

    state = _provider_with(monkeypatch, [_status_error(400), _Raw(_Completion("never"))])
    with pytest.raises(openai.APIStatusError):
        OpenAIProvider().generate_text(
            model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=16
        )
    # Exactly one attempt: a 400 must not consume the retry budget.
    assert state["i"] == 1


def test_retries_are_exhausted_and_the_last_error_is_raised(monkeypatch):
    import openai

    from app.config import get_settings

    attempts = get_settings().genai_max_retries
    _provider_with(monkeypatch, [_status_error(429) for _ in range(attempts)])
    with pytest.raises(openai.APIStatusError):
        OpenAIProvider().generate_text(
            model="m", system=None, parts=[TextPart("hi")], temperature=0.0, max_output_tokens=16
        )


def test_backoff_is_abandoned_when_the_job_is_cancelled(monkeypatch):
    from app.services.llm import openai as provider
    from app.worker.failures import JobCancelled

    monkeypatch.setattr(provider, "current_job_cancelled", lambda: True)
    # Without this, a job wedged in backoff cannot notice the stop button until the whole wait is
    # served - the exact case a reviewer wants to kill.
    with pytest.raises(JobCancelled):
        provider._cancellable_sleep(30.0)


# --- temperature capability -----------------------------------------------------------------------


def test_a_model_that_rejects_temperature_is_retried_without_it(monkeypatch):
    # MEASURED 2026-08-05: the whole gpt-5.6 family returns 400 for any non-default temperature,
    # while gpt-4.1 accepts 0.0. Learned at runtime rather than hardcoded to a name prefix, which
    # would be wrong for the next family.
    from app.services.llm import openai as provider

    provider._NO_TEMPERATURE.discard("gpt-5.6-luna")
    rejection = _status_error(
        400, "Unsupported value: 'temperature' does not support 0 with this model."
    )
    sent = []

    class _WithRaw:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            sent.append(dict(kwargs))
            self.calls += 1
            if self.calls == 1:
                raise rejection
            return _Raw(_Completion("done"))

    class _Completions:
        with_raw_response = _WithRaw()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(provider, "_client", lambda: _Client())
    monkeypatch.setattr(provider, "_cancellable_sleep", lambda _s: None)
    for name in ("acquire", "record_success", "record_rejection", "observe_limits"):
        monkeypatch.setattr(provider.pacing, name, lambda *a, **k: True)

    result = OpenAIProvider().generate_text(
        model="gpt-5.6-luna",
        system=None,
        parts=[TextPart("hi")],
        temperature=0.0,
        max_output_tokens=16,
    )
    assert result.text == "done"
    assert "temperature" in sent[0]
    assert "temperature" not in sent[1]
    # Remembered, so every later call skips it before it is sent.
    assert "gpt-5.6-luna" in provider._NO_TEMPERATURE
    provider._NO_TEMPERATURE.discard("gpt-5.6-luna")


def test_a_model_that_accepts_temperature_keeps_receiving_it(captured):
    from app.services.llm import openai as provider

    provider._NO_TEMPERATURE.discard("gpt-4.1-mini")
    OpenAIProvider().generate_text(
        model="gpt-4.1-mini",
        system=None,
        parts=[TextPart("hi")],
        temperature=0.0,
        max_output_tokens=16,
    )
    # summary_temperature=0.0 is a measured determinism guarantee, not a default to drop casually.
    assert captured["temperature"] == 0.0


def test_an_unrelated_400_is_not_mistaken_for_a_temperature_rejection():
    from app.services.llm.openai import _rejects_temperature

    assert _rejects_temperature(_status_error(400, "Invalid schema for response_format")) is False
    assert _rejects_temperature(_status_error(429, "temperature")) is False
