"""llm_classify model wiring: it uses the (cheap) classify_model by default and honors an override.

An A/B showed Flash-Lite matches full Flash on the labeled taxonomy examples (identical accuracy,
100% agreement), so classification runs on the cheaper tier. These tests pin the model selection;
the Vertex call itself is stubbed.
"""

from types import SimpleNamespace

from app.config import get_settings
from app.services import classification


def _stub_generate(captured):
    def fake(client, **kwargs):
        captured["model"] = kwargs.get("model")
        return SimpleNamespace(text="1")  # a valid category id

    return fake


def test_llm_classify_defaults_to_classify_model(monkeypatch):
    captured = {}
    monkeypatch.setattr(classification, "get_genai_client", lambda: object())
    monkeypatch.setattr(classification, "generate_with_retry", _stub_generate(captured))
    assert classification.llm_classify("Progress Report") == "1"
    assert captured["model"] == get_settings().classify_model


def test_llm_classify_honors_model_override(monkeypatch):
    captured = {}
    monkeypatch.setattr(classification, "get_genai_client", lambda: object())
    monkeypatch.setattr(classification, "generate_with_retry", _stub_generate(captured))
    classification.llm_classify("anything", model="gemini-2.5-flash")
    assert captured["model"] == "gemini-2.5-flash"
