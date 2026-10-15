"""Where the seam between two overlapping segmentation windows sits (`vllm_segment_ownership`).

The case these exist for is a run of identical two-page forms: a window that begins on a form's
SECOND page must still start a row at its own page 1, so it takes that page for a first page and
answers every following boundary one page late. The earlier window saw the same pages in the right
rhythm. Synthetic page numbers only.
"""

import pytest

from app.services import segment_engine as se
from app.worker.failures import JobCancelled

# Four 30-page windows over 75 pages, overlapping by 10, as `byte_budgeted_windows` lays out vLLM.
WINDOWS = [(1, 30), (21, 50), (41, 70), (61, 75)]
PAGES = 75
# The truth: a two-page form starts on every even page from 2.
TRUE_STARTS = [1, *range(2, 76, 2)]


def _rows(starts):
    return [{"start": s, "end": s} for s in starts]


def _window(ws, we, phase_ok=True):
    """A window's reply: page ws always starts a row; then even starts, or odd ones when the window
    took its first page (an even form's second page) for a first page."""
    if phase_ok:
        return _rows([ws, *[s for s in range(ws + 1, we + 1) if s % 2 == 0]])
    return _rows([ws, *range(ws + 2, we + 1, 2)])


def _starts(rows):
    return [int(r["start"]) for r in rows]


def test_edge_is_the_original_rule():
    """GUARD: window k owns (ws_k, ws_k+1] - the merge every record has had until now."""
    assert se._ownership_caps(WINDOWS, PAGES, "edge") == [21, 41, 61, 75]
    reports = [_window(*w) for w in WINDOWS]

    assert _starts(se.merge_window_rows(reports, WINDOWS, PAGES)) == TRUE_STARTS


def test_middle_moves_the_seam_into_the_overlap():
    assert se._ownership_caps(WINDOWS, PAGES, "middle") == [25, 45, 65, 75]


def test_middle_gives_the_overlap_pages_to_the_window_that_saw_them_in_context():
    """The later window took its first page for a first page; the middle line repairs the half of
    the overlap the earlier window owns."""
    reports = [_window(1, 30), _window(21, 50, phase_ok=False), _window(41, 70), _window(61, 75)]

    edge = _starts(se.merge_window_rows(reports, WINDOWS, PAGES))
    middle = _starts(se.merge_window_rows(reports, WINDOWS, PAGES, ownership="middle"))

    assert [s for s in edge if 21 < s <= 25] == [23, 25]
    assert [s for s in middle if 21 < s <= 25] == [22, 24]


def test_realign_reasks_the_window_from_a_start_the_earlier_window_found():
    asked = []

    def ask(_pdf, ws, we):
        asked.append((ws, we))
        return _window(ws, we)  # began on a real first page, so the rhythm is right

    reports = [_window(1, 30), _window(21, 50, phase_ok=False), _window(41, 70), _window(61, 75)]

    windows, reports, n = se._realigned("x.pdf", WINDOWS, reports, PAGES, 30, ask=ask)

    assert asked == [(24, 53)], (
        "the last start the earlier window saw in the first half of the overlap"
    )
    assert n == 1
    assert windows[1] == (24, 53)
    merged = _starts(se.merge_window_rows(reports, windows, PAGES, ownership="realign"))
    assert merged == TRUE_STARTS


def test_realign_carries_a_repair_to_the_next_seam():
    """A run of wrong-rhythm windows agree with EACH OTHER; only the repaired neighbour exposes the
    next one, which is why the seams are walked left to right."""
    asked = []

    def ask(_pdf, ws, we):
        asked.append((ws, we))
        return _window(ws, we)

    reports = [
        _window(1, 30),
        _window(21, 50, phase_ok=False),
        _window(41, 70, phase_ok=False),
        _window(61, 75, phase_ok=False),
    ]

    windows, reports, n = se._realigned("x.pdf", WINDOWS, reports, PAGES, 30, ask=ask)

    assert n == 3
    assert (
        _starts(se.merge_window_rows(reports, windows, PAGES, ownership="realign")) == TRUE_STARTS
    )


def test_realign_asks_nothing_when_the_windows_agree():
    """GUARD: no extra call on a record whose windows already agree."""

    def ask(*_a):
        raise AssertionError("no seam disagrees")

    reports = [_window(*w) for w in WINDOWS]

    windows, _reports, n = se._realigned("x.pdf", WINDOWS, reports, PAGES, 30, ask=ask)

    assert n == 0
    assert windows == WINDOWS


def test_a_failed_reask_keeps_the_first_answer():
    def ask(*_a):
        raise RuntimeError("the pod went away")

    reports = [_window(1, 30), _window(21, 50, phase_ok=False), _window(41, 70), _window(61, 75)]

    windows, out, n = se._realigned("x.pdf", WINDOWS, reports, PAGES, 30, ask=ask)

    assert n == 0
    assert windows == WINDOWS
    assert out == reports


def test_a_reviewers_stop_during_a_reask_propagates():
    def ask(*_a):
        raise JobCancelled(1, 2)

    reports = [_window(1, 30), _window(21, 50, phase_ok=False), _window(41, 70), _window(61, 75)]

    with pytest.raises(JobCancelled):
        se._realigned("x.pdf", WINDOWS, reports, PAGES, 30, ask=ask)


def test_gemini_always_uses_the_edge(monkeypatch):
    """GUARD: the setting is our model's; Gemini's merge is untouched whatever it says."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "vllm_segment_ownership", "realign")
    monkeypatch.setattr(settings, "llm_backend_overrides", "")

    monkeypatch.setattr(settings, "llm_backend", "gemini")
    assert se._window_ownership(settings) == "edge"

    monkeypatch.setattr(settings, "llm_backend", "vllm")
    assert se._window_ownership(settings) == "realign"
