"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image).

The Tesseract binary is often installed but off PATH (Windows installer default); TESSERACT_CMD
overrides the lookup. It is applied lazily (first call) so importing this module needs no
environment. NOTE: extract_text_from_selected_pages swallows per-page errors and returns "" -
a known trap (a missing Tesseract silently starves summarization). main's fail-fast hardening
(errors.py / OcrUnavailableError, PR #25) should be ported here; flagged for the P3a check-in.
"""

import pytesseract
from pdf2image import convert_from_path

from app.config import get_settings

_configured = False


def _ensure_tesseract() -> None:
    global _configured
    if not _configured:
        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        _configured = True


def extract_text_from_image(image) -> str:
    """OCR one already-rasterized page image (PIL)."""
    _ensure_tesseract()
    return pytesseract.image_to_string(image)


def extract_text_from_selected_pages(pdf_path: str, selected_pages) -> str:
    _ensure_tesseract()
    extracted_text = ""
    for page_number in sorted(set(selected_pages)):
        try:
            images = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)
            for page_image in images:
                extracted_text += pytesseract.image_to_string(page_image)
        except Exception as exc:
            print(f"Error processing page {page_number}: {exc}")
    return extracted_text


def extract_text_from_all_pages(pdf_path: str) -> str:
    _ensure_tesseract()
    extracted_text = ""
    try:
        images = convert_from_path(pdf_path)
        for page_number, page_image in enumerate(images, start=1):
            extracted_text += f"Page {page_number}:\n{pytesseract.image_to_string(page_image)}\n"
    except Exception as exc:
        print(f"Error processing PDF: {exc}")
    return extracted_text
