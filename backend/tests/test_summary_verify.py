"""Unit tests for the summary faithfulness verify pass (services.summary_verify).

Pure: the genai client + generate_with_retry are monkeypatched, so no Vertex call is made.
"""

import json

import pytest

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


def test_the_audit_enforces_house_style_not_only_faithfulness():
    """The six rules the editors asked for on 2026-07-30 are all FAITHFUL to the source, so the
    original prompt - which said "do NOT re-style a faithful sentence" and "do NOT drop content that
    IS supported" - forbade the audit from touching any of them. It now carries them explicitly, and
    still forbids restyling anything else."""
    prompt = sv.VERIFY_PROMPT
    assert "HOUSE RULES" in prompt
    for operative in (
        "HEIGHT AND WEIGHT",
        "PAIN",
        "CAPITALISATION",
        "RANGE OF MOTION",
        "DUPLICATION",
        "PREVIOUS VISITS",
    ):
        assert operative in prompt, operative
    # The licence to restyle must stay bounded to those rules, or the pass starts rewriting good
    # prose and the raw/verified distinction stops meaning anything.
    assert "the one reason you may edit a sentence that is perfectly faithful" in prompt
    assert "do NOT re-style a sentence that breaks neither" in prompt
    # The title is an all-capitals header by design (812 of 813 measured human entries), so the
    # capitalisation rule must exempt it or the audit would "fix" every title.
    assert "The TITLE is exempt" in prompt
    # Rule 1 is height and weight only; the audit must not strip vitals Adrian has not ruled on.
    assert "Those two ONLY" in prompt
    assert "never remove a BMI" in prompt


def test_every_house_rule_has_its_own_issue_type():
    # Stored issues are the only way to measure whether a rule fires, so a rule without its own type
    # is a rule nobody can audit. Kept in step with the six HOUSE RULES above.
    types = set(_RESPONSE_SCHEMA_ISSUE_TYPES())
    assert {
        "unsupported",
        "contradiction",
        "date",
        "laterality",
    } <= types  # faithfulness, unchanged
    assert {
        "vitals",
        "pain_descriptor",
        "capitalization",
        "range_of_motion",
        "duplicate_finding",
        "prior_visit",
    } <= types


def _RESPONSE_SCHEMA_ISSUE_TYPES():
    return sv._RESPONSE_SCHEMA["properties"]["issues"]["items"]["properties"]["type"]["enum"]


def test_the_document_date_reaches_the_model_when_given(monkeypatch):
    # Rule 6 cannot be checked without it: the source names several dates and only the caller knows
    # which one is this sub-document's.
    seen = {}

    def gen(client, *, model, contents, config):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", document_date="03/09/2023")
    assert "THIS DOCUMENT'S DATE:\n03/09/2023" in seen["contents"]


@pytest.mark.parametrize("date", [None, "", "   ", "-"])
def test_a_missing_document_date_is_omitted_rather_than_asserted(monkeypatch, date):
    # Segmentation could not read a date -> rule 6 is skipped. Sending "-" would invite the model to
    # treat everything as a prior visit.
    seen = {}

    def gen(client, *, model, contents, config):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(sv, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", document_date=date)
    assert "THIS DOCUMENT'S DATE" not in seen["contents"]


def test_blank_fixed_title_falls_back_to_the_original(monkeypatch):
    monkeypatch.setattr(sv, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        sv,
        "generate_with_retry",
        _fake_gen({"fixed_text": "Body.", "fixed_title": "   ", "issues": []}),
    )
    result = sv.verify_summary("m", "src", "Body.", title="KEEP THIS TITLE")
    assert result["fixed_title"] == "KEEP THIS TITLE"
