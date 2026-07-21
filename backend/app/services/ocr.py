"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image).

Config failures fail fast: if Tesseract or Poppler is missing, these raise OcrUnavailableError
instead of silently returning "" (an empty extraction previously starved summarization and
surfaced downstream as a cryptic Vertex "Model input cannot be empty" error). A single unreadable
page is still skipped so one bad page does not abort the whole document. TESSERACT_CMD (Windows
installs are often off PATH) is applied lazily on first use so importing this module needs no env.
Ported with main's PR #25 hardening (edd110f).
"""

import pytesseract
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError

from app.config import get_settings
from app.errors import OcrUnavailableError

_configured = False


def _ensure_tesseract() -> None:
    global _configured
    if not _configured:
        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        _configured = True


def _ocr_image(image) -> str:
    """OCR one page image, mapping a missing Tesseract to OcrUnavailableError."""
    _ensure_tesseract()
    try:
        return pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError(f"Tesseract not found: {exc}") from exc


def _rasterize(pdf_path, **kwargs):
    """Rasterize pages, mapping a missing/broken Poppler to OcrUnavailableError."""
    try:
        return convert_from_path(pdf_path, **kwargs)
    except (PDFInfoNotInstalledError, PDFPageCountError) as exc:
        raise OcrUnavailableError(f"Poppler (pdf2image) unavailable: {exc}") from exc


def extract_text_from_image(image) -> str:
    """OCR one already-rasterized page image (PIL)."""
    return _ocr_image(image)


def extract_text_from_selected_pages(pdf_path, selected_pages) -> str:
    extracted_text = ""
    for page_number in sorted(set(selected_pages)):
        try:
            images = _rasterize(pdf_path, first_page=page_number, last_page=page_number)
        except OcrUnavailableError:
            raise  # config failure: fail fast rather than silently return partial/empty text
        except Exception as exc:
            print(f"Error processing page {page_number}: {exc}")  # one bad page must not abort
            continue
        for page_image in images:
            extracted_text += _ocr_image(page_image)
    return extracted_text


def extract_text_from_all_pages(pdf_path) -> str:
    extracted_text = ""
    try:
        images = _rasterize(pdf_path)
    except OcrUnavailableError:
        raise
    except Exception as exc:
        print(f"Error processing PDF: {exc}")
        return extracted_text
    for page_number, page_image in enumerate(images, start=1):
        extracted_text += f"Page {page_number}:\n{_ocr_image(page_image)}\n"
    return extracted_text
