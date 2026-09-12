"""vLLM provider: the four deliberate divergences from the OpenAI provider, and the retry policy.

No network. The client is stubbed, so these pin what the app SENDS - which is the only thing they
can pin. vLLM sets ``ConfigDict(extra="allow")``, so it accepts unknown fields and silently ignores
them; a test that asserted the server's behaviour from a stub would be asserting a fiction.

THE NEGATIVE ASSERTIONS ARE WRITTEN AS COMPARISONS on purpose. ``assert "store" not in sent`` passes
just as happily when the capture is broken, when the provider raised before building kwargs, or when
someone renames the key - a fixture that cannot express the failure cannot prove its absence. So each
one is paired against the OpenAI provider driven through an equivalent stub: that call DOES send the
key, which is what demonstrates these tests would notice if vLLM started sending it too.
"""

import pytest

from app.services.llm import pacing
from app.services.llm.base import DelegatingProvider
from app.services.llm.openai import OpenAIProvider
from app.services.llm.parts import DocumentPart, ImagePart, TextPart
from app.services.llm.vllm import VLLMProvider, _backoff, _retryable


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 11
    completion_tokens = 22


class _Completion:
    def __init__(self, content="ok", finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage()


class _Raw:
    """Mimics with_raw_response, which the OpenAI provider uses and this one deliberately does not."""

    def __init__(self, completion, headers=None):
        self._completion = completion
        self.headers = headers or {}

    def parse(self):
        return self._completion


def _quiet_pacing(monkeypatch):
    monkeypatch.setattr(pacing, "acquire", lambda *a, **k: True)
    monkeypatch.setattr(pacing, "record_success", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "record_rejection", lambda *a, **k: None)
    monkeypatch.setattr(pacing, "observe_limits", lambda *a, **k: None)


@pytest.fixture
def sent(monkeypatch):
    """Capture the kwargs the vLLM provider sends. It calls create() directly - no raw response,
    because there are no rate-limit headers on this path to read."""
    calls = {}

    class _Completions:
        def create(self, **kwargs):
            calls.update(kwargs)
            return _Completion()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr("app.services.llm.vllm._client", lambda: _Client())
    _quiet_pacing(monkeypatch)
    return calls


@pytest.fixture
def sent_openai(monkeypatch):
    """The same capture against the OpenAI provider, so the negative assertions have a control."""
    calls = {}

    class _WithRawResponse:
        def create(self, **kwargs):
            calls.update(kwargs)
            return _Raw(_Completion())

    class _Completions:
        with_raw_response = _WithRawResponse()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr("app.services.llm.openai._client", lambda: _Client())
    _quiet_pacing(monkeypatch)
    return calls


def _run_vllm(schema=None, max_output_tokens=64):
    provider = VLLMProvider()
    kwargs = {
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "system": "s",
        "parts": [TextPart("hi")],
        "temperature": 0.0,
        "max_output_tokens": max_output_tokens,
    }
    if schema is None:
        return provider.generate_text(**kwargs)
    return provider.generate_structured(schema=schema, **kwargs)


# --- divergence 1: no `store` -----------------------------------------------------------------------


def test_store_is_never_sent_while_openai_does_send_it(sent, sent_openai):
    """store is a PHI control ON OPENAI and meaningless here.

    vLLM has no such field and does not reject unknown ones (extra="allow" since PR #10463), so
    sending it would LOOK like a control while doing nothing - worse than not sending it, because
    nothing surfaces the difference. The real control for this path is the approved-destination
    check in config, since the endpoint is our own box.
    """
    _run_vllm()
    OpenAIProvider().generate_text(
        model="m", system="s", parts=[TextPart("hi")], temperature=0.0, max_output_tokens=64
    )
    assert sent_openai["store"] is False, "control failed: the OpenAI provider should still send it"
    assert "store" not in sent


# --- divergence 2: no `strict` ----------------------------------------------------------------------


def test_strict_is_not_sent_but_the_schema_still_carries_the_constraint(sent, sent_openai):
    # vLLM parses response_format and passes only the SCHEMA through, dropping strict. Strictness has
    # to travel inside the schema instead, which _strict_schema does.
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    _run_vllm(schema=schema)
    OpenAIProvider().generate_structured(
        model="m",
        system="s",
        parts=[TextPart("hi")],
        schema=schema,
        temperature=0.0,
        max_output_tokens=64,
    )
    control = sent_openai["response_format"]["json_schema"]
    assert control["strict"] is True, "control failed: the OpenAI provider should still send strict"

    fmt = sent["response_format"]["json_schema"]
    assert "strict" not in fmt
    # The constraint survives where it actually binds: inside the schema.
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["a"]


# --- divergence 3: thinking off ---------------------------------------------------------------------


def test_thinking_is_sent_explicitly_off_on_every_call(sent):
    """Measured 2026-09-11: with thinking on, 25 of 476 rows on a 1,314-page record came back EMPTY -
    finish_reason=length at exactly the 8,192 cap, 23,878 to 30,891 characters of reasoning and no
    summary. The model spends the output allowance on reasoning."""
    _run_vllm()
    assert sent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_a_thinking_budget_is_never_sent(sent):
    # A bounded budget does not fix the empty summary, it relocates it: the reasoning is then written
    # INTO the reply. Off is the only safe value, so the field must not appear at all.
    _run_vllm(schema={"type": "object", "properties": {}})
    flat = str(sent)
    assert "thinking_token_budget" not in flat
    assert "reasoning_effort" not in flat


def test_thinking_is_off_on_the_structured_path_too(sent):
    # Easy to set on one code path and forget on the other; the structured path is the one that runs
    # for four of the seven services.
    _run_vllm(schema={"type": "object", "properties": {"a": {"type": "string"}}})
    assert sent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


# --- divergence 4: a client-side timeout is not retried ---------------------------------------------


def _provider_with(monkeypatch, sequence):
    """A provider whose create() yields each item: an exception is raised, anything else returned."""
    from app.services.llm import vllm as provider

    state = {"i": 0}

    class _Completions:
        def create(self, **_kwargs):
            item = sequence[state["i"]]
            state["i"] += 1
            if isinstance(item, BaseException):
                raise item
            return item

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(provider, "_client", lambda: _Client())
    monkeypatch.setattr(provider, "_cancellable_sleep", lambda _s: None)
    _quiet_pacing(monkeypatch)
    return state


def test_a_read_timeout_is_not_retried(monkeypatch):
    """The money divergence. vLLM keeps generating after our client gives up, so a retry starts a
    SECOND generation while the first still holds the card - at genai_max_retries of 8 that is eight
    concurrent generations of one prompt and roughly sixteen minutes of rented GPU for no answer."""
    import openai

    timeout = openai.APITimeoutError.__new__(openai.APITimeoutError)
    state = _provider_with(monkeypatch, [timeout, _Completion("never")])
    with pytest.raises(openai.APITimeoutError):
        _run_vllm()
    assert state["i"] == 1, "a read timeout must not consume the retry budget"


def test_the_openai_provider_still_retries_a_timeout():
    # Control for the test above: the divergence is real rather than a mirror of shared code. That
    # policy is right against an API that has probably dropped the request, and wrong against a GPU
    # we are paying for by the second.
    import openai

    from app.services.llm.openai import _retryable as openai_retryable

    timeout = openai.APITimeoutError.__new__(openai.APITimeoutError)
    # Note the shapes differ, and deliberately: OpenAI returns (retry, advised_delay) because it
    # really does send Retry-After, while vLLM returns a plain bool because it never does.
    assert openai_retryable(timeout)[0] is True
    assert _retryable(timeout) is False


def test_a_connection_error_is_still_retried(monkeypatch):
    # Nothing was ever generating, so nothing is wasted - and a tunnel that blinked during a pod
    # restart is exactly what a retry is for.
    import openai

    conn = openai.APIConnectionError.__new__(openai.APIConnectionError)
    state = _provider_with(monkeypatch, [conn, _Completion("done")])
    assert _run_vllm().text == "done"
    assert state["i"] == 2


def test_server_errors_are_retried_and_client_errors_are_not():
    import openai

    def _status(code):
        exc = openai.APIStatusError.__new__(openai.APIStatusError)
        exc.status_code = code
        exc.message = ""
        exc.response = None
        return exc

    assert _retryable(_status(500)) is True
    assert _retryable(_status(503)) is True
    for code in (400, 401, 404):
        assert _retryable(_status(code)) is False


def test_backoff_is_bounded_by_the_configured_maximum(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr("app.services.llm.vllm.random.uniform", lambda a, b: b)
    assert _backoff(20) <= get_settings().genai_retry_max_delay


# --- payload shape ----------------------------------------------------------------------------------


def test_an_absent_token_cap_is_omitted_rather_than_invented(sent):
    """Four of the seven services that will route here set no cap today. A seam that demands one
    forces the caller to invent a number, and this repo has shipped that bug twice."""
    _run_vllm(max_output_tokens=None)
    assert "max_completion_tokens" not in sent


def test_a_token_cap_is_passed_through_when_given(sent):
    _run_vllm(max_output_tokens=4096)
    assert sent["max_completion_tokens"] == 4096


def test_a_pdf_part_is_refused_rather_than_silently_dropped(sent):
    # Chat completions has no inline-PDF part on either backend. Rasterising for the vLLM path is
    # PR 3; until then a document must fail loudly rather than arrive as an empty request.
    #
    # The provider and the parts are built OUTSIDE the raises block deliberately (python:S5778).
    # With the constructor inside it, a TypeError raised while BUILDING the provider would satisfy
    # this assertion just as well as the one the test is actually about.
    provider = VLLMProvider()
    parts = [DocumentPart(b"%PDF-1.4")]
    with pytest.raises(TypeError, match="inline PDF"):
        provider.generate_text(
            model="m", system=None, parts=parts, temperature=0.0, max_output_tokens=64
        )


# --- the shared base --------------------------------------------------------------------------------


def test_the_shared_base_refuses_to_be_used_without_a_call_implementation():
    """DelegatingProvider carries the three public methods for all three backends.

    It is not usable on its own, and that has to fail loudly: a subclass that forgot `_call` would
    otherwise return None from every method, and None has a `.text` nowhere - so the failure would
    surface several frames away from the class that caused it.
    """
    base = DelegatingProvider()
    with pytest.raises(NotImplementedError):
        base.generate_text(model="m", system=None, parts=[TextPart("hi")], temperature=0.0)


def test_every_provider_shares_one_implementation_of_the_public_methods():
    # The duplication fix, asserted rather than assumed: if someone re-adds a forwarding copy to a
    # provider, this notices. Three near-identical copies are what tripped the SonarCloud
    # new-code duplication gate at 3.8% against a 3% threshold.
    from app.services.llm.gemini import GeminiProvider
    from app.services.llm.openai import OpenAIProvider

    for method in ("generate_text", "generate_structured"):
        assert getattr(VLLMProvider, method) is getattr(DelegatingProvider, method)
        assert getattr(GeminiProvider, method) is getattr(DelegatingProvider, method)
        assert getattr(OpenAIProvider, method) is getattr(DelegatingProvider, method)
    # generate_choice is the documented exception: OpenAI has no bare-enum mode and must unwrap.
    assert VLLMProvider.generate_choice is DelegatingProvider.generate_choice
    assert GeminiProvider.generate_choice is DelegatingProvider.generate_choice
    assert OpenAIProvider.generate_choice is not DelegatingProvider.generate_choice


def test_part_order_survives_into_one_user_message(sent):
    # Load-bearing for the multimodal summary call: images, then OCR text, then the instruction last.
    VLLMProvider().generate_text(
        model="m",
        system=None,
        parts=[ImagePart(b"img"), TextPart("ocr"), TextPart("instruction")],
        temperature=0.0,
        max_output_tokens=64,
    )
    content = sent["messages"][-1]["content"]
    assert content[0]["type"] == "image_url"
    assert content[1]["text"] == "ocr"
    assert content[2]["text"] == "instruction"


def test_truncation_and_usage_come_back_normalised(sent):
    result = _run_vllm()
    assert result.truncated is False
    assert (result.input_tokens, result.output_tokens) == (11, 22)
