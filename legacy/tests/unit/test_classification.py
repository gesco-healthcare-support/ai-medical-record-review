"""Unit tests for the B5 classification cascade (rules, embedding, fusion)."""

import numpy as np
import pytest

from mrr_ai.services import classification as clf
from mrr_ai.services.classification import classify, match_rules


def _boom(*args, **kwargs):
    raise AssertionError("stage should not have been called")


# --- rules stage --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Primary Treating Physician's Progress Report (PR-2)", "1"),
        ("PR-4 Permanent and Stationary Report", "2"),
        ("MRI Lumbar Spine Without Contrast", "3"),
        ("GI Outpatient Procedure H&P", "4"),
        ("Initial Chiropractic Evaluation", "5"),
        ("Application for Adjudication of Claim", "7"),
        ("Operative Report", "8"),
        ("Deposition of John Doe", "9"),
        ("RFA (Request For Authorization)", "10"),
        ("Comprehensive Interval History Form", "11"),
        ("QME Supplemental Report", "12"),
        ("AME Report", "13"),
        ("Test Results", "14"),
    ],
)
def test_match_rules_unambiguous_types(title, expected):
    assert match_rules(title) == expected


def test_match_rules_returns_none_when_no_signal():
    assert match_rules("Patient Information Sheet") is None
    assert match_rules("") is None


# --- embedding stage (encoder mocked; no torch loaded) ------------------------------------


def test_embed_classify_picks_nearest_category(monkeypatch):
    ids = list(clf.CATEGORIES.keys())
    n = len(ids)
    target_idx = ids.index("9")

    def fake_encode(texts):
        if len(texts) == n:  # the category corpora -> orthonormal basis
            return np.eye(n)
        vec = np.zeros(n)  # the input -> points exactly at the target category
        vec[target_idx] = 1.0
        return np.array([vec])

    monkeypatch.setattr(clf, "_encode", fake_encode)
    monkeypatch.setattr(clf, "_category_matrix", None)
    monkeypatch.setattr(clf, "_category_ids", None)

    category, score = clf.embed_classify("anything")
    assert category == "9"
    assert score == pytest.approx(1.0)


# --- fusion ---------------------------------------------------------------------------------


def test_rules_short_circuit_skips_embedding_and_llm(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", _boom)
    monkeypatch.setattr(clf, "llm_classify", _boom)

    result = classify("Operative Report")
    assert (result.category, result.method, result.needs_review) == ("8", "rules", False)


def test_agreement_is_high_confidence(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", lambda text: ("3", 0.6))
    monkeypatch.setattr(clf, "llm_classify", lambda text: "3")

    result = classify("Quarterly Status Overview")  # no rule fires
    assert result.category == "3"
    assert result.needs_review is False
    assert result.method == "llm+embedding"


def test_disagreement_flags_for_review_and_trusts_llm(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", lambda text: ("5", 0.4))
    monkeypatch.setattr(clf, "llm_classify", lambda text: "1")

    result = classify("Quarterly Status Overview")
    assert result.category == "1"  # LLM wins on disagreement
    assert result.needs_review is True
    assert result.method == "llm-disagree"


def test_llm_unavailable_uses_embedding_and_flags(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", lambda text: ("8", 0.5))
    monkeypatch.setattr(clf, "llm_classify", lambda text: None)

    result = classify("Quarterly Status Overview")
    assert result.category == "8"
    assert result.needs_review is True
    assert result.method == "embedding-only"


def test_empty_text_defaults_to_100_and_flags(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", _boom)
    monkeypatch.setattr(clf, "llm_classify", _boom)

    result = classify("")
    assert result.category == "100"
    assert result.needs_review is True


def _raise(text):
    raise RuntimeError("stage failed")


def test_embedding_failure_falls_back_to_llm(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", _raise)
    monkeypatch.setattr(clf, "llm_classify", lambda text: "2")

    result = classify("Quarterly Status Overview")
    assert result.category == "2"
    assert result.needs_review is True
    assert result.method == "llm-only"


def test_both_stages_unavailable_defaults_to_100(monkeypatch):
    monkeypatch.setattr(clf, "embed_classify", _raise)
    monkeypatch.setattr(clf, "llm_classify", lambda text: None)

    result = classify("Quarterly Status Overview")
    assert result.category == "100"
    assert result.needs_review is True
    assert result.method == "no-signal"


# --- LLM stage (Gemini client mocked) -----------------------------------------------------


def _fake_genai(text):
    from types import SimpleNamespace

    def generate_content(**kwargs):
        if isinstance(text, Exception):
            raise text
        return SimpleNamespace(text=text)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def test_llm_classify_returns_valid_id(monkeypatch):
    monkeypatch.setattr(clf, "genai_client", _fake_genai("3"))
    assert clf.llm_classify("an MRI study report") == "3"


def test_llm_classify_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr(clf, "genai_client", _fake_genai("999"))
    assert clf.llm_classify("something") is None


def test_llm_classify_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(clf, "genai_client", _fake_genai(RuntimeError("gemini down")))
    assert clf.llm_classify("something") is None
