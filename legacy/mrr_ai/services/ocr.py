"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image).

Config failures fail fast: if Tesseract or Poppler is missing, these raise OcrUnavailableError
instead of silently returning "" (an empty extraction previously starved summarization and
surfaced downstream as a cryptic Vertex "Model input cannot be empty" error). A single unreadable
page is still skipped so one bad page does not abort the whole document.
"""

import pytesseract
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError

from mrr_ai.config import TESSERACT_CMD
from mrr_ai.errors import OcrUnavailableError

# Tesseract is often installed but not on PATH (Windows installer default); an empty
# extraction here silently starves summarization, so honor the explicit override.
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def _ocr_image(image):
    """OCR one page image, mapping a missing Tesseract to OcrUnavailableError."""
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


def extract_text_from_image(image):
    """OCR one already-rasterized page image (PIL).

    Callers that need both the pixels and the text of a page rasterize once and reuse
    the image (see services/verify_pass.py) instead of paying double conversion.
    """
    return _ocr_image(image)


def extract_text_from_selected_pages(pdf_path, selected_pages):
    extracted_text = ""

    # Sort the selected pages to optimize page range extraction
    selected_pages = sorted(set(selected_pages))  # Ensure no duplicates and sort pages

    # Convert only the required pages to images
    for page_number in selected_pages:
        try:
            # Convert the specific page to an image (1-indexed)
            images = _rasterize(pdf_path, first_page=page_number, last_page=page_number)
        except OcrUnavailableError:
            raise  # config failure: fail fast rather than silently return partial/empty text
        except Exception as e:
            # A single unreadable page must not abort the whole document.
            print(f"Error processing page {page_number}: {e}")
            continue

        # Process the single image returned for this page
        for page_image in images:
            print(f"Processing page {page_number} using PyTesseract")
            extracted_text += _ocr_image(page_image)

    return extracted_text


def extract_text_from_all_pages(pdf_path):
    extracted_text = ""

    try:
        # Convert all pages to images
        images = _rasterize(pdf_path)
    except OcrUnavailableError:
        raise
    except Exception as e:
        print(f"Error processing PDF: {e}")
        return extracted_text

    # Process each page image
    for page_number, page_image in enumerate(images, start=1):
        print(f"Processing page {page_number} using PyTesseract")
        extracted_text += f"Page {page_number}:\n{_ocr_image(page_image)}\n"

    return extracted_text
