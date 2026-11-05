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


def _review_page(
    footer, entries=("01/13/2026 Sample Therapist, PT", "Chief complaint: shoulder pain.")
):
    """A page of the evaluation's records review: dated summaries of other records, no evaluator
    wording, and only the evaluation's page label in its footer."""
    body = "\n".join(entries + ("Plan: continue therapy.",) * 6)
    return f"{body}\n{footer}"


def test_the_pieces_of_an_evaluations_records_review_are_suggested_merges():
    """DEMONSTRATES the 2026-11-02 email's case: the records review cut into rows, each piece under
    the evaluation's header with the page numbering running on."""
    rows = [_row(1, 4, "13"), _row(5, 5, "1"), _row(6, 6, "2"), _row(7, 8, "1")]
    out, stats = ep.suggest_evaluation_pieces(rows, _pages(_evaluation(1, 8)))
    assert [r["suggest_merge"] for r in out] == [False, True, True, True]
    assert stats == {"evaluation_pieces": 3}


def test_records_review_pages_need_no_evaluator_wording():
    """DEMONSTRATES the 2026-11-05 email's case: the review's pages name no QME or AME - only the
    footer label runs on - and a summary of an earlier evaluation inside the review is filed 13
    itself. Every piece is suggested and the walk goes on through the 13."""
    texts = {
        1: f"{_HEADER}\nPanel Qualified Medical Evaluation",
        **{page: _review_page(f"Page {page}") for page in range(2, 9)},
    }
    rows = [_row(1, 3, "13"), _row(4, 4, "5"), _row(5, 5, "13"), _row(6, 6, "5"), _row(7, 8, "1")]
    out, stats = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, True, True, True, True]
    assert stats == {"evaluation_pieces": 4}


def test_a_page_n_of_m_footer_runs_on_like_page_n():
    """DEMONSTRATES the label forms: "7 of 40" and "Page: 7 of 40" both count."""
    texts = {
        1: f"{_HEADER}\n1 of 40",
        2: _review_page("2 of 40"),
        3: _review_page("Page: 3 of 40"),
    }
    rows = [_row(1, 1, "13"), _row(2, 2, "5"), _row(3, 3, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, True, True]


def test_a_new_evaluation_whose_numbering_starts_again_is_not_suggested():
    """GUARD: a supplemental report placed after the evaluation carries the same wording but starts
    its own page numbering - a real document of its own."""
    texts = {**_evaluation(1, 4), **_evaluation(5, 8, first_number=1)}
    rows = [_row(1, 4, "13"), _row(5, 8, "12")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False]


def test_the_walk_stops_at_the_first_cut_whose_numbering_does_not_run_on():
    """GUARD: the next document starts its own numbering; it is not marked, and neither is anything
    after it - even a row whose pages number on from that document's."""
    texts = {
        **_evaluation(1, 4),
        5: "Primary Treating Physician's Progress Report\nPage 1",
        6: "Work status\nPage 2",
    }
    rows = [_row(1, 4, "13"), _row(5, 5, "1"), _row(6, 6, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False, False]


def test_a_cut_with_no_label_on_either_side_ends_the_walk():
    """GUARD: an unnumbered page is not a continuation, and the walk does not jump over it."""
    texts = {**_evaluation(1, 4), 5: "Attached letter, unnumbered.", 6: _evaluation_page(6)}
    rows = [_row(1, 4, "13"), _row(5, 5, "100"), _row(6, 6, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False, False]


def test_a_different_total_does_not_run_on():
    """GUARD: "4 of 10" then "5 of 12" are two documents' numbering, not one."""
    texts = {1: f"{_HEADER}\n1 of 10", 4: _review_page("4 of 10"), 5: _review_page("5 of 12")}
    rows = [_row(1, 4, "13"), _row(5, 5, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False]


def test_a_page_number_quoted_in_the_body_is_not_a_label():
    """GUARD: only the first and last lines carry the page's own label; a records review quotes
    the pages of the records it summarizes ("see page 5")."""
    body = ("Summary line.",) * 5
    texts = {
        **_evaluation(1, 3),
        4: "\n".join(body + ("as noted on page 4 of the records",) + body),
        5: "\n".join(body + ("continued on page 5 of the records",) + body),
    }
    rows = [_row(1, 4, "13"), _row(5, 5, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False]


def test_numbering_stamped_across_the_whole_bundle_starts_no_walk():
    """GUARD: a fax transmission or Bates numbers run on into the evaluation from the document
    before it, and on through every document after it - they say nothing about where it ends."""
    texts = {page: f"FAX Page {page}\nSome document." for page in range(1, 9)}
    rows = [_row(1, 2, "1"), _row(3, 5, "13"), _row(6, 6, "1"), _row(7, 8, "5")]
    out, stats = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False, False, False]
    assert stats == {"evaluation_pieces": 0}


def test_a_bundle_stamp_is_seen_past_an_unnumbered_first_page():
    """GUARD: the evaluation's own first page carries no label, but its second follows the page
    before the evaluation by two - the same stamp."""
    texts = {page: f"FAX Page {page}\nSome document." for page in range(1, 9)}
    texts[3] = "Panel Qualified Medical Evaluation"
    rows = [_row(1, 2, "1"), _row(3, 5, "13"), _row(6, 6, "1")]
    out, _ = ep.suggest_evaluation_pieces(rows, _pages(texts))
    assert [r["suggest_merge"] for r in out] == [False, False, False]


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
