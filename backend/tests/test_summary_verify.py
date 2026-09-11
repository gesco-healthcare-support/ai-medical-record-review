"""Unit tests for the summary faithfulness verify pass (services.summary_verify).

Pure: the Gemini provider's client + retry seam are monkeypatched, so no Vertex call is made.
They are patched on services.llm.gemini rather than on this module, because summary_verify now
goes through the provider abstraction - so these tests also cover that translation.
"""

import json
import logging

import pytest

from app.config import get_settings
from app.services import summary_verify as sv
from app.services.llm import gemini as gm


class _Resp:
    def __init__(self, text):
        self.text = text


def _fake_gen(payload):
    def gen(client, *, model, contents, config, **_kw):
        return _Resp(json.dumps(payload))

    return gen


def test_flags_and_fixes_unsupported(monkeypatch):
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm,
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
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm, "generate_with_retry", _fake_gen({"fixed_text": "All supported.", "issues": []})
    )
    result = sv.verify_summary("m", "src", "All supported.")
    assert result["issues"] == []
    assert result["fixed_text"] == "All supported."
    assert result["ok"] is True  # the audit ran and its reply parsed


def test_blank_summary_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(gm, "get_genai_client", lambda: called.append(1))
    monkeypatch.setattr(gm, "generate_with_retry", _fake_gen({"fixed_text": "x", "issues": []}))
    result = sv.verify_summary("m", "src", "   ", title="A TITLE")
    # ok is False, not True: no model was called, so nothing was verified. A degenerate row must not
    # end up claiming a check that never happened.
    assert result == {
        "fixed_text": "   ",
        "fixed_title": "A TITLE",
        "issues": [],
        "ok": False,
        # Still exact equality, deliberately. These two assertions pin the WHOLE fail-safe shape,
        # which is the point of them - loosening to a subset match to accommodate new keys would
        # throw away the guarantee. The token fields joined that shape on 2026-09-10.
        #
        # None, not 0: no model was ever called here, so there is no usage to report. Zero tokens
        # would mean the provider answered and spent nothing, which is a different event.
        "truncated": False,
        "input_tokens": None,
        "output_tokens": None,
    }
    assert called == []  # no model call for an empty summary


def test_model_failure_returns_original(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", boom)
    result = sv.verify_summary("m", "src", "Original summary.", title="ORIGINAL TITLE")
    # Fail-safe covers the title too: a broken check must never blank a good header.
    assert result == {
        "fixed_text": "Original summary.",
        "fixed_title": "ORIGINAL TITLE",
        "issues": [],
        "ok": False,
        # The call RAISED, so no response object exists to read usage from - None rather than 0,
        # and truncated False because nothing came back to be truncated. That distinction is the
        # reason these fields were added: `ok` False alone folds "nothing to audit", "the reply hit
        # the cap" and "something threw" into one value, and the benchmark could not tell them
        # apart on the stage that turned out to waste 93 percent of its model time.
        "truncated": False,
        "input_tokens": None,
        "output_tokens": None,
    }


def test_blank_fixed_text_keeps_original(monkeypatch):
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", _fake_gen({"fixed_text": "  ", "issues": []}))
    result = sv.verify_summary("m", "src", "Keep me.")
    assert result["fixed_text"] == "Keep me."
    # ok stays True: the audit ran and returned valid structured output, it simply had nothing
    # usable to offer. That is a different event from the audit failing, and conflating the two is
    # what made this invisible in the first place.
    assert result["ok"] is True


class _TruncatedResp:
    """A reply cut off at the token cap: a valid JSON prefix, no closing brace, and the MAX_TOKENS
    finish reason the provider reads to set `truncated`."""

    def __init__(self, text):
        self.text = text
        self.candidates = [type("_C", (), {"finish_reason": "MAX_TOKENS"})()]


def test_a_truncated_reply_is_not_a_verification(monkeypatch):
    """WHEN the audit reply hits the token cap, THE SYSTEM SHALL keep the original AND report that
    no verification happened.

    This is the observed production failure: dynamic thinking is billed against the same
    max_output_tokens as the reply, so on a hard row the reasoning consumes the budget and the JSON
    arrives cut off - `Unterminated string starting at: line 2 column 17 (char 18)` is a reply that
    died right after the opening quote of fixed_text.
    """
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm,
        "generate_with_retry",
        lambda client, **_kw: _TruncatedResp('{\n  "fixed_text": "The claimant rep'),
    )
    result = sv.verify_summary("m", "src", "Original summary.", title="ORIGINAL TITLE")
    assert result["ok"] is False
    assert result["fixed_text"] == "Original summary."  # fail-safe still returns the original
    assert result["fixed_title"] == "ORIGINAL TITLE"
    assert result["issues"] == []


def test_title_is_audited_and_corrected(monkeypatch):
    # WHEN the pass finds a laterality error in the title, THE SYSTEM SHALL return a corrected title
    # alongside the corrected body, and name the issue type.
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm,
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

    def gen(client, *, model, contents, config, **_kw):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", title="A TITLE")
    assert "TITLE:\nA TITLE" in "".join(seen["contents"])
    assert "SOURCE:\nthe source" in "".join(seen["contents"])


def test_the_call_sets_its_own_thinking_budget(monkeypatch):
    # REGRESSION: the retry seam applies thinking_budget=0 to any call that does not set one, and
    # summary_model (2.5-pro) rejects 0 with a 400. Because this module is fail-safe, that 400 was
    # swallowed and every verify silently returned the original summary. The call must therefore
    # carry its own thinking_config.
    seen = {}

    def gen(client, *, model, contents, config, **_kw):
        seen["thinking"] = config.thinking_config
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", gen)
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

    def gen(client, *, model, contents, config, **_kw):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", document_date="03/09/2023")
    assert "THIS DOCUMENT'S DATE:\n03/09/2023" in "".join(seen["contents"])


@pytest.mark.parametrize("date", [None, "", "   ", "-"])
def test_a_missing_document_date_is_omitted_rather_than_asserted(monkeypatch, date):
    # Segmentation could not read a date -> rule 6 is skipped. Sending "-" would invite the model to
    # treat everything as a prior visit.
    seen = {}

    def gen(client, *, model, contents, config, **_kw):
        seen["contents"] = contents
        return _Resp(json.dumps({"fixed_text": "Body.", "issues": []}))

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", gen)
    sv.verify_summary("m", "the source", "Body.", document_date=date)
    assert "THIS DOCUMENT'S DATE" not in "".join(seen["contents"])


def test_blank_fixed_title_falls_back_to_the_original(monkeypatch):
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm,
        "generate_with_retry",
        _fake_gen({"fixed_text": "Body.", "fixed_title": "   ", "issues": []}),
    )
    result = sv.verify_summary("m", "src", "Body.", title="KEEP THIS TITLE")
    assert result["fixed_title"] == "KEEP THIS TITLE"


# --- the audit's own output cap: reachable from the app, and reported honestly -------------------
#
# `verify_summary` has taken a `max_output_tokens` override since #285 so a caller that knows its
# own reply distribution can bound what a runaway costs. Two halves of that were unfinished: the
# truncation warning still read the SETTING rather than the override actually in force, and the
# app's one production caller had no setting to pass, so it could not opt in at all.


def _cap_seen(monkeypatch, **kwargs):
    """Run one audit and return the max_output_tokens the provider was actually called with."""
    seen = {}

    def gen(client, *, model, contents, config, **_kw):
        seen["cap"] = config.max_output_tokens
        return _Resp(json.dumps({"fixed_text": "Body.", "fixed_title": "T", "issues": []}))

    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(gm, "generate_with_retry", gen)
    sv.verify_summary("m", "src", "Body.", title="T", **kwargs)
    return seen["cap"]


def test_the_audit_shares_the_body_budget_when_nothing_overrides_it(monkeypatch):
    """WHEN neither the argument nor the setting is given, THE SYSTEM SHALL use the body's budget.

    A GUARD, not a demonstration: it passes on main too. It is here because leaving the default
    alone EXACTLY is the property that lets this knob ship dark - if the resolution ever started
    landing somewhere else, every deployed box would change audit behaviour on upgrade with nothing
    said.
    """
    get_settings.cache_clear()
    monkeypatch.delenv("AUDIT_MAX_OUTPUT_TOKENS", raising=False)
    try:
        assert _cap_seen(monkeypatch) == get_settings().summary_max_output_tokens
    finally:
        get_settings.cache_clear()


def test_an_override_is_the_cap_the_audit_actually_runs_under(monkeypatch):
    """WHEN a caller passes an override, THE SYSTEM SHALL call the model with it.

    A GUARD: this is #285's behaviour and it passes on main. It is worth pinning anyway because the
    resolution below it grew two more tiers, and the argument staying authoritative through that is
    the property the benchmark harness depends on.
    """
    assert _cap_seen(monkeypatch, max_output_tokens=2048) == 2048


def test_the_audit_cap_setting_is_reachable_without_a_code_change(monkeypatch):
    """WHEN `audit_max_output_tokens` is set, THE SYSTEM SHALL run the audit under it.

    `verify_summary` has taken the argument since #285 and the benchmark harness passes one, but the
    app's single production caller passed nothing and there was no setting to pass - so on a
    deployed box the knob existed and could not be reached. Which way the cap should move is an open
    question (see config.py); this pins the wiring, not a policy.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("AUDIT_MAX_OUTPUT_TOKENS", "2048")
    try:
        assert _cap_seen(monkeypatch) == 2048
    finally:
        get_settings.cache_clear()


def test_an_explicit_argument_still_beats_the_setting(monkeypatch):
    """WHEN both are given, THE SYSTEM SHALL prefer the argument.

    The order matters and is not arbitrary: the setting is an operator's decision for the whole
    deployment, the argument is a caller that knows its own reply distribution. A caller that has
    measured itself is the narrower claim, so it wins - and the benchmark harness, which is exactly
    such a caller, keeps behaving the same whatever a box sets.

    Passes on main, but only VACUOUSLY - there is no setting there for the argument to beat, so the
    assertion is not exercising a precedence rule until this change exists. Read it as a guard over
    the new middle tier rather than as a demonstration.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("AUDIT_MAX_OUTPUT_TOKENS", "2048")
    try:
        assert _cap_seen(monkeypatch, max_output_tokens=777) == 777
    finally:
        get_settings.cache_clear()


def test_a_truncated_audit_names_the_cap_that_was_in_force(monkeypatch, caplog):
    """WHEN an overridden audit hits its cap, THE SYSTEM SHALL name THAT cap, not the setting.

    The warning read `get_settings().summary_max_output_tokens` while the call used the override, so
    the two disagreed for exactly the callers that opt in. The benchmark harness passes an override
    on every audit, which put the wrong number in the log in the one place audit truncation was
    being diagnosed - a diagnostic that contradicts the run it is describing is worse than none.

    2048 is deliberately not the default here, so a warning that still quoted the setting would
    print 8192 and fail on the value rather than on the wording.
    """
    monkeypatch.setattr(gm, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        gm,
        "generate_with_retry",
        lambda client, **_kw: _TruncatedResp('{\n  "fixed_text": "The claimant rep'),
    )
    with caplog.at_level(logging.WARNING):
        result = sv.verify_summary("m", "src", "Body.", title="T", max_output_tokens=2048)

    assert result["ok"] is False  # still fail-safe
    warning = "".join(r.getMessage() for r in caplog.records)
    assert "2048-token cap" in warning
    assert str(get_settings().summary_max_output_tokens) not in warning
