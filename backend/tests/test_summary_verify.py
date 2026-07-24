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
    result = sv.verify_summary("m", "src", "   ")
    assert result == {"fixed_text": "   ", "issues": []}
    assert called == []  # no model call for an empty summary


def test_model_failure_returns_original(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", boom)
    result = sv.verify_summary("m", "src", "Original summary.")
    assert result == {"fixed_text": "Original summary.", "issues": []}


def test_blank_fixed_text_keeps_original(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", _fake_gen({"fixed_text": "  ", "issues": []}))
    result = sv.verify_summary("m", "src", "Keep me.")
    assert result["fixed_text"] == "Keep me."
