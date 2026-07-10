"""Unit tests for the category-bundle helpers (Diagnostic&Operative / Depositions).

Pure logic + PDF assembly are deterministic and tested directly; the summarize path is
tested with summarize_row mocked (no Gemini).
"""

from pypdf import PdfReader, PdfWriter

from mrr_ai.services import bundles, summarize_engine


def _rows(*specs):
    # specs: (start, end, category) -> full row dict shape the API produces.
    return [
        {
            "start": s,
            "end": e,
            "category": c,
            "title": f"doc {s}-{e}",
            "date": "01/01/2024",
            "injury_date": "-",
            "flag": "-",
            "suggest_merge": False,
        }
        for (s, e, c) in specs
    ]


def test_matched_rows_filters_by_category_set_order_preserved():
    rows = _rows((1, 5, "1"), (6, 7, "3"), (8, 9, "8"), (10, 12, "9"))
    # int/str mix in the requested set is normalized.
    matched = bundles.matched_rows(rows, [3, "8"])
    assert [(r["start"], r["category"]) for r in matched] == [(6, "3"), (8, "8")]


def test_matched_rows_empty_when_no_category_hits():
    rows = _rows((1, 5, "1"), (6, 7, "2"))
    assert bundles.matched_rows(rows, ["9"]) == []


def test_pages_for_rows_expands_inclusive_ranges_in_order():
    rows = _rows((2, 4, "3"), (9, 9, "8"))
    assert bundles.pages_for_rows(rows) == [2, 3, 4, 9]


def test_build_bundle_pdf_contains_exactly_the_matched_pages(tmp_path):
    src = tmp_path / "src.pdf"
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=72, height=72)
    with open(src, "wb") as fh:
        writer.write(fh)

    rows = _rows((2, 3, "3"), (7, 7, "8"))  # 2 + 1 = 3 pages
    buffer = bundles.build_bundle_pdf(str(src), rows)
    assert buffer.getvalue()[:4] == b"%PDF"
    assert len(PdfReader(buffer).pages) == 3


def test_build_bundle_pdf_skips_out_of_range_pages(tmp_path):
    # Defensive: a row referencing a page past the file must not crash the whole bundle.
    src = tmp_path / "src.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    with open(src, "wb") as fh:
        writer.write(fh)

    buffer = bundles.build_bundle_pdf(str(src), _rows((2, 9, "3")))  # 2,3 valid; 4-9 gone
    assert len(PdfReader(buffer).pages) == 2


def test_bundle_summary_entries_maps_summarize_row_output(monkeypatch):
    def fake_summarize(pdf_path, row, model=None):
        return {
            "summaryTitle": f"T{row['start']}",
            "summaryDate": row["date"],
            "summaryText": "body",
            "sourceText": "ocr",
            "manualCheck": "",
        }

    monkeypatch.setattr(summarize_engine, "summarize_row", fake_summarize)
    rows = _rows((6, 7, "3"), (8, 9, "8"))
    entries = bundles.bundle_summary_entries("case.pdf", rows, model="m")
    assert entries == [
        {"summaryDate": "01/01/2024", "summaryTitle": "T6", "summaryText": "body"},
        {"summaryDate": "01/01/2024", "summaryTitle": "T8", "summaryText": "body"},
    ]
