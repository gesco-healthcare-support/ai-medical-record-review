"""A QME/AME evaluation cut into pieces is offered back as one (`evaluation_pieces`,
SUGGEST_EVALUATION_PIECES). Synthetic rows and page text only."""

from app.config import get_settings
from app.services import evaluation_pieces as ep

_HEADER = "Comprehensive Medical-Legal Evaluation - Panel QME\nRe: Sample Applicant"


def _row(start, end, category, suggest=False):
    return {
        "start": start,
        "end": end,
        "category": category,
        "title": "-",
        "date": "03/02/2026",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": suggest,
    }


def _pages(texts):
    return lambda page: texts.get(page, "")


def _evaluation_page(number, body="Summary of the records reviewed."):
    return f"{_HEADER}\nPage {number}\n{body}"


def _evaluation(first_page, last_page, first_number=1):
    return {
        page: _evaluation_page(first_number + page - first_page)
        for page in range(first_page, last_page + 1)
    }


def test_the_pieces_of_an_evaluations_records_review_are_suggested_merges():
    """DEMONSTRATES the email's case: an evaluation whose records review was cut into rows, each
    piece under the same evaluator header with the page numbering running on."""
    rows = [_row(1, 4, "13"), _row(5, 5, "1"), _row(6, 6, "2"), _row(7, 8, "13")]
    out, stats = ep.suggest_evaluation_pieces(rows, _pages(_evaluation(1, 8)))
    assert [r["suggest_merge"] for r in out] == [False, True, True, True]
    assert stats == {"evaluation_pieces": 3}


def test_a_new_evaluation_whose_numbering_starts_again_is_not_suggested():
    """GUARD: a supplemental report placed after the evaluation carries the same wording but starts
    its own page numbering - a real document of its own."""
    texts = {**_evaluation(1, 4), **_evaluation(5, 8, first_number=1)}
    rows = [_row(1, 4, "13"), _row(5, 8, "12")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False]


def test_the_walk_stops_where_the_evaluator_wording_stops():
    """GUARD: the next document after the evaluation, and everything after it, is never marked -
    even when its own pages happen to number on."""
    texts = {
        **_evaluation(1, 4),
        5: "Primary Treating Physician's Progress Report\nPage 5",
        6: _evaluation_page(6),
    }
    rows = [_row(1, 4, "13"), _row(5, 5, "1"), _row(6, 6, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False, False]


def test_a_cut_whose_numbering_does_not_run_on_is_not_suggested():
    """GUARD: evaluator wording on both pages is not enough - reviewers kept 118 such boundaries as
    real ticked documents. Only the numbering running on marks a piece."""
    texts = {
        **_evaluation(1, 4),
        5: f"{_HEADER}\nAttached letter, unnumbered.",
        6: _evaluation_page(6),
    }
    rows = [_row(1, 4, "13"), _row(5, 5, "100"), _row(6, 6, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    # Neither cut has numbers on both of its pages, so neither is marked - and the walk goes on
    # past an unmarked cut while the wording continues, which the next test's stop does not.
    assert [r["suggest_merge"] for r in out] == [False, False, False]
    texts[5] = _evaluation_page(5, body="Attached letter.")
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, True, True]


def test_rows_after_a_document_that_is_not_an_evaluation_are_never_suggested():
    """GUARD: only a category 12 or 13 row starts a walk."""
    rows = [_row(1, 4, "1"), _row(5, 5, "1")]
    out, stats = ep.suggest_evaluation_pieces(rows, _pages(_evaluation(1, 5)))
    assert [r["suggest_merge"] for r in out] == [False, False]
    assert stats == {"evaluation_pieces": 0}


def test_a_suggestion_already_made_is_never_cleared():
    """GUARD: the verify pass's own suggestions stand; this only adds."""
    rows = [_row(1, 4, "1"), _row(5, 5, "1", suggest=True)]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages({}))
    assert out[1]["suggest_merge"] is True


def test_no_boundary_moves():
    """GUARD: suggestions only - every row keeps its pages and category."""
    rows = [_row(1, 4, "13"), _row(5, 5, "1"), _row(6, 8, "2")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(_evaluation(1, 8)))
    assert [(r["start"], r["end"], r["category"]) for r in out] == [
        (1, 4, "13"),
        (5, 5, "1"),
        (6, 8, "2"),
    ]


# --- the setting -----------------------------------------------------------------------------


def test_the_setting_is_off_by_default():
    assert get_settings().__class__.model_fields["suggest_evaluation_pieces"].default is False


def _segment(monkeypatch, enabled, page_text_fn):
    import app.services.segment_engine as se

    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 5)])
    monkeypatch.setattr(se, "_window_rows", lambda *_a: [_row(1, 4, "13"), _row(5, 5, "1")])
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row, *_a, **_kw: row)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False)
    monkeypatch.setattr(se.get_settings(), "merge_visit_pieces", False)
    monkeypatch.setattr(se.get_settings(), "suggest_evaluation_pieces", enabled)
    return se.run_segmentation("/x.pdf", total_pages=5, page_text_fn=page_text_fn)


def test_segmentation_marks_the_pieces_when_the_setting_is_on(monkeypatch):
    """DEMONSTRATES the wiring: the pass runs at the end of segmentation on the stored page text."""
    rows = _segment(monkeypatch, True, _pages(_evaluation(1, 5)))
    assert [r["suggest_merge"] for r in rows] == [False, True]


def test_segmentation_marks_nothing_when_the_setting_is_off(monkeypatch):
    """GUARD: off by default, the draft is what it was."""
    rows = _segment(monkeypatch, False, _pages(_evaluation(1, 5)))
    assert [r["suggest_merge"] for r in rows] == [False, False]


def test_segmentation_without_page_text_marks_nothing(monkeypatch):
    """GUARD: with no stored page text there is nothing to read, so nothing is suggested."""
    rows = _segment(monkeypatch, True, None)
    assert [r["suggest_merge"] for r in rows] == [False, False]
