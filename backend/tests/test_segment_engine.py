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
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row: row)
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
    monkeypatch.setattr(se, "_categorize", lambda pdf_path, row: row)
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
