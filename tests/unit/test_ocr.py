"""Unit tests for OCR extraction, with Tesseract/Poppler fully mocked."""

from mrr_ai.services import ocr as ocr_service
from mrr_ai.services.ocr import extract_text_from_all_pages, extract_text_from_selected_pages


def _stub_ocr(monkeypatch, images_per_call, text="TEXT"):
    """Make convert_from_path return fixed images and image_to_string a fixed string."""
    monkeypatch.setattr(ocr_service, "convert_from_path", lambda *a, **k: images_per_call)
    monkeypatch.setattr(ocr_service.pytesseract, "image_to_string", lambda image: text)


def test_extract_selected_pages_concatenates_text(monkeypatch):
    _stub_ocr(monkeypatch, images_per_call=["img"], text="TEXT")
    # Two selected pages, one image each -> "TEXT" twice, no page labels on this path.
    assert extract_text_from_selected_pages("/tmp/x.pdf", [1, 2]) == "TEXTTEXT"


def test_extract_selected_pages_swallows_per_page_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("poppler missing")

    monkeypatch.setattr(ocr_service, "convert_from_path", boom)
    assert extract_text_from_selected_pages("/tmp/x.pdf", [1]) == ""


def test_extract_all_pages_labels_each_page(monkeypatch):
    _stub_ocr(monkeypatch, images_per_call=["img1", "img2"], text="TEXT")
    assert extract_text_from_all_pages("/tmp/x.pdf") == "Page 1:\nTEXT\nPage 2:\nTEXT\n"


def test_extract_all_pages_swallows_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(ocr_service, "convert_from_path", boom)
    assert extract_text_from_all_pages("/tmp/x.pdf") == ""
