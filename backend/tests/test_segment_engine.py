"""Segmentation's injury-date stage, and the removal of the field it replaces.

The date of injury used to be read twice - once as field "i" of the segmentation call, which sees a
whole WINDOW and so propagates one document's date onto neighbours that state none, and again at
summarize time. The two never reconciled and the summarize value won, which is how a reviewer's
correction came to be silently discarded.

These pin the replacement: one isolated read per sub-document, at the END of run_segmentation so the
page ranges are final, storing onto the row that everything downstream reads.

Pure: the model client, the window call and the categorizer are all stubbed, so nothing hits Vertex.
"""

import pytest


def test_segmentation_reads_the_injury_date_per_sub_document(monkeypatch):
    """WHEN segmentation finishes, THE SYSTEM SHALL read each row's injury date from that row's OWN
    pages and store it on the row - one read, in one place, so the row is the source of truth.

    A segmentation WINDOW spans many documents, so a date read from a window propagates onto
    neighbours that state none. That propagation is what this replaces.
    """
    import app.services.segment_engine as se

    seen = []

    def fake_extract(pdf_path, start, end, model=None, strict=False):
        seen.append((start, end))
        return f"0{start}/08/2022"

    monkeypatch.setattr(se, "extract_injury_date", fake_extract)
    monkeypatch.setattr(se, "get_genai_client", lambda: None)
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 4)])
    monkeypatch.setattr(
        se,
        "_window_rows",
        lambda pdf_path, ws, we, client: [
            dict(start=1, end=2, title="A", date="-", injury_date="-", flag="-"),
            dict(start=3, end=4, title="B", date="-", injury_date="-", flag="-"),
        ],
    )
    # *_a absorbs page_text_fn, which the page-text store added to _categorize. A too-narrow stub
    # raises TypeError inside a pool worker, where it surfaces as an opaque job failure.
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row, *_a, **_kw: row)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False, raising=False)

    rows = se.run_segmentation("/x.pdf", total_pages=4)

    # Each row asked about its OWN page range, and nothing else.
    assert seen == [(1, 2), (3, 4)]
    assert [r["injury_date"] for r in rows] == ["01/08/2022", "03/08/2022"]


def test_a_failed_injury_date_read_leaves_the_row_at_the_sentinel(monkeypatch):
    """WHEN the read cannot produce a date, THE SYSTEM SHALL leave "-" and still return the rows.

    extract_injury_date is fail-safe and returns "-" itself, so this pins that a per-row failure is
    never allowed to fail the segmentation job - a missing date costs a DOI prefix, not coverage.
    """
    import app.services.segment_engine as se

    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    monkeypatch.setattr(se, "get_genai_client", lambda: None)
    monkeypatch.setattr(se, "byte_budgeted_windows", lambda *a, **k: [(1, 2)])
    monkeypatch.setattr(
        se,
        "_window_rows",
        lambda pdf_path, ws, we, client: [
            dict(start=1, end=2, title="A", date="-", injury_date="-", flag="-")
        ],
    )
    # *_a absorbs page_text_fn, which the page-text store added to _categorize. A too-narrow stub
    # raises TypeError inside a pool worker, where it surfaces as an opaque job failure.
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row, *_a, **_kw: row)
    monkeypatch.setattr(se.get_settings(), "verify_merge", False, raising=False)

    rows = se.run_segmentation("/x.pdf", total_pages=2)
    assert [r["injury_date"] for r in rows] == ["-"]


def test_the_segmentation_call_no_longer_reports_an_injury_date():
    """WHEN the segmentation schema is inspected, THE SYSTEM SHALL NOT ask the model for an injury
    date. Reading it from a whole window is what propagated one document's date onto its neighbours."""
    from app.services.gemini import SEGMENT_RESPONSE_SCHEMA, SEGMENTATION_PROMPT, parse_segment_item

    props = SEGMENT_RESPONSE_SCHEMA["items"]["properties"]
    assert "i" not in props
    assert "i" not in SEGMENT_RESPONSE_SCHEMA["items"]["required"]
    assert "i" not in SEGMENT_RESPONSE_SCHEMA["items"]["propertyOrdering"]
    # The prompt still mentions the date of injury, deliberately - it warns the model not to put
    # one in the document-date field. What must be gone is the FIELD, which the schema proves.
    assert '"i" injury date' not in SEGMENTATION_PROMPT
    assert '"i":' not in SEGMENTATION_PROMPT
    # And the parser returns five fields, not six.
    parsed = parse_segment_item({"s": 1, "e": 2, "t": "T", "d": "01/02/2020", "m": "-"})
    assert len(parsed) == 5


class _Result:
    """Stand-in for classification.Classification, which is a frozen dataclass in the real module."""

    def __init__(self, category, needs_review):
        self.category = category
        self.needs_review = needs_review
        self.confidence = "low" if needs_review else "high"
        self.method = "stub"


def _row(start, end, title="Report", flag="-"):
    return {"start": start, "end": end, "title": title, "date": "-", "flag": flag}


def _categorize_capturing(monkeypatch, answers, *, confident_on_title=False):
    """Run _categorize with classify() stubbed; return (row, list of page_text passed to classify).

    `answers` maps the escalation text it receives to the category it should return, so a test can
    say "these pages mean 13, those mean 100" without a model.
    """
    from app.services import segment_engine as se

    seen = []

    def _classify(title, page_text=None):
        seen.append(page_text)
        if page_text is None:
            return _Result("100", needs_review=not confident_on_title)
        return _Result(answers.get(page_text, "100"), needs_review=False)

    monkeypatch.setattr(se, "classify", _classify)
    return se, seen


def test_escalation_reads_the_rows_first_three_pages_not_just_one(monkeypatch):
    """The bug this fixes: one page of evidence let a boundary decide a 52-page document.

    Page 93 (the previous document's tail) answered 100 and page 93+94 still answered 100; only the
    three-page read recovered 13. So the escalation must hand classify() all three, joined.
    """
    se, seen = _categorize_capturing(monkeypatch, {"p93\np94\np95": "13"})
    pages = {93: "p93", 94: "p94", 95: "p95", 96: "p96"}
    row = se._categorize("x.pdf", _row(93, 145), lambda p: pages.get(p, ""))

    assert seen[1:] == ["p93\np94\np95"], "escalation must read three pages, joined in page order"
    assert row["category"] == "13"


def test_escalation_never_reads_past_the_rows_own_end(monkeypatch):
    """Evidence from the NEXT document would be a new way to get the category wrong."""
    se, seen = _categorize_capturing(monkeypatch, {})
    pages = {5: "p5", 6: "p6", 7: "SHOULD-NOT-BE-READ"}
    se._categorize("x.pdf", _row(5, 6), lambda p: pages.get(p, ""))

    assert seen[1:] == ["p5\np6"]


def test_a_single_page_row_still_reads_exactly_that_page(monkeypatch):
    se, seen = _categorize_capturing(monkeypatch, {})
    se._categorize("x.pdf", _row(9, 9), lambda p: {9: "p9", 10: "p10"}.get(p, ""))

    assert seen[1:] == ["p9"]


def test_a_blank_page_is_skipped_rather_than_truncating_the_evidence(monkeypatch):
    """Scanners emit blank backsides; a blank page 2 must not hide page 3 from the classifier."""
    se, seen = _categorize_capturing(monkeypatch, {})
    pages = {20: "p20", 21: "   ", 22: "p22"}
    se._categorize("x.pdf", _row(20, 40), lambda p: pages.get(p, ""))

    assert seen[1:] == ["p20\np22"]


def test_a_confident_title_reads_no_pages_at_all(monkeypatch):
    """The common case must be untouched: no page reads, so no added cost."""
    se, seen = _categorize_capturing(monkeypatch, {}, confident_on_title=True)
    reads = []

    def _page_text(page):
        reads.append(page)
        return "text"

    row = se._categorize("x.pdf", _row(1, 50), _page_text)

    assert reads == [], "a confident title must not trigger any page read"
    assert seen == [None]
    assert row["flag"] == "-"


def test_the_escalation_text_is_capped(monkeypatch):
    """llm_classify inlines this text whole and truncates nothing, so the cap has to hold here."""
    from app.services import segment_engine as se

    se_cap = se._ESCALATION_CHARS
    text = se._escalation_text("x.pdf", _row(1, 99), lambda p: "x" * se_cap)

    assert len(text) == se_cap


def test_an_unreadable_page_leaves_the_title_only_answer(monkeypatch):
    """A page-store failure must not fail the job: the row keeps its title-only category + flag."""
    se, seen = _categorize_capturing(monkeypatch, {})

    def _boom(page):
        raise RuntimeError("page store down")

    row = se._categorize("x.pdf", _row(3, 8), _boom)

    assert row["category"] == "100"
    assert row["flag"] == "x", "a low-confidence row still routes to human review"


def test_a_config_failure_in_the_escalation_is_not_swallowed(monkeypatch):
    """WHEN the escalation read fails because Tesseract is MISSING, THE SYSTEM SHALL propagate it.

    The broad catch here is RIGHT for a per-page failure: one unreadable page must not stop a document
    being categorized on its title alone. It is wrong for a config failure, which fails identically on
    EVERY row - so the whole document is quietly categorized title-only, and so is every document after
    it, leaving one WARNING per row and nothing that says the binary is missing.
    """
    from app.errors import OcrUnavailableError

    se, _seen = _categorize_capturing(monkeypatch, {})

    def missing_binary(page):
        raise OcrUnavailableError("no tesseract on this host")

    with pytest.raises(OcrUnavailableError):
        se._categorize("x.pdf", _row(5, 7), missing_binary)


def test_a_per_page_failure_in_the_escalation_still_falls_back_to_the_title(monkeypatch):
    """The other half of the pair, and the reason the fix above must be narrow: a TIMEOUT must still
    degrade to title-only rather than failing the document. Pinned so re-raising a config failure can
    never be widened into re-raising everything."""
    se, seen = _categorize_capturing(monkeypatch, {})

    def timed_out(page):
        raise RuntimeError("Tesseract process timeout")

    row = se._categorize("x.pdf", _row(5, 7), timed_out)
    assert seen == [None], "only the title-only classify should have run"
    assert row["category"] == "100"
