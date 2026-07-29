"""Unit tests for the summary faithfulness verify pass (services.summary_verify).

Pure: the genai client + generate_with_retry are monkeypatched, so no Vertex call is made.
"""

import json

from app.services import summary_verify as sv


class _Resp:
    def __init__(self, text):
        self.text = text


def _fake_gen(payload):
    def gen(client, *, model, contents, config):
        return _Resp(json.dumps(payload))

    return gen


def test_flags_and_fixes_unsupported(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        sv,
        "generate_with_retry",
        _fake_gen(
            {
                "fixed_text": "Back pain noted.",
                "issues": [{"type": "unsupported", "detail": "knee surgery"}],
            }
        ),
    )
    result = sv.verify_summary("m", "back pain", "Back pain noted. Knee surgery done.")
    assert result["fixed_text"] == "Back pain noted."
    assert len(result["issues"]) == 1
    assert result["issues"][0]["type"] == "unsupported"


def test_faithful_summary_unchanged(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        sv, "generate_with_retry", _fake_gen({"fixed_text": "All supported.", "issues": []})
    )
    result = sv.verify_summary("m", "src", "All supported.")
    assert result["issues"] == []
    assert result["fixed_text"] == "All supported."


def test_blank_summary_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(sv, "get_genai_client", lambda: called.append(1))
    monkeypatch.setattr(sv, "generate_with_retry", _fake_gen({"fixed_text": "x", "issues": []}))
    result = sv.verify_summary("m", "src", "   ", title="A TITLE")
    assert result == {"fixed_text": "   ", "fixed_title": "A TITLE", "issues": []}
    assert called == []  # no model call for an empty summary


def test_model_failure_returns_original(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", boom)
    result = sv.verify_summary("m", "src", "Original summary.", title="ORIGINAL TITLE")
    # Fail-safe covers the title too: a broken check must never blank a good header.
    assert result == {
        "fixed_text": "Original summary.",
        "fixed_title": "ORIGINAL TITLE",
        "issues": [],
    }


def test_blank_fixed_text_keeps_original(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", _fake_gen({"fixed_text": "  ", "issues": []}))
    result = sv.verify_summary("m", "src", "Keep me.")
    assert result["fixed_text"] == "Keep me."


def test_title_is_audited_and_corrected(monkeypatch):
    # WHEN the pass finds a laterality error in the title, THE SYSTEM SHALL return a corrected title
    # alongside the corrected body, and name the issue type.
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        sv,
        "generate_with_retry",
        _fake_gen(
            {
                "fixed_text": "Body.",
                "fixed_title": "JANE SMITH, M.D. MRI OF THE LEFT KNEE",
                "issues": [{"type": "laterality", "detail": "title said right, source says left"}],
            }
        ),
    )
    result = sv.verify_summary(
        "m", "left knee MRI", "Body.", title="JANE SMITH, M.D. MRI OF THE RIGHT KNEE"
    )
    assert result["fixed_title"] == "JANE SMITH, M.D. MRI OF THE LEFT KNEE"
    assert result["issues"][0]["type"] == "laterality"


def test_title_is_sent_to_the_model_when_given(monkeypatch):
    seen = {}

    def gen(client, *, model, contents, config):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", title="A TITLE")
    assert "TITLE:\nA TITLE" in seen["contents"]
    assert "SOURCE:\nthe source" in seen["contents"]


def test_the_call_sets_its_own_thinking_budget(monkeypatch):
    # REGRESSION: the retry seam applies thinking_budget=0 to any call that does not set one, and
    # summary_model (2.5-pro) rejects 0 with a 400. Because this module is fail-safe, that 400 was
    # swallowed and every verify silently returned the original summary. The call must therefore
    # carry its own thinking_config.
    seen = {}

    def gen(client, *, model, contents, config):
        seen["thinking"] = config.thinking_config
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", gen)
    sv.verify_summary("m", "src", "Body.")
    assert seen["thinking"] is not None
    assert seen["thinking"].thinking_budget != 0


def test_blank_fixed_title_falls_back_to_the_original(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        sv,
        "generate_with_retry",
        _fake_gen({"fixed_text": "Body.", "fixed_title": "   ", "issues": []}),
    )
    result = sv.verify_summary("m", "src", "Body.", title="KEEP THIS TITLE")
    assert result["fixed_title"] == "KEEP THIS TITLE"
