"""Unit tests for the sliding-window segmentation engine (LLM + OCR fully mocked)."""

from mrr_ai.services import segment_engine
from mrr_ai.services.classification import Classification
from mrr_ai.services.segment_engine import merge_window_rows, run_segmentation


def _row(start, end, title="Doc", flag="-"):
    return dict(start=start, end=end, title=title, date="-", injury_date="-", flag=flag)


def test_merge_drops_artifact_and_unsevers_straddling_doc():
    # Doc 47-55 straddles the seam at 51: window 2 reports its own first page (51) as a
    # start (artifact). Ownership keeps window 1's view (start 47) and drops the cut.
    windows = [(1, 80), (51, 91)]
    reports = [
        [_row(1, 46, "A"), _row(47, 80, "B straddles")],
        [_row(51, 55, "artifact"), _row(56, 91, "C")],
    ]
    merged = merge_window_rows(reports, windows, total_pages=91)
    assert [r["start"] for r in merged] == [1, 47, 56]
    assert [r["end"] for r in merged] == [46, 55, 91]  # tiled ends, straddler intact
    assert merged[1]["title"] == "B straddles"


def test_merge_prepends_front_row_when_model_skips_page_one():
    merged = merge_window_rows([[_row(3, 10, "late")]], [(1, 10)], total_pages=10)
    assert merged[0]["start"] == 1 and merged[0]["flag"] == "x"
    assert merged[1]["start"] == 3 and merged[-1]["end"] == 10


def test_merge_respects_ownership_and_dedups_duplicate_starts():
    # Ownership: window 1 owns (0, 31], window 2 owns (31, 90] - so page 40 is decided
    # by window 2 even though window 1 also saw it. Duplicate starts WITHIN a window's
    # report (model emitting two rows for one page) collapse to the first.
    windows = [(1, 60), (31, 90)]
    reports = [
        [_row(1, 30, "A"), _row(40, 60, "window1 view - not owner")],
        [_row(31, 39, "artifact"), _row(40, 60, "owner first"), _row(40, 90, "owner dup")],
    ]
    merged = merge_window_rows(reports, windows, total_pages=90)
    forty = [r for r in merged if r["start"] == 40]
    assert len(forty) == 1 and forty[0]["title"] == "owner first"


def test_run_segmentation_wires_windows_classify_and_progress(monkeypatch, make_pdf, tmp_path):
    pdf = make_pdf(tmp_path / "case.pdf", pages=6)
    monkeypatch.setattr(segment_engine, "byte_budgeted_windows", lambda *a, **k: [(1, 4), (3, 6)])
    fake_reports = {
        (1, 4): [_row(1, 2, "Operative Report"), _row(3, 4, "Ambiguous")],
        (3, 6): [_row(3, 4, "artifact"), _row(5, 6, "Deposition")],
    }
    monkeypatch.setattr(
        segment_engine,
        "_window_rows",
        lambda pdf_path, ws, we, client: fake_reports[(ws, we)],
    )

    def fake_classify(title, page_text=None):
        if title == "Ambiguous" and page_text is None:
            return Classification("100", "low", "disagree", needs_review=True)
        mapping = {"Operative Report": "8", "Ambiguous": "1", "Deposition": "9"}
        return Classification(mapping.get(title, "100"), "high", "rules", needs_review=False)

    monkeypatch.setattr(segment_engine, "classify", fake_classify)
    monkeypatch.setattr(
        segment_engine, "extract_text_from_selected_pages", lambda path, pages: "text"
    )
    # verification is unit-tested separately; here it must not reach for a real oracle
    monkeypatch.setattr(
        segment_engine,
        "verify_and_merge",
        lambda pdf_path, rows, progress=None: (rows, dict(suspects=0, merged_away=0)),
    )

    calls = []
    rows = run_segmentation(pdf, 6, progress=lambda *a: calls.append(a))

    assert [(r["start"], r["end"], r["category"]) for r in rows] == [
        (1, 2, "8"),
        (3, 4, "1"),
        (5, 6, "9"),
    ]
    stages = {c[0] for c in calls}
    assert stages == {"segmenting", "categorizing"}
    assert ("segmenting", 2, 2) in calls and ("categorizing", 3, 3) in calls
