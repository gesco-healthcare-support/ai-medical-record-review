"""Unit tests for summarize_row: the factuality-hardening preamble and the configured temperature.

Pure (no DB, no Vertex): _generate and the OCR call are monkeypatched, so the test asserts what
summarize_row FEEDS the model - that the summary call gets HARDENING_PREAMBLE + the category prompt
at the configured summary_temperature, while the title call stays pure extraction at 0.0.
"""

import pytest

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services import summarize_engine as se


def _row(**over):
    row = {"start": 1, "end": 2, "category": "1", "date": "2026-01-01", "injury_date": "-", "flag": ""}
    row.update(over)
    return row


def test_summary_call_uses_hardening_preamble_and_configured_temperature(monkeypatch):
    calls = []

    def fake_generate(model, system_msg, user_text, temperature):
        calls.append({"system_msg": system_msg, "temperature": temperature})
        return "Progress Note - Dr Smith" if system_msg == se.TITLE_PROMPT else "SUMMARY BODY"

    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "raw OCR text")
    monkeypatch.setattr(se, "_generate", fake_generate)

    out = se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")

    summary_call = next(c for c in calls if c["system_msg"] != se.TITLE_PROMPT)
    title_call = next(c for c in calls if c["system_msg"] == se.TITLE_PROMPT)
    assert summary_call["system_msg"] == se.HARDENING_PREAMBLE + "CATEGORY PROMPT"
    assert summary_call["temperature"] == get_settings().summary_temperature
    assert title_call["temperature"] == 0.0  # title is pure extraction, always deterministic
    assert "SUMMARY BODY" in out["summaryText"]
    assert out["sourceText"] == "raw OCR text"


def test_empty_ocr_fails_fast(monkeypatch):
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "   ")
    monkeypatch.setattr(se, "_generate", lambda *a, **k: "unused")
    with pytest.raises(EmptyExtractionError):
        se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")
