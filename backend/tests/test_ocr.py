"""OCR bounding + per-page resilience (pipeline forever-hang fix).

_ocr_image passes a wall-clock timeout to Tesseract; a timeout is a skippable per-page failure
(RuntimeError), NOT the fail-fast OcrUnavailableError (which means Tesseract/Poppler is missing).
The per-page extraction loops log and skip a failed page rather than aborting the document.
"""

import pytest

from app.errors import OcrUnavailableError
from app.services import ocr


class _Sentinel:
    """Stand-in for a rasterized PIL page (image_to_string is monkeypatched, so identity suffices)."""


def test_ocr_image_forwards_timeout(monkeypatch):
    captured = {}

    def fake_image_to_string(image, timeout=0):
        captured["timeout"] = timeout
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    ocr._configured = True  # skip _ensure_tesseract's settings read

    assert ocr._ocr_image(_Sentinel()) == "text"
    assert captured["timeout"] == ocr.get_settings().ocr_timeout_seconds == 120


def test_ocr_image_timeout_raises_runtimeerror_not_unavailable(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    ocr._configured = True

    with pytest.raises(RuntimeError) as excinfo:
        ocr._ocr_image(_Sentinel())
    assert not isinstance(excinfo.value, OcrUnavailableError)


def test_selected_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    ocr._configured = True

    # A per-page OCR timeout must be logged + skipped, never propagate out of the loop.
    assert ocr.extract_text_from_selected_pages("dummy.pdf", [1, 2]) == ""


def test_selected_pages_marks_absolute_page_numbers_when_asked(monkeypatch):
    # Depositions are summarised one line per transcript page, so the model has to SEE where each
    # page ends. The markers carry the ABSOLUTE record page, not a 1-based offset within the range.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    ocr._configured = True

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [143, 144], mark_pages=True)
    assert out == "Page 143:\nbody\nPage 144:\nbody\n"


def test_selected_pages_is_unmarked_by_default(monkeypatch):
    # Every existing caller (the duplicate check, and summarization for every category except 9) must
    # be byte-for-byte unchanged: markers in the dedup text would pollute similarity scoring, and in
    # other categories they would push page numbers into ordinary summaries.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    ocr._configured = True

    assert ocr.extract_text_from_selected_pages("dummy.pdf", [143, 144]) == "bodybody"
    assert "Page" not in ocr.extract_text_from_selected_pages("dummy.pdf", [143])


def test_marked_page_is_skipped_without_losing_the_following_markers(monkeypatch):
    # A page whose OCR times out must not emit a marker with no body attached, and must not stop the
    # remaining pages from being marked.
    calls = {"n": 0}

    def flaky(image, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Tesseract process timeout")
        return "body"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", flaky)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    ocr._configured = True

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [7, 8], mark_pages=True)
    assert "Page 7:" not in out  # the failed page contributes nothing at all
    assert out == "Page 8:\nbody\n"


def test_all_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel(), _Sentinel()])
    ocr._configured = True

    # Page headers are still emitted; the unreadable body is skipped without aborting.
    out = ocr.extract_text_from_all_pages("dummy.pdf")
    assert "Page 1:" in out and "Page 2:" in out


def test_tesseract_missing_still_fails_fast(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    ocr._configured = True

    with pytest.raises(OcrUnavailableError):
        ocr._ocr_image(_Sentinel())


class _Page:
    """A rasterized page that remembers which page it is, so a fake OCR can behave per page."""

    def __init__(self, page):
        self.page = page


def _per_page_rasterize(path, first_page, last_page):
    return [_Page(first_page)]


def test_report_separates_pages_that_errored_from_pages_that_read_blank(monkeypatch):
    """WHEN a page errors and another reads cleanly with no words, THE SYSTEM SHALL report them apart.

    extract_text_from_selected_pages collapses both into a silent skip, which is why a dedup run that
    could not read a fifth of a record was indistinguishable from one with nothing to find. The two
    have different causes: an error may be a transient timeout, while a film, photograph or blank
    separator sheet is legitimately textless and no retry will produce words.
    """

    def by_page(image, timeout=0):
        if image.page == 1:
            raise RuntimeError("Tesseract process timeout")
        return "" if image.page == 2 else "real body text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", by_page)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    ocr._configured = True

    text, report = ocr.extract_pages_with_report("dummy.pdf", [1, 2, 3], retries=0)
    assert text == "real body text"
    assert report == {"pages": 3, "errored": [1], "blank": [2]}


def test_report_retries_only_the_errored_page(monkeypatch):
    """A retry is for a transient failure. A page that read cleanly and held no words must NOT be
    re-OCR'd: there is nothing to recover, and each attempt costs a rasterize plus a Tesseract run."""
    attempts = []

    def flaky(image, timeout=0):
        attempts.append(image.page)
        if image.page == 1 and attempts.count(1) == 1:
            raise RuntimeError("Tesseract process timeout")
        return "" if image.page == 2 else "body"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", flaky)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    ocr._configured = True

    text, report = ocr.extract_pages_with_report("dummy.pdf", [1, 2], retries=1)
    assert attempts == [1, 1, 2]  # page 1 retried and recovered; page 2 read once
    assert text == "body"
    assert report == {"pages": 2, "errored": [], "blank": [2]}


def test_report_fails_fast_when_tesseract_is_missing(monkeypatch):
    """A config failure is not a per-page problem: retrying it would burn the budget on every page of
    the document and still produce nothing."""

    def missing(image, timeout=0):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", missing)
    monkeypatch.setattr(ocr, "_rasterize", _per_page_rasterize)
    ocr._configured = True

    with pytest.raises(OcrUnavailableError):
        ocr.extract_pages_with_report("dummy.pdf", [1, 2])
