"""The boundary A/B's aggregation (scripts/eval/ab_stats.py).

These are the numbers that decide whether a segmentation prompt change ships, and on 2026-08-17 two
prompt comparisons were reported as signal and both reversed - once because two arms' totals were
summed over different numbers of documents, once because a gap smaller than the run-to-run spread was
read as a difference. Both are arithmetic errors, so they are pinned here.

The module is stdlib-only by design, so it loads by path in milliseconds; the harness that calls it
imports the torch classifier and would take ~45 seconds.
"""

import importlib.util
import os

import pytest

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "eval", "ab_stats.py"
)
_spec = importlib.util.spec_from_file_location("ab_stats", _PATH)
ab_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ab_stats)


def _score(predicted, exact, truth=20, over=0, under=0, misaligned=0):
    """One run's score dict in the shape the harness's _score returns."""
    return {
        "predicted": predicted,
        "truth": truth,
        "exact": exact,
        "over_split": over,
        "under_split": under,
        "misaligned": misaligned,
        "exact_pct": round(100.0 * exact / predicted, 1) if predicted else 0.0,
    }


def test_summarize_reports_the_observed_range_not_just_the_mean():
    """A spread of runs must survive into the summary; a mean alone hides the variance."""
    runs = {("doc1", "control"): [_score(17, 17), _score(13, 10), _score(15, 14)]}

    summary = ab_stats.summarize(runs)[("doc1", "control")]

    assert summary["n"] == 3
    assert summary["exact"]["lo"] == 10
    assert summary["exact"]["hi"] == 17
    assert summary["exact"]["mean"] == pytest.approx(41 / 3)


def test_a_document_an_arm_failed_is_excluded_from_the_comparable_set():
    """The invalid-TOTAL bug: one arm losing a run must drop the document for EVERY arm.

    Otherwise control totals five documents, the contender totals six, and the two are printed in
    adjacent rows as though they answered the same question.
    """
    runs = {
        ("doc1", "control"): [_score(10, 8)],
        ("doc1", "contender"): [_score(10, 9)],
        ("doc2", "control"): [_score(10, 8)],
        # doc2 died on the contender arm - a 504 on one window loses that run.
    }

    kept, dropped = ab_stats.comparable_documents(
        ["doc1", "doc2"], ["control", "contender"], runs, repeats=1
    )

    assert kept == ["doc1"]
    assert dropped == [("doc2", {"contender": 1})]


def test_a_partly_completed_repeat_set_is_also_excluded():
    """Two of three repeats is not a completed arm: it changes the denominator just as a failure does."""
    runs = {
        ("doc1", "control"): [_score(10, 8), _score(10, 7), _score(10, 9)],
        ("doc1", "contender"): [_score(10, 9), _score(10, 9)],
    }

    kept, dropped = ab_stats.comparable_documents(
        ["doc1"], ["control", "contender"], runs, repeats=3
    )

    assert kept == []
    assert dropped == [("doc1", {"contender": 1})]


def test_totals_average_repeats_so_more_runs_do_not_inflate_the_scale():
    """A document run three times must weigh the same as one run of it, or totals leave truth's scale."""
    once = {("doc1", "control"): [_score(10, 8)]}
    thrice = {("doc1", "control"): [_score(10, 8), _score(10, 8), _score(10, 8)]}

    assert (
        ab_stats.totals(["control"], ["doc1"], once)["control"]["exact"]
        == ab_stats.totals(["control"], ["doc1"], thrice)["control"]["exact"]
    )


def test_totals_report_how_many_documents_they_cover():
    """The denominator has to travel with the number, so a reader cannot compare two unlike totals."""
    runs = {
        ("doc1", "control"): [_score(10, 8)],
        ("doc2", "control"): [_score(10, 6)],
    }

    total = ab_stats.totals(["control"], ["doc1", "doc2"], runs)["control"]

    assert total["documents"] == 2
    assert total["exact"] == 14
    assert total["exact_pct"] == 70.0


def test_a_gap_inside_the_run_to_run_spread_does_not_clear_the_noise():
    """The reversed-call bug: a 1-row mean gap against a 4-row self-spread is not a difference."""
    runs = {
        ("doc1", "control"): [_score(17, 13), _score(17, 17), _score(17, 15)],
        ("doc1", "contender"): [_score(17, 14), _score(17, 18), _score(17, 16)],
    }
    summaries = ab_stats.summarize(runs)

    verdict = ab_stats.compare("control", "contender", ["doc1"], summaries)

    assert verdict["rows"][0]["gap"] == pytest.approx(1.0)
    assert verdict["rows"][0]["noise"] == 4
    assert verdict["rows"][0]["clears"] is False


def test_a_gap_wider_than_the_spread_clears_it():
    """The converse must also hold, or the guard would reject every result including real ones."""
    runs = {
        ("doc1", "control"): [_score(17, 10), _score(17, 10), _score(17, 11)],
        ("doc1", "contender"): [_score(17, 16), _score(17, 16), _score(17, 17)],
    }
    summaries = ab_stats.summarize(runs)

    verdict = ab_stats.compare("control", "contender", ["doc1"], summaries)

    assert verdict["rows"][0]["clears"] is True


def test_a_single_run_per_arm_reports_noise_as_unmeasured_not_as_cleared():
    """n=1 has zero observed spread, and zero observed spread is not zero noise.

    Returning False would read as "measured, did not clear"; None says the question was not asked.
    This distinction is the whole reason --repeats exists.
    """
    runs = {
        ("doc1", "control"): [_score(17, 10)],
        ("doc1", "contender"): [_score(17, 16)],
    }
    summaries = ab_stats.summarize(runs)

    verdict = ab_stats.compare("control", "contender", ["doc1"], summaries)

    assert verdict["rows"][0]["clears"] is None
    assert verdict["unmeasured"] == 1
    assert verdict["cleared"] == 0


def test_the_sign_test_needs_a_clean_sweep_at_six_documents():
    """Pins the ceiling the output warns about: 6-0 reaches 0.031, and 5-1 only reaches 0.219."""
    assert ab_stats.sign_test_p(6, 0) == pytest.approx(0.03125)
    assert ab_stats.sign_test_p(5, 1) == pytest.approx(0.21875)
    assert ab_stats.sign_test_p(3, 3) == pytest.approx(1.0)
    assert ab_stats.sign_test_p(0, 0) == 1.0


def test_ties_are_dropped_from_the_sign_test_and_counted_separately():
    """A document where the arms tie carries no directional information, but must not vanish."""
    runs = {
        ("doc1", "control"): [_score(10, 8)],
        ("doc1", "contender"): [_score(10, 8)],
        ("doc2", "control"): [_score(10, 8)],
        ("doc2", "contender"): [_score(10, 9)],
    }
    summaries = ab_stats.summarize(runs)

    verdict = ab_stats.compare("control", "contender", ["doc1", "doc2"], summaries)

    assert (verdict["wins"], verdict["losses"], verdict["ties"]) == (1, 0, 1)
    assert verdict["p"] == pytest.approx(1.0)
