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
