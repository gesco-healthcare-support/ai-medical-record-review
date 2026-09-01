"""OCR bounding + per-page resilience (pipeline forever-hang fix).

_ocr_image passes a wall-clock timeout to Tesseract; a timeout is a skippable per-page failure
(RuntimeError), NOT the fail-fast OcrUnavailableError (which means Tesseract/Poppler is missing).
The per-page extraction loops log and skip a failed page rather than aborting the document.
"""

from types import SimpleNamespace

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
    monkeypatch.setattr(ocr, "_configured", True)  # skip _ensure_tesseract's settings read

    assert ocr._ocr_image(_Sentinel()) == "text"
    assert captured["timeout"] == ocr.get_settings().ocr_timeout_seconds == 120


def test_ocr_image_timeout_raises_runtimeerror_not_unavailable(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    sentinel = _Sentinel()
    with pytest.raises(RuntimeError) as excinfo:
        ocr._ocr_image(sentinel)
    assert not isinstance(excinfo.value, OcrUnavailableError)


def test_selected_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    # A per-page OCR timeout must be logged + skipped, never propagate out of the loop.
    assert ocr.extract_text_from_selected_pages("dummy.pdf", [1, 2]) == ""


def test_selected_pages_marks_absolute_page_numbers_when_asked(monkeypatch):
    # Depositions are summarised one line per transcript page, so the model has to SEE where each
    # page ends. The markers carry the ABSOLUTE record page, not a 1-based offset within the range.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [143, 144], mark_pages=True)
    assert out == "Page 143:\nbody\nPage 144:\nbody\n"


def test_selected_pages_is_unmarked_by_default(monkeypatch):
    # Every existing caller (the duplicate check, and summarization for every category except 9) must
    # be byte-for-byte unchanged: markers in the dedup text would pollute similarity scoring, and in
    # other categories they would push page numbers into ordinary summaries.
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda image, timeout=0: "body")
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

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
    monkeypatch.setattr(ocr, "_configured", True)

    out = ocr.extract_text_from_selected_pages("dummy.pdf", [7, 8], mark_pages=True)
    assert "Page 7:" not in out  # the failed page contributes nothing at all
    assert out == "Page 8:\nbody\n"


def test_all_pages_skips_failing_page(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise RuntimeError("Tesseract process timeout")

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_rasterize", lambda *a, **k: [_Sentinel(), _Sentinel()])
    monkeypatch.setattr(ocr, "_configured", True)

    # Page headers are still emitted; the unreadable body is skipped without aborting.
    out = ocr.extract_text_from_all_pages("dummy.pdf")
    assert "Page 1:" in out
    assert "Page 2:" in out


def test_tesseract_missing_still_fails_fast(monkeypatch):
    def fake_image_to_string(image, timeout=0):
        raise ocr.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    sentinel = _Sentinel()
    with pytest.raises(OcrUnavailableError):
        ocr._ocr_image(sentinel)


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
    monkeypatch.setattr(ocr, "_configured", True)

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
    monkeypatch.setattr(ocr, "_configured", True)

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
    monkeypatch.setattr(ocr, "_configured", True)

    with pytest.raises(OcrUnavailableError):
        ocr.extract_pages_with_report("dummy.pdf", [1, 2])


def _with_cap(monkeypatch, cap):
    """Point ocr.get_settings() at a copy carrying `cap`, since the real default disables capping."""
    real = ocr.get_settings()
    stub = SimpleNamespace(
        ocr_base_dpi=real.ocr_base_dpi,
        ocr_max_long_edge_px=cap,
        ocr_timeout_seconds=real.ocr_timeout_seconds,
        tesseract_cmd=real.tesseract_cmd,
    )
    monkeypatch.setattr(ocr, "get_settings", lambda: stub)
    return stub


def test_capping_is_disabled_by_default(monkeypatch):
    """The default must not change any rendering. Capping was MEASURED and rejected: it made an
    oversized page 4.2x faster and cost 6.0% of its recognized characters, so it ships off until a
    word-level quality metric exists to judge it (see the note on ocr_max_long_edge_px)."""
    assert ocr.get_settings().ocr_max_long_edge_px == 0
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: (3455.0,))
    assert ocr._dpi_for_page("/x.pdf", 1) == ocr.get_settings().ocr_base_dpi


def test_dpi_is_capped_for_oversized_pages_and_left_alone_otherwise(monkeypatch):
    """WHEN capping is enabled and a page would exceed it, THE SYSTEM SHALL lower the DPI to fit.

    Cap-only is the safety property: an ordinary page must render EXACTLY as before, so its stored OCR
    text cannot change. Only oversized pages move.
    """
    settings = _with_cap(monkeypatch, 3500)
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: (792.0, 3455.0))

    # 792pt at 200 DPI is 2200px - already inside the cap, so untouched.
    assert ocr._dpi_for_page("/x.pdf", 1) == settings.ocr_base_dpi

    # 3455pt at 200 DPI would be 9598px - capped, and the result must actually fit.
    capped = ocr._dpi_for_page("/x.pdf", 2)
    assert capped < settings.ocr_base_dpi
    assert 3455.0 * capped / 72 <= settings.ocr_max_long_edge_px


def test_unknown_page_sizes_fall_back_to_the_base_dpi(monkeypatch):
    """An unreadable page box must not stop OCR - it just means the cap cannot be applied."""
    settings = _with_cap(monkeypatch, 3500)
    monkeypatch.setattr(ocr, "_page_long_edges_pt", lambda path: ())
    assert ocr._dpi_for_page("/x.pdf", 1) == settings.ocr_base_dpi
    # A whole-document rasterize has no single page to size against.
    assert ocr._dpi_for_page("/x.pdf", None) == settings.ocr_base_dpi


def test_the_rendered_dpi_is_declared_to_tesseract(monkeypatch):
    """Tesseract scales x-height decisions by the DPI it is told, so a reduced-DPI image that does not
    declare itself risks WORSE recognition - which would silently undo the point of the cap."""
    captured = {}

    def fake_image_to_string(image, timeout=0, config=""):
        captured["config"] = config
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    class _Rendered:
        info = {"dpi": (80, 80)}

    assert ocr._ocr_image(_Rendered()) == "text"
    assert captured["config"] == "--dpi 80"


def test_an_image_without_recorded_dpi_passes_no_config(monkeypatch):
    """Stubbed rasterizers hand back objects with no `.info`; that must not become a crash or a
    bogus `--dpi 0`."""
    captured = {}

    def fake_image_to_string(image, timeout=0):
        captured["called"] = True
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)

    assert ocr._ocr_image(_Sentinel()) == "text"
    assert captured["called"] is True


def test_the_base_dpi_is_not_declared_so_ordinary_pages_are_unchanged(monkeypatch):
    """WHEN a page renders at the base DPI, THE SYSTEM SHALL pass no --dpi flag.

    Measured 2026-08-19: passing `--dpi 200` changed the recognized text of a page whose resolution had
    not changed at all. Since ordinary pages are already inside the pixel cap, declaring the DPI there
    would silently alter most stored OCR output for no measured gain - so the flag is reserved for the
    pages whose DPI was actually lowered.
    """
    captured = {}

    def fake_image_to_string(image, timeout=0, **kwargs):
        captured.update(kwargs)
        return "text"

    monkeypatch.setattr(ocr.pytesseract, "image_to_string", fake_image_to_string)
    monkeypatch.setattr(ocr, "_configured", True)
    base = ocr.get_settings().ocr_base_dpi

    class _AtBase:
        info = {"dpi": (base, base)}

    class _Capped:
        info = {"dpi": (72, 72)}

    assert ocr._ocr_image(_AtBase()) == "text"
    assert "config" not in captured, "the base DPI must not be declared"

    captured.clear()
    assert ocr._ocr_image(_Capped()) == "text"
    assert captured.get("config") == "--dpi 72"
