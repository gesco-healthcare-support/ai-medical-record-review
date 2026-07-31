"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image).

Config failures fail fast: if Tesseract or Poppler is missing, these raise OcrUnavailableError
instead of silently returning "" (an empty extraction previously starved summarization and
surfaced downstream as a cryptic Vertex "Model input cannot be empty" error). A single unreadable
page is still skipped so one bad page does not abort the whole document. TESSERACT_CMD (Windows
installs are often off PATH) is applied lazily on first use so importing this module needs no env.
Ported with main's PR #25 hardening (edd110f).
"""

import logging

import pytesseract
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError

from app.config import get_settings
from app.errors import OcrUnavailableError

logger = logging.getLogger(__name__)

_configured = False


def _ensure_tesseract() -> None:
    global _configured
    if not _configured:
        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        _configured = True


def _ocr_image(image) -> str:
    """OCR one page image within a wall-clock timeout (ocr_timeout_seconds).

    A missing Tesseract is a config failure -> OcrUnavailableError (fail-fast). A timeout or a
    tesseract execution error is a per-page failure -> logged and re-raised as a RuntimeError the
    per-page callers skip; pytesseract kills the tesseract subprocess, so a hung/oversized page can
    never block a worker thread forever (the concurrent-OCR deadlock backstop)."""
    _ensure_tesseract()
    try:
        return pytesseract.image_to_string(image, timeout=get_settings().ocr_timeout_seconds)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError(f"Tesseract not found: {exc}") from exc
    except RuntimeError as exc:
        logger.warning("OCR failed for a page (timeout or tesseract error): %s", exc)
        raise


def _rasterize(pdf_path, **kwargs):
    """Rasterize pages, mapping a missing/broken Poppler to OcrUnavailableError."""
    try:
        return convert_from_path(pdf_path, **kwargs)
    except (PDFInfoNotInstalledError, PDFPageCountError) as exc:
        raise OcrUnavailableError(f"Poppler (pdf2image) unavailable: {exc}") from exc


def extract_text_from_image(image) -> str:
    """OCR one already-rasterized page image (PIL)."""
    return _ocr_image(image)


def extract_text_from_selected_pages(pdf_path, selected_pages, *, mark_pages: bool = False) -> str:
    """OCR ``selected_pages`` into one string.

    ``mark_pages`` prefixes each page with ``Page <n>:`` - the ABSOLUTE record page, since
    ``selected_pages`` already carries absolute numbers. Depositions need it: their convention is one
    summary line per transcript page, and a model handed concatenated text cannot see where a page
    ends. Off by default, because those markers would otherwise reach every category's model input,
    and because the duplicate check feeds this text into similarity scoring where a shared
    ``Page 1: Page 2: ...`` vocabulary would make unrelated documents look alike.
    """
    extracted_text = ""
    for page_number in sorted(set(selected_pages)):
        try:
            images = _rasterize(pdf_path, first_page=page_number, last_page=page_number)
        except OcrUnavailableError:
            raise  # config failure: fail fast rather than silently return partial/empty text
        except Exception as exc:
            logger.warning(
                "could not rasterize page %s: %s", page_number, exc
            )  # skip, do not abort
            continue
        for page_image in images:
            try:
                page_text = _ocr_image(page_image)
            except OcrUnavailableError:
                raise  # Tesseract missing: fail fast
            except Exception as exc:
                logger.warning("OCR skipped page %s: %s", page_number, exc)  # timeout/bad page
                continue
            # Same marker shape as extract_text_from_all_pages, so both extractors read alike.
            extracted_text += f"Page {page_number}:\n{page_text}\n" if mark_pages else page_text
    return extracted_text


def extract_pages_with_report(pdf_path, selected_pages, *, retries: int = 1):
    """OCR ``selected_pages``, retrying pages that ERRORED, and report what each page did.

    Returns ``(text, report)`` where report is ``{"pages", "errored", "blank"}``: ``errored`` lists
    pages whose rasterize/OCR raised on every attempt, ``blank`` lists pages that read cleanly but
    carried no text.

    The distinction is the whole point. ``extract_text_from_selected_pages`` collapses both into a
    silent skip, so a row that produced no text is indistinguishable from a row nobody tried to read
    - which is how a dedup run that could not read a fifth of a document presented as a clean one.
    An errored page may be a transient Tesseract timeout worth one more attempt; a film,
    photograph or separator sheet is legitimately textless and no number of retries will yield
    words, so only the errors are retried.
    """
    pages = sorted(set(selected_pages))
    text, errored, blank = "", [], []
    for page_number in pages:
        page_text, failed = None, None
        for _ in range(max(1, retries + 1)):
            try:
                images = _rasterize(pdf_path, first_page=page_number, last_page=page_number)
                page_text = "".join(_ocr_image(image) for image in images)
                failed = None
                break
            except OcrUnavailableError:
                raise  # config failure (no Tesseract/Poppler): fail fast, never retry
            except Exception as exc:
                failed = exc
        if failed is not None:
            logger.warning(
                "OCR gave up on page %s after %d attempt(s): %s", page_number, retries + 1, failed
            )
            errored.append(page_number)
            continue
        if not (page_text or "").strip():
            blank.append(page_number)
        text += page_text or ""
    return text, {"pages": len(pages), "errored": errored, "blank": blank}


def extract_text_from_all_pages(pdf_path) -> str:
    extracted_text = ""
    try:
        images = _rasterize(pdf_path)
    except OcrUnavailableError:
        raise
    except Exception as exc:
        logger.warning("could not rasterize PDF: %s", exc)
        return extracted_text
    for page_number, page_image in enumerate(images, start=1):
        try:
            text = _ocr_image(page_image)
        except OcrUnavailableError:
            raise  # Tesseract missing: fail fast
        except Exception as exc:
            logger.warning("OCR skipped page %s: %s", page_number, exc)  # timeout/bad page
            text = ""
        extracted_text += f"Page {page_number}:\n{text}\n"
    return extracted_text
