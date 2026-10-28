"""One visit's pieces merged into one draft row before review (`visit_merge`, MERGE_VISIT_PIECES).

The reviewers' answer of 2026-10-28: "for visits you can always merge them if it's from the same
doctor ... if there is an RFA (category 10) or a Work Status from the same doctor and the same date,
you can also merge them with the PR-2/Visit ... Diagnostics are always separate."

Synthetic rows only. The model is a stub, and so is the page renderer, so no PDF is read.
"""

import logging
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import visit_merge
from app.services.llm import ImagePart, TextPart
from app.worker.failures import JobCancelled

_DATE = "02/04/2026"


def _row(start, end, category="1", title="PR-2 PROGRESS REPORT", date=_DATE, flag="-", **over):
    return {
        "start": start,
        "end": end,
        "category": category,
        "title": title,
        "date": date,
        "injury_date": "-",
        "flag": flag,
        "method": "rule",
        **over,
    }


_PR2 = _row(1, 2)
_PR2_PAGE = _row(3, 3)
_RFA = _row(3, 3, "10", "REQUEST FOR AUTHORIZATION")
_ORDERS = _row(3, 3, "10", "ORDERS")
_SLIP_100 = _row(3, 3, "100", "WORK STATUS REPORT")
_THERAPY = _row(1, 1, "5", "CHIROPRACTIC TREATMENT NOTE")
_THERAPY_2 = _row(2, 2, "5", "CHIROPRACTIC TREATMENT NOTE")
_STUDY = _row(3, 3, "3", "X-RAY OF THE LUMBAR SPINE")
_LAB = _row(3, 3, "14", "LABORATORY REPORT")


@pytest.mark.parametrize(
    ("prev", "row"),
    [
        (_PR2, _PR2_PAGE),  # one PR-2 cut in two
        (_THERAPY, _THERAPY_2),  # one therapy visit cut in two
        (_PR2, _RFA),  # the visit's RFA
        (_PR2, _SLIP_100),  # the visit's work status, whatever its category
        (_row(1, 1, "10", "REQUEST FOR AUTHORIZATION"), _row(2, 3)),  # the RFA first
        (_THERAPY, _row(2, 2, "1", "WORK STATUS")),  # a work status filed 1 joins a therapy visit
    ],
    ids=["pr2+pr2", "5+5", "pr2+rfa", "pr2+work status", "rfa+pr2", "5+work status"],
)
def test_a_same_date_visit_pair_is_a_candidate(prev, row):
    """DEMONSTRATES which boundaries are asked about: two visit documents of one category, or a
    visit and the RFA or work status that joins it, on one date."""
    assert visit_merge.candidate_indices([prev, row]) == [1]


@pytest.mark.parametrize(
    ("prev", "row"),
    [
        (_PR2, _STUDY),  # "Diagnostics are always separate"
        (_row(1, 1, "3", "MRI"), _row(2, 2, "3", "MRI")),  # two studies
        (_row(1, 1, "3", "WORK STATUS"), _PR2_PAGE),  # a study never joins, whatever its title
        (_PR2, _row(3, 3, date="03/11/2026")),  # another date
        (_row(1, 2, date="-"), _row(3, 3, date="-")),  # no date to share
        (_PR2, _ORDERS),  # orders share category 10 but were not in the answer
        (_PR2, _LAB),  # a lab is folded by date at export, not here
        (_PR2, _row(3, 3, "5", "ACUPUNCTURE NOTE")),  # category 1 beside category 5: not measured
        (_row(1, 1, "100", "WORK STATUS"), _SLIP_100),  # no visit in the pair
        (_row(1, 1, "10", "REQUEST FOR AUTHORIZATION"), _RFA),  # RFAs alone are not a visit
    ],
    ids=[
        "visit+study",
        "study+study",
        "study titled work status",
        "another date",
        "undated",
        "orders",
        "lab",
        "1+5",
        "slips alone",
        "rfas alone",
    ],
)
def test_a_pair_that_is_not_one_visit_is_never_asked_about(prev, row):
    """GUARD: no call, so no merge, for a pair the answer does not cover."""
    assert visit_merge.candidate_indices([prev, row]) == []


def _answering(*starts):
    """A `_same_author` stand-in that says YES for exactly the rows starting at ``starts``."""
    asked = []

    def _same(_pdf, _prev, row):
        asked.append(row["start"])
        return row["start"] in starts

    return _same, asked


def test_a_confirmed_pair_becomes_one_row_with_the_visits_identity(monkeypatch):
    """DEMONSTRATES the merge: the visit's category, title and classification, the first piece's
    start, the second's end, and a review flag carried over."""
    same, _ = _answering(3)
    monkeypatch.setattr(visit_merge, "_same_author", same)
    rows = [_PR2, _row(3, 3, "100", "WORK STATUS REPORT", flag="x", method="llm-disagree")]

    out, stats = visit_merge.merge_visit_pieces("/x.pdf", rows)

    assert stats == {"candidates": 1, "merged": 1}
    assert len(out) == 1
    merged = out[0]
    assert (merged["start"], merged["end"]) == (1, 3)
    assert (merged["category"], merged["title"], merged["method"]) == ("1", _PR2["title"], "rule")
    assert merged["flag"] == "x"


def test_when_the_rfa_comes_first_the_visit_still_gives_the_row_its_identity(monkeypatch):
    """DEMONSTRATES: the row starts where the RFA starts, but is the PR-2."""
    same, _ = _answering(2)
    monkeypatch.setattr(visit_merge, "_same_author", same)
    rows = [_row(1, 1, "10", "REQUEST FOR AUTHORIZATION", suggest_merge=True), _row(2, 3)]

    out, _ = visit_merge.merge_visit_pieces("/x.pdf", rows)

    assert [(r["start"], r["end"], r["category"], r["title"]) for r in out] == [
        (1, 3, "1", "PR-2 PROGRESS REPORT")
    ]
    # The first piece's hint is about the boundary ABOVE the merged row, so it is the one kept.
    assert out[0]["suggest_merge"] is True


def test_a_run_of_confirmed_pieces_collapses_into_one_row_and_the_rest_stay(monkeypatch):
    """DEMONSTRATES a PR-2 in two pieces plus its RFA, then an unrelated study, kept apart."""
    same, asked = _answering(3, 4)
    monkeypatch.setattr(visit_merge, "_same_author", same)
    rows = [_PR2, _PR2_PAGE, _row(4, 4, "10", "REQUEST FOR AUTHORIZATION"), _row(5, 6, "3", "MRI")]

    out, stats = visit_merge.merge_visit_pieces("/x.pdf", rows)

    assert sorted(asked) == [3, 4]  # the study is never asked about
    assert [(r["start"], r["end"], r["category"]) for r in out] == [(1, 4, "1"), (5, 6, "3")]
    assert stats == {"candidates": 2, "merged": 2}
    assert out[-1]["end"] == rows[-1]["end"]  # still a tiling of the record


def test_a_no_keeps_the_boundary(monkeypatch):
    """GUARD: a candidate the model does not confirm keeps the split segmentation drew."""
    same, asked = _answering()
    monkeypatch.setattr(visit_merge, "_same_author", same)

    out, stats = visit_merge.merge_visit_pieces("/x.pdf", [_PR2, _RFA])

    assert asked == [3]
    assert [(r["start"], r["end"]) for r in out] == [(1, 2), (3, 3)]
    assert stats == {"candidates": 1, "merged": 0}


def test_an_unanswered_check_keeps_the_boundary(monkeypatch):
    """GUARD: a check the pool never finished merges nothing - the pool timeout path."""
    from app.services.pools import PoolTimeout

    def _timed_out(futures, _timeout):
        raise PoolTimeout(list(futures))
        yield  # pragma: no cover - makes this a generator, as drain_pool is

    monkeypatch.setattr(visit_merge, "drain_pool", _timed_out)
    monkeypatch.setattr(visit_merge, "_same_author", lambda *_a: True)

    out, stats = visit_merge.merge_visit_pieces("/x.pdf", [_PR2, _RFA], pool_timeout=1)

    assert len(out) == 2
    assert stats == {"candidates": 1, "merged": 0}


def test_the_log_names_page_ranges_and_counts_never_a_title(monkeypatch, caplog):
    """GUARD on the patient-data rule: counts and page ranges only."""
    same, _ = _answering(3)
    monkeypatch.setattr(visit_merge, "_same_author", same)
    secret = "JANE ROE, N.P. VALLEY CLINIC. PR-2"
    with caplog.at_level(logging.INFO, logger="app.services.visit_merge"):
        visit_merge.merge_visit_pieces("/x.pdf", [_row(1, 2, title=secret), _RFA])

    assert "pages 3-3 joined pages 1-2" in caplog.text
    assert "JANE ROE" not in caplog.text


def test_progress_is_reported_as_boundary_checks(monkeypatch):
    same, _ = _answering(3)
    monkeypatch.setattr(visit_merge, "_same_author", same)
    calls = []

    visit_merge.merge_visit_pieces("/x.pdf", [_PR2, _RFA], progress=lambda *a: calls.append(a))

    assert calls == [("verifying", 0, 1), ("verifying", 1, 1)]


# --- the call itself -------------------------------------------------------------------------


def _stub_provider(monkeypatch, reply="YES", error=None):
    """Route `visit_merge.provider_for_stage` to a stub that records the stage and the kwargs, and
    render each page as a fake image part naming its page."""
    seen = {}

    class _Provider:
        def generate_choice(self, **kwargs):
            seen.update(kwargs)
            if error is not None:
                raise error
            return SimpleNamespace(text=reply)

    def _resolver(stage, *_a, **_k):
        seen["resolved"] = stage
        return _Provider()

    def _pages(_pdf, start, end, max_pages, *_a, **_k):
        assert (start, end, max_pages) == (start, start, 1)
        return [ImagePart(data=f"page-{start}".encode(), mime_type="image/jpeg")]

    monkeypatch.setattr(visit_merge, "provider_for_stage", _resolver)
    monkeypatch.setattr(visit_merge, "page_image_parts", _pages)
    return seen


def test_the_check_asks_the_verify_stage_with_each_rows_edge_pages(monkeypatch):
    """DEMONSTRATES the request: through the seam on the `verify` stage and its model, the first and
    last page of each row as images first, the same-author question last, YES or NO only."""
    seen = _stub_provider(monkeypatch)

    assert visit_merge._same_author("/x.pdf", _row(1, 4), _row(5, 5, "10", "RFA")) is True

    assert seen["resolved"] == "verify"
    assert seen["stage"] == "verify"
    assert seen["model"] == get_settings().model_for_stage("verify")
    assert seen["choices"] == ["YES", "NO"]
    assert seen["temperature"] == 0.0
    assert seen["system"] == visit_merge._SAME_AUTHOR_SYSTEM
    parts = seen["parts"]
    assert [p.data for p in parts[:-1]] == [b"page-1", b"page-4", b"page-5"]
    assert isinstance(parts[-1], TextPart)
    assert "Did the SAME doctor or provider write or sign both" in parts[-1].text
    assert "Document A: pages 1-4" in parts[-1].text
    assert "Document B: pages 5-5" in parts[-1].text


@pytest.mark.parametrize("reply", ["NO", "", "MAYBE", "YES, probably"])
def test_only_a_clear_yes_merges(monkeypatch, reply):
    """GUARD: an unclear reply is a no."""
    _stub_provider(monkeypatch, reply=reply)
    assert visit_merge._same_author("/x.pdf", _PR2, _RFA) is False


def test_a_failed_call_merges_nothing(monkeypatch):
    """GUARD: an error keeps the boundary - a merge on missing evidence would hide a document."""
    _stub_provider(monkeypatch, error=RuntimeError("the backend is unhappy"))
    assert visit_merge._same_author("/x.pdf", _PR2, _RFA) is False


def test_a_stop_is_not_swallowed(monkeypatch):
    """GUARD: the reviewer's Stop unwinds to the job runner, never read as a model failure."""
    _stub_provider(monkeypatch, error=JobCancelled(0, 0))
    with pytest.raises(JobCancelled):
        visit_merge._same_author("/x.pdf", _PR2, _RFA)


# --- the setting -----------------------------------------------------------------------------


def test_the_setting_is_off_by_default():
    assert get_settings().__class__.model_fields["merge_visit_pieces"].default is False


def _segment_two_pieces(monkeypatch, enabled):
    import app.services.segment_engine as se

    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 3)])
    monkeypatch.setattr(se, "_window_rows", lambda *_a: [dict(_PR2), dict(_RFA)])
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row, *_a, **_kw: row)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False)
    monkeypatch.setattr(se.get_settings(), "merge_visit_pieces", enabled)
    monkeypatch.setattr(visit_merge, "_same_author", lambda *_a: True)
    return se.run_segmentation("/x.pdf", total_pages=3)


def test_segmentation_merges_a_visits_pieces_when_the_setting_is_on(monkeypatch):
    """DEMONSTRATES the wiring: the pass runs at the end of segmentation, before injury dates."""
    rows = _segment_two_pieces(monkeypatch, enabled=True)
    assert [(r["start"], r["end"], r["category"]) for r in rows] == [(1, 3, "1")]


def test_segmentation_leaves_the_draft_alone_when_the_setting_is_off(monkeypatch):
    """GUARD: off - the default - changes nothing."""
    rows = _segment_two_pieces(monkeypatch, enabled=False)
    assert [(r["start"], r["end"]) for r in rows] == [(1, 2), (3, 3)]
