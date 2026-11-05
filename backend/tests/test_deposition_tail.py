"""A deposition's closing paperwork offered back to it as a merge hint (`deposition_tail`,
VISIT_MERGE_HINTS). Synthetic rows only; no model call is possible here."""

import pytest

from app.services import deposition_tail


def _row(start, end, category, title, **over):
    return {
        "start": start,
        "end": end,
        "category": category,
        "title": title,
        "date": "03/02/2026",
        "injury_date": "-",
        "flag": "-",
        **over,
    }


_DEPOSITION = _row(1, 80, "9", "DEPOSITION OF SAMPLE APPLICANT")


@pytest.mark.parametrize(
    "title",
    [
        "REPORTER'S CERTIFICATE",
        "ERRATA SHEET",
        "DECLARATION UNDER PENALTY OF PERJURY - SIGNATURE PAGE",
        "STIPULATIONS",
        "CORRECTION SHEET",
        "INDEX OF EXHIBITS",
    ],
)
def test_a_short_paperwork_row_after_a_deposition_is_hinted(title):
    """DEMONSTRATES: reviewers joined such rows back 15 of 16 times on Gemini's drafts, 2 of 2 on
    ours."""
    rows = [_DEPOSITION, _row(81, 83, "100", title)]
    out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert [bool(r.get("suggest_merge")) for r in out] == [False, True]
    assert hinted == 1
    assert "suggest_merge" not in rows[1], "the caller's own rows must come back untouched"


def test_the_same_title_after_another_document_is_not_hinted():
    """GUARD: only a deposition's tail - a certificate after a report is its own paperwork."""
    rows = [_row(1, 10, "13", "PANEL QME REPORT"), _row(11, 11, "100", "REPORTER'S CERTIFICATE")]
    out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert hinted == 0
    assert not any(r.get("suggest_merge") for r in out)


def test_a_row_longer_than_six_pages_is_not_hinted():
    """GUARD: a tail is short; seven pages is a document of its own."""
    rows = [_DEPOSITION, _row(81, 87, "100", "ERRATA SHEET")]
    _out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert hinted == 0
    rows = [_DEPOSITION, _row(81, 86, "100", "ERRATA SHEET")]
    _out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert hinted == 1


@pytest.mark.parametrize("title", ["PR-2 PROGRESS REPORT", "MRI OF THE LUMBAR SPINE", "LETTER"])
def test_an_ordinary_title_after_a_deposition_is_not_hinted(title):
    """GUARD: short rows after a deposition with any other title were joined only 12 of 57 times
    (1 of 10 on ours) - the title is what makes the hint safe."""
    rows = [_DEPOSITION, _row(81, 82, "1", title)]
    _out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert hinted == 0


def test_only_the_row_directly_after_the_deposition_is_hinted():
    """GUARD: a second paperwork row after the first tail was not measured; it is left alone."""
    rows = [
        _DEPOSITION,
        _row(81, 81, "100", "ERRATA SHEET"),
        _row(82, 82, "100", "REPORTER'S CERTIFICATE"),
    ]
    out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert [bool(r.get("suggest_merge")) for r in out] == [False, True, False]
    assert hinted == 1


def test_a_row_already_hinted_is_not_counted_and_nothing_moves():
    """GUARD: hints only - rows keep their pages and categories; an existing hint stands."""
    rows = [_DEPOSITION, _row(81, 82, "100", "ERRATA SHEET", suggest_merge=True)]
    out, hinted = deposition_tail.hint_deposition_paperwork(rows)
    assert hinted == 0
    assert out[1]["suggest_merge"] is True
    assert [(r["start"], r["end"], r["category"]) for r in out] == [(1, 80, "9"), (81, 82, "100")]


def _segment(monkeypatch, hints):
    import app.services.segment_engine as se

    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 82)])
    monkeypatch.setattr(
        se, "_window_rows", lambda *_a: [dict(_DEPOSITION), _row(81, 82, "100", "ERRATA SHEET")]
    )
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row, *_a, **_kw: row)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False)
    monkeypatch.setattr(se.get_settings(), "merge_visit_pieces", False)
    monkeypatch.setattr(se.get_settings(), "visit_merge_hints", hints)
    return se.run_segmentation("/x.pdf", total_pages=82)


def test_segmentation_hints_the_tail_when_the_setting_is_on(monkeypatch):
    """DEMONSTRATES the wiring: on the VISIT_MERGE_HINTS switch, at the end of segmentation."""
    rows = _segment(monkeypatch, hints=True)
    assert [bool(r.get("suggest_merge")) for r in rows] == [False, True]


def test_segmentation_hints_no_tail_when_the_setting_is_off(monkeypatch):
    """GUARD: off - the default - is today's draft."""
    rows = _segment(monkeypatch, hints=False)
    assert not any(r.get("suggest_merge") for r in rows)
