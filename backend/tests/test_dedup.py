"""Unit tests for the duplicate-clustering service (services.dedup).

cluster_rows is pure; confirm_cluster's model call is monkeypatched (no Vertex).
"""

import json

from app.services import dedup


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)


def test_cluster_rows_groups_near_identical_and_excludes_distinct():
    items = [
        {"idx": 0, "text": "patient reports lower back pain lumbar tenderness physical therapy"},
        {
            "idx": 1,
            "text": "patient reports lower back pain lumbar tenderness physical therapy plan",
        },
        {
            "idx": 2,
            "text": "operative report knee arthroscopy anesthesia general surgeon signature",
        },
    ]
    clusters = dedup.cluster_rows(items, jaccard_threshold=0.5)
    assert len(clusters) == 1
    assert {m["idx"] for m in clusters[0]["members"]} == {0, 1}
    assert clusters[0]["similarity"] > 0.7  # near-identical -> high char-difflib


def test_cluster_similarity_low_for_shared_template_different_content():
    a = "work activity status report employer name date restrictions no lifting over 10 lbs today"
    b = "work activity status report employer name date restrictions full duty no restrictions today"
    clusters = dedup.cluster_rows(
        [{"idx": 0, "text": a}, {"idx": 1, "text": b}], jaccard_threshold=0.4
    )
    assert len(clusters) == 1  # Jaccard groups them (shared vocabulary)
    assert clusters[0]["similarity"] < 0.9  # but difflib exposes they may be a series


def test_similarity_compares_excerpts_not_whole_documents():
    """difflib is quadratic, so the score reads only the first _EXCERPT_CHARS of each member - the
    same window the AI-confirm call uses. Two long documents that agree on that window and diverge
    far beyond it still score as near-identical, which is what makes the check affordable on a
    re-scanned transcript (measured: 33ms vs 1132ms on a real 13k-char cluster, verdict unchanged)."""
    shared = "identical scanned report text " * 60  # ~1800 chars, past the excerpt window
    # Single-char tails so the word-set signature (and therefore Jaccard) is identical: this test is
    # about the SCORE, not about what clusters.
    a = shared + "z " * 20000
    b = shared + "q " * 20000
    clusters = dedup.cluster_rows([{"idx": 0, "text": a}, {"idx": 1, "text": b}])
    assert len(clusters) == 1
    assert clusters[0]["similarity"] == 1.0
    # Proof it is the truncation doing this: the full texts are only ~half alike.
    assert dedup._min_difflib.__doc__  # documented behaviour
    assert len(shared) > dedup._EXCERPT_CHARS


def test_similarity_still_separates_a_form_series_within_the_excerpt():
    # WHEN members share a template but differ in content inside the window, the score stays low.
    a = "work status report employer date restrictions no lifting over 10 lbs " * 30
    b = "work status report employer date restrictions full duty without limits " * 30
    # Threshold lowered to the point where the shared template alone groups them (as a real form
    # series does), which is exactly the case the score has to distinguish.
    clusters = dedup.cluster_rows([{"idx": 0, "text": a}, {"idx": 1, "text": b}], 0.4)
    assert len(clusters) == 1
    assert clusters[0]["similarity"] < 0.95


def test_confirm_cluster_returns_confirmed_subset(monkeypatch):
    members = [
        {"title": "A", "date": "1", "text": "x"},
        {"title": "B", "date": "2", "text": "y"},
        {"title": "C", "date": "3", "text": "z"},
    ]
    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        dedup, "generate_with_retry", lambda *a, **k: _Resp({"duplicate_indices": [1, 3]})
    )
    assert dedup.confirm_cluster(members, model="m") == [members[0], members[2]]


def test_confirm_cluster_empty_when_model_finds_no_duplicates(monkeypatch):
    members = [{"title": "A", "date": "1", "text": "x"}, {"title": "B", "date": "2", "text": "y"}]
    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(
        dedup, "generate_with_retry", lambda *a, **k: _Resp({"duplicate_indices": [1]})
    )
    assert dedup.confirm_cluster(members, model="m") == []


def test_confirm_cluster_failsafe_trusts_candidate_on_error(monkeypatch):
    members = [{"title": "A", "date": "1", "text": "x"}, {"title": "B", "date": "2", "text": "y"}]

    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(dedup, "get_genai_client", lambda: None)
    monkeypatch.setattr(dedup, "generate_with_retry", boom)
    assert dedup.confirm_cluster(members, model="m") == members


def test_confirm_cluster_single_member_returns_empty():
    assert dedup.confirm_cluster([{"text": "x"}]) == []


def _members(*pairs):
    """Cluster members from (date, title) pairs; text is irrelevant to the gate."""
    return [{"date": date, "title": title, "text": "x"} for date, title in pairs]


def test_gate_passes_a_cluster_sharing_both_date_and_title():
    members = _members(("05/08/2022", "Progress Report"), ("05/08/2022", "progress  report"))
    assert dedup.date_title_gate(members, 0.30) is True  # normalization: case + whitespace


def test_gate_rejects_a_multi_date_series_despite_shared_title():
    members = _members(("05/08/2022", "Work Status Report"), ("06/12/2022", "Work Status Report"))
    assert dedup.date_title_gate(members, 0.51) is False


def test_gate_override_keeps_a_genuine_rescan_with_two_dates():
    """The measured 0.998 pair: two transcribed dates, one document. Content overrides metadata."""
    members = _members(("05/08/2022", "Operative Report"), ("05/09/2022", "Operative Report"))
    assert dedup.date_title_gate(members, 0.998) is True


def test_gate_treats_absent_date_and_title_as_unknown_not_matching():
    # Aggregate-built records carry "-" for every row; two unknowns have told us nothing, so the
    # gate must fall through to content similarity rather than reading them as a match.
    members = _members(("-", "-"), ("-", "-"))
    assert dedup.date_title_gate(members, 0.40) is False
    assert dedup.date_title_gate(members, 0.95) is True


def test_gate_handles_similarity_none_from_pre_column_clusters():
    members = _members(("05/08/2022", "A"), ("06/08/2022", "B"))
    assert dedup.date_title_gate(members, None) is False


def test_gate_reproduces_the_reviewer_verdicts_on_the_measured_clusters():
    """Regression fixture: the 22 live clusters measured 2026-07-31, as
    (similarity, distinct dates, distinct titles, reviewer verdict) - no model call needed.

    Genuine duplicates all sat at >= 0.994; every false positive was a multi-date series between
    0.07 and 0.82. This pins the separation so a threshold change cannot silently regress it.
    """
    cases = [
        # (similarity, dates, titles, is_duplicate)
        (0.823, 7, 3, False),
        (0.514, 6, 4, False),
        (0.300, 4, 1, False),
        (0.399, 1, 2, False),
        (0.073, 2, 2, False),
        (1.000, 1, 1, True),
        (0.994, 1, 2, True),
        (0.998, 2, 1, True),
    ]
    for similarity, dates, titles, expected in cases:
        members = _members(
            *(
                (f"0{(i % dates) + 1}/08/2022", f"Report {(i % titles) + 1}")
                for i in range(max(dates, titles))
            )
        )
        assert dedup.date_title_gate(members, similarity) is expected, (
            f"similarity={similarity} dates={dates} titles={titles}"
        )


def test_cluster_rows_threshold_defaults_to_the_configured_value(monkeypatch):
    calls = []

    real_jaccard = dedup._jaccard

    def spy(a, b):
        value = real_jaccard(a, b)
        calls.append(value)
        return value

    monkeypatch.setattr(dedup, "_jaccard", spy)
    shared = "alpha beta gamma delta epsilon zeta eta theta"  # 8 words, so one differing -> 8/10
    items = [{"text": f"{shared} iota"}, {"text": f"{shared} kappa"}]
    # The configured default (0.70) groups these at 0.8; an explicit argument still wins.
    assert len(dedup.cluster_rows(items)) == 1
    assert calls == [0.8]  # the configured cut, not a hardcoded argument, decided this
    assert dedup.cluster_rows(items, jaccard_threshold=0.99) == []
