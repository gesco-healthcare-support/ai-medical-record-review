"""Unit tests for the Gemini summarize engine (model + OCR mocked; decorations under test)."""

from types import SimpleNamespace

import pytest

from mrr_ai.errors import EmptyExtractionError
from mrr_ai.services import summarize_engine
from mrr_ai.services.summarize_engine import summarize_row


def _wire(monkeypatch, replies):
    """Stub OCR and the Gemini call; capture the system prompts used."""
    calls = []

    def fake_generate(client, model, contents, config):
        calls.append(dict(model=model, system=config.system_instruction))
        return SimpleNamespace(text=replies[len(calls) - 1])

    monkeypatch.setattr(summarize_engine, "generate_with_retry", fake_generate)
    monkeypatch.setattr(
        summarize_engine, "extract_text_from_selected_pages", lambda path, pages: "page text"
    )
    return calls


def _row(**overrides):
    row = dict(start=3, end=5, category="3", date="02/01/2024", injury_date="05/07/2018", flag="x")
    row.update(overrides)
    return row


def test_decorations_match_legacy_shape(monkeypatch):
    _wire(monkeypatch, ["the summary", "MRI Report - Dr Scan"])
    out = summarize_row("case.pdf", _row(), model="gemini-2.5-flash")
    assert (
        out["summaryTitle"] == "[ManualCheck] MRI Report - Dr Scan [Diagnostic Study] (Pages 3-5)"
    )
    assert out["summaryText"] == "**DOI**:05/07/2018, the summary"
    assert out["summaryDate"] == "02/01/2024"


def test_category_prompt_selected_and_11_falls_back(monkeypatch):
    calls = _wire(monkeypatch, ["s", "t", "s", "t"])
    summarize_row("case.pdf", _row(category="9", injury_date="-", flag="-"))
    from mrr_ai.prompts import prompts

    assert calls[0]["system"] == prompts["category_09"]
    # category 11 has no prompt in prompts.py - must fall back, never KeyError
    summarize_row("case.pdf", _row(category="11", injury_date="-", flag="-"))
    assert calls[2]["system"] == prompts["category_100"]


def test_clean_row_has_no_decorations(monkeypatch):
    _wire(monkeypatch, ["s", "Progress Note - Dr A"])
    out = summarize_row("case.pdf", _row(category="1", injury_date="-", flag="-"))
    assert out["summaryTitle"] == "Progress Note - Dr A (Pages 3-5)"
    assert out["summaryText"] == " s"
    assert out["manualCheck"] == ""


def test_empty_extraction_raises_before_calling_the_model(monkeypatch):
    calls = _wire(monkeypatch, ["s", "t"])
    # OCR yields only whitespace: summarization must fail fast, not send empty input to Gemini.
    monkeypatch.setattr(summarize_engine, "extract_text_from_selected_pages", lambda p, pg: "  \n ")
    with pytest.raises(EmptyExtractionError):
        summarize_row("case.pdf", _row())
    assert calls == []  # the model was never called
