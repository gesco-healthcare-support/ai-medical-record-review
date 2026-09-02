"""`extract_header` and its honesty about a partial OCR read (#211).

The four fields it produces - patient first/last name, DOB, law firm - are reviewer-facing on the
landing table and travel into the deliverable. It used to read through
`extract_text_from_selected_pages`, which catches a per-page Tesseract failure and continues, so a
dropped page was indistinguishable from a page with no words on it.

The two failures are not equally safe, which is the whole point:

* EVERY page fails -> empty text -> blanks -> the reviewer meets four empty fields and fills them
  in. Visible and recoverable.
* ONE page fails -> a header built from what survived, which LOOKS complete.

Vertex is stubbed throughout; no model call and no Tesseract.
"""

import logging
from types import SimpleNamespace

from app.services import extraction


def _stub_vertex(
    monkeypatch, payload='{"first_name": "A", "last_name": "B", "dob": "", "lawfirm": ""}'
):
    monkeypatch.setattr(extraction, "get_genai_client", lambda: object())
    monkeypatch.setattr(
        extraction,
        "generate_with_retry",
        lambda client, **kwargs: SimpleNamespace(text=payload),
    )


def _stub_ocr(monkeypatch, text, errored, pages=(1, 2, 3)):
    monkeypatch.setattr(
        extraction,
        "extract_pages_with_report",
        lambda pdf_path, p: (text, {"pages": list(pages), "errored": list(errored), "blank": []}),
    )


def test_a_partial_read_still_produces_a_header_but_says_so(monkeypatch, caplog):
    """The unsafe case. The header is still extracted - throwing away good text would be worse - but
    the pages that could not be read are named, so a wrong name or DOB is attributable afterwards."""
    _stub_vertex(monkeypatch)
    _stub_ocr(monkeypatch, "text of the pages that survived", errored=[2])

    with caplog.at_level(logging.WARNING):
        header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header["first_name"] == "A"  # still extracted
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "PARTIAL" in logged
    assert "[2]" in logged, "the errored page must be named, not merely counted"
    assert "1 of 3" in logged


def test_a_clean_read_says_nothing(monkeypatch, caplog):
    """A warning on every record would make the signal worthless."""
    _stub_vertex(monkeypatch)
    _stub_ocr(monkeypatch, "all of the text", errored=[])

    with caplog.at_level(logging.WARNING):
        header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header["first_name"] == "A"
    assert not [r for r in caplog.records if "PARTIAL" in r.getMessage()]


def test_a_total_failure_returns_blanks_without_calling_the_model(monkeypatch):
    """Unchanged behaviour, pinned: no text means no model call, and four empty fields the reviewer
    can see and fill in."""
    called = []
    monkeypatch.setattr(extraction, "get_genai_client", lambda: called.append("client"))
    monkeypatch.setattr(
        extraction,
        "generate_with_retry",
        lambda client, **kwargs: called.append("model") or SimpleNamespace(text="{}"),
    )
    _stub_ocr(monkeypatch, "   ", errored=[1, 2, 3])

    header = extraction.extract_header("x.pdf", [1, 2, 3])

    assert header == {"first_name": "", "last_name": "", "dob": "", "lawfirm": ""}
    assert called == [], "a blank read must not spend a Vertex call"


def test_a_malformed_model_reply_returns_blanks(monkeypatch):
    """Unchanged behaviour, pinned alongside the above so the partial-read change cannot disturb it."""
    _stub_vertex(monkeypatch, payload="not json at all")
    _stub_ocr(monkeypatch, "some text", errored=[])

    assert extraction.extract_header("x.pdf", [1]) == {
        "first_name": "",
        "last_name": "",
        "dob": "",
        "lawfirm": "",
    }


def test_the_log_line_carries_page_numbers_and_nothing_else(monkeypatch, caplog):
    """PHI guard: the OCR text is the one thing that must never reach a log line here, and this
    function holds a page of it at the moment it warns."""
    secret = "PATIENT JANE DOE DOB 01/02/1980 ACME LAW"
    _stub_vertex(monkeypatch)
    _stub_ocr(monkeypatch, secret, errored=[7])

    with caplog.at_level(logging.WARNING):
        extraction.extract_header("x.pdf", [7, 8])

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "[7]" in logged
    for fragment in ("JANE", "DOE", "01/02/1980", "ACME"):
        assert fragment not in logged, fragment
