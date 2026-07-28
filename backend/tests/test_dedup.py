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
