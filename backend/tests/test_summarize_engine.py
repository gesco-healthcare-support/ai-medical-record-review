"""Unit tests for summarize_row: the factuality-hardening preamble, the configured temperature,
and the faithfulness verify pass wiring.

Pure (no DB, no Vertex): _generate, the OCR call, and verify_summary are monkeypatched, so the
tests assert what summarize_row FEEDS the model and how it threads the verify result.
"""

import pytest

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services import summarize_engine as se

_NO_ISSUES = {"fixed_text": "", "issues": []}


@pytest.fixture(autouse=True)
def _stub_doi(monkeypatch):
    # Isolated DOI extraction hits a real PDF/Vertex; existing tests don't exercise it, so default
    # it to "-" (no DOI). DOI-specific tests below re-patch it.
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")


def _row(**over):
    row = {
        "start": 1,
        "end": 2,
        "category": "1",
        "date": "2026-01-01",
        "injury_date": "-",
        "flag": "",
    }
    row.update(over)
    return row


def _fake_generate(model, system_msg, user_text, temperature):
    return "Progress Note - Dr Smith" if system_msg == se.TITLE_PROMPT else "SUMMARY BODY"


def test_summary_call_uses_hardening_preamble_and_configured_temperature(monkeypatch):
    calls = []

    def fake_generate(model, system_msg, user_text, temperature):
        calls.append({"system_msg": system_msg, "temperature": temperature})
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "raw OCR text")
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

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
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    with pytest.raises(EmptyExtractionError):
        se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")


def test_verify_populates_verified_fields_when_issues_found(monkeypatch):
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "raw OCR text")
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature: (
            "Title - Dr" if system_msg == se.TITLE_PROMPT else "SUMMARY BODY with a fabrication"
        ),
    )
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary: {
            "fixed_text": "SUMMARY BODY",
            "issues": [{"type": "unsupported", "detail": "a fabrication"}],
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verified"] is True
    assert out["verifyIssues"] == [{"type": "unsupported", "detail": "a fabrication"}]
    assert "SUMMARY BODY" in out["verifiedText"]
    assert "fabrication" not in out["verifiedText"]
    # The raw model output stays the un-fixed body (immutable training data).
    assert "fabrication" in out["summaryText"]


def test_doi_prefix_from_isolated_extraction(monkeypatch):
    # WHEN extract_doi and the isolated extraction returns a date, THE SYSTEM SHALL prefix it.
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "OCR text")
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "09/25/2023")
    out = se.summarize_row("/x.pdf", _row(injury_date="-"), prompt="P", extract_doi=True)
    assert out["summaryText"].startswith("**DOI**:09/25/2023,")


def test_doi_prefix_omitted_when_isolated_returns_dash(monkeypatch):
    # WHEN the isolated extraction returns "-", THE SYSTEM SHALL omit the prefix even though the
    # (propagated) row value carries a date.
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "OCR text")
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    out = se.summarize_row("/x.pdf", _row(injury_date="05/08/2022"), prompt="P", extract_doi=True)
    assert "**DOI**" not in out["summaryText"]


def test_extract_doi_false_uses_row_value_without_calling(monkeypatch):
    # WHEN extract_doi is False, THE SYSTEM SHALL use row["injury_date"] and NOT call extraction.
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "OCR text")
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    called = []
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: called.append(1) or "99/99/9999")
    out = se.summarize_row("/x.pdf", _row(injury_date="05/07/2018"), prompt="P", extract_doi=False)
    assert out["summaryText"].startswith("**DOI**:05/07/2018,")
    assert called == []


def test_verify_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(se, "extract_text_from_selected_pages", lambda path, pages: "raw OCR text")
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature: (
            "Title - Dr" if system_msg == se.TITLE_PROMPT else "BODY"
        ),
    )
    called = []

    def _spy(*a, **k):
        called.append(1)
        return _NO_ISSUES

    monkeypatch.setattr(se, "verify_summary", _spy)

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=False)

    assert called == []  # verify must not run when explicitly disabled (bundle export path)
    assert out["verified"] is False
    assert out["verifiedText"] is None
    assert out["verifyIssues"] is None
