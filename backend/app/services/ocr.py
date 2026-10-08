"""OCR text extraction via Tesseract (pages rasterized by Poppler/pdf2image).

Config failures fail fast: if Tesseract or Poppler is missing, these raise OcrUnavailableError
instead of silently returning "" (an empty extraction previously starved summarization and
surfaced downstream as a cryptic Vertex "Model input cannot be empty" error). A single unreadable
page is still skipped so one bad page does not abort the whole document. TESSERACT_CMD (Windows
installs are often off PATH) is applied lazily on first use so importing this module needs no env.
Ported with main's PR #25 hardening (edd110f).

WHY EVERY PAGE IS RASTERIZED AND OCR'D, EVEN WHEN THE PDF ALREADY CARRIES A TEXT LAYER
---------------------------------------------------------------------------------------
Most of these records are not photographs of paper - they were generated digitally, or a document
system OCR'd them before we received them - so `pypdf`'s `extract_text()` returns something for many
pages, and reading it costs ~0.04s against ~2.0s to rasterize and run Tesseract. Since OCR is roughly
HALF the wall-clock of a segment job (one 297-page record segmented three times on 2026-08-17:
1,463s, then 810s and 715s once its page text was stored), that looks like the largest speed lever in
the pipeline. It is not usable, and it was measured rather than argued.

Measured 2026-08-24 over 35 distinct records on the box, ~1,050 sampled pages, comparing the layer
against Tesseract by WORD RECALL - the share of Tesseract's words that appear in the layer:

* Of the pages carrying a layer, only ~64% match closely. **~18% are missing more than 30% of the
  words Tesseract finds.** The worst records are a fax header stamp over a scanned page: median
  recall 0.013 and 0.027, holding 5% and 2% of OCR's characters.
* **No character floor separates them.** Pages with 900-2000 characters of layer agree 60% of the
  time; pages with 2000+ agree 70%. The curve rises and plateaus well short of safe.
* Measured in BOTH directions to test whether Tesseract is even the right yardstick - on a digital
  PDF the layer is the original and OCR is a lossy re-read, so a disagreement could mean OCR invented
  tokens from scan speckle. It does not: of the disagreeing pages above a 150-char floor, 25.8% are
  the layer genuinely missing content and only 1.5% are OCR noise.
* A per-document calibration gate strict enough to be safe trusts **2 of 35 records**; relaxed to 80%
  agreement it trusts 4. Safe and worthless, or useful and lossy.

So the cost is largely unavoidable, and it does not go away when this pipeline stops paying a vendor
per document - OCR is CPU work that no GPU purchase makes cheaper.

**If you pick this up again, measure word recall in both directions, not character volume.** Character
volume agreed 96-101% on records whose word recall was far worse, and reading volume as agreement is
exactly what made this look like a 50x win. See also `ocr_max_long_edge_px` in config, which records
the other rejected OCR speed lever - capping the DPI was 4.2x faster and lost 6.0% of the characters.
"""

import logging
import re
from functools import lru_cache
from typing import NotRequired, TypedDict

import pytesseract
from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
from pypdf import PdfReader

from app.config import get_settings
from app.errors import OcrUnavailableError, PdfUnreadableError

logger = logging.getLogger(__name__)

_configured = False


class _TesseractArgs(TypedDict):
    """The keyword arguments `ocr_image` passes to `pytesseract.image_to_string`. `config` is sent only when
    the page's DPI differs from the base, so a base-DPI call carries no config at all."""

    timeout: int
    config: NotRequired[str]


def _ensure_tesseract() -> None:
    global _configured
    if not _configured:
        cmd = get_settings().tesseract_cmd
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        _configured = True


def _ocr_image(image, dpi=None) -> str:
    """OCR one page image within a wall-clock timeout (ocr_timeout_seconds).

    A missing Tesseract is a config failure -> OcrUnavailableError (fail-fast). A timeout or a
    tesseract execution error is a per-page failure -> logged and re-raised as a RuntimeError the
    per-page callers skip; pytesseract kills the tesseract subprocess, so a hung/oversized page can
    never block a worker thread forever (the concurrent-OCR deadlock backstop).

    A REDUCED ``dpi`` is declared to Tesseract via --dpi, and that is not cosmetic: Tesseract judges
    x-height from the DPI, so an image rendered below the base must say so or recognition degrades.
    Falls back to the value ``_rasterize`` recorded on the image, so no caller threads it through.

    At the base DPI the flag is deliberately OMITTED rather than passed as `--dpi 200`. Measured
    2026-08-19: passing it changed the text of a page whose resolution had not changed at all, which
    would have silently altered ~90% of stored OCR output for no measured gain. Declaring the DPI is a
    correction owed only where the resolution was actually lowered.
    """
    _ensure_tesseract()
    settings = get_settings()
    if dpi is None:
        recorded = (getattr(image, "info", None) or {}).get("dpi")
        if isinstance(recorded, (tuple, list)):  # PIL stores (x_dpi, y_dpi)
            recorded = recorded[0] if recorded else None
        if isinstance(recorded, (int, float)):
            dpi = recorded
    kwargs: _TesseractArgs = {"timeout": settings.ocr_timeout_seconds}
    if dpi and int(round(float(dpi))) != int(settings.ocr_base_dpi):
        kwargs["config"] = f"--dpi {int(round(float(dpi)))}"
    try:
        text = pytesseract.image_to_string(image, **kwargs)
        if text.strip():
            return text
        return _single_block_fallback(image, kwargs) or text
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrUnavailableError(f"Tesseract not found: {exc}") from exc
    except RuntimeError as exc:
        logger.warning("OCR failed for a page (timeout or tesseract error): %s", exc)
        raise


# A page the default layout pass read as EMPTY is read again as one block of text (--psm 6), and
# that second reading is kept only when it holds at least this many words of three or more letters.
#
# Why: Tesseract's default page segmentation (--psm 3) finds no text blocks at all on some grey,
# speckled form scans and returns nothing, while --psm 6 reads the same page. Reviewer feedback
# 2026-10-05: a handwritten intake questionnaire was "refused outright" - eight pages read as empty,
# so summarize_row raised EmptyExtractionError and no model, Gemini or ours, ever saw the pages.
#
# Measured on the test box 2026-10-05, every page stored with empty text (13 pages, 5 records):
# the eight questionnaire pages gave 24-89 words, two pages of other records 12 and 49, and the
# genuinely blank ones 0 or 1-5 stray tokens. The floor keeps those last ones blank, which preserves
# the rule that a page read cleanly with no words stays silent rather than reaching a model.
#
# Only a page that read EMPTY is retried, so every page that produces text today is unchanged -
# the same reason the base-DPI path omits --dpi (see _ocr_image).
_FALLBACK_MIN_WORDS = 5
_FALLBACK_WORD = re.compile(r"[A-Za-z]{3,}")


def _single_block_fallback(image, kwargs: _TesseractArgs) -> str:
    """Re-read an empty page as one block of text; "" unless it finds enough real words."""
    config = " ".join(part for part in (kwargs.get("config", ""), "--psm 6") if part)
    text = pytesseract.image_to_string(image, timeout=kwargs["timeout"], config=config)
    if len(_FALLBACK_WORD.findall(text)) < _FALLBACK_MIN_WORDS:
        return ""
    logger.info("OCR layout pass found no text; single-block pass read %d chars", len(text))
    return text


@lru_cache(maxsize=64)
def _page_long_edges_pt(pdf_path) -> tuple[float, ...]:
    """Long edge of every page in points, cached per file.

    One parse per document rather than per page: an upload is immutable, and a fresh PdfReader on a
    335-page file costs enough that doing it per page would add tens of seconds to a full population.
    An unreadable box is not fatal - callers fall back to the base DPI.
    """
    try:
        reader = PdfReader(pdf_path)
        return tuple(max(float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages)
    except Exception as exc:
        logger.warning("could not read page sizes, falling back to the base DPI: %s", exc)
        return ()


def _dpi_for_page(pdf_path, page) -> int:
    """DPI for one page: the base, optionally lowered to keep the render within ocr_max_long_edge_px.

    CAP-ONLY - the DPI is never raised, so an ordinary page renders exactly as it always did.

    The cap is DISABLED by default (ocr_max_long_edge_px = 0) on measurement, not on principle. Capping
    a 2700pt page to 3500px made OCR 4.2x faster and lost 6.0% of its characters; see the note on the
    setting for why a higher cap did not recover them, and what would have to be measured to enable it.
    """
    settings = get_settings()
    base = int(settings.ocr_base_dpi)
    cap = int(settings.ocr_max_long_edge_px)
    if cap <= 0 or page is None:
        return base  # capping off, or a whole-document call where one DPI serves every page
    edges = _page_long_edges_pt(pdf_path)
    if not edges or page < 1 or page > len(edges) or edges[page - 1] <= 0:
        return base
    return max(1, min(base, int(cap * 72 / edges[page - 1])))


def _rasterize(pdf_path, **kwargs):
    """Rasterize pages, separating a missing Poppler from a PDF that cannot be opened.

    Picks the DPI when the caller did not (see ``_dpi_for_page``) and records it on every image, so
    ``_ocr_image`` can declare the same value to Tesseract without any call site threading it through.
    """
    kwargs.setdefault("dpi", _dpi_for_page(pdf_path, kwargs.get("first_page")))
    dpi = kwargs["dpi"]
    try:
        images = convert_from_path(pdf_path, **kwargs)
    except PDFInfoNotInstalledError as exc:
        # The binary really is absent: a server problem, identical on every document.
        raise OcrUnavailableError(f"Poppler (pdf2image) unavailable: {exc}") from exc
    except PDFPageCountError as exc:
        # NOT a config problem, though it was reported as one until #201. `pdfinfo` raises this
        # whenever it cannot read a page count - a corrupt or encrypted upload, a truncated file, a
        # deleted path, an unmounted volume - all of which happen on a perfectly healthy install.
        # Labelling it "Poppler unavailable" sends the operator to check a binary that is fine.
        raise PdfUnreadableError(f"cannot read the PDF ({pdf_path}): {exc}") from exc
    for image in images:
        image.info["dpi"] = (dpi, dpi)
    return images


def extract_text_from_image(image) -> str:
    """OCR one already-rasterized page image (PIL)."""
    return _ocr_image(image)


FRONT_MATTER_MARKER = "Front matter (before transcript page 1, no page number):"


def page_marker(page_number: int, page_label_offset: int = 0) -> str:
    """The ``Page <n>:`` line that opens a marked page, or the front-matter line (#259).

    A deposition is labelled with the transcript's OWN printed numbers (``page_label_offset``, from
    `deposition_pages.transcript_page_offset`), and `_deposition_pages_block` tells the model those
    markers ARE the printed numbers and to cite them exactly. A page that comes BEFORE the
    transcript's page 1 - a cover, caption or appearance page - has no printed number, and the offset
    derivation already skips such pages for exactly that reason. Shifting it anyway labelled it
    ``Page 0:`` (or below), a number that does not exist in the transcript, under an instruction to
    cite it. So a label at or below zero becomes a marker that says what the page is instead.

    Record pages start at 1, so with no offset this never fires: every non-deposition caller gets the
    marker it always got.
    """
    label = page_number + page_label_offset
    return f"Page {label}:" if label > 0 else FRONT_MATTER_MARKER


def _ocr_page_images(images, page_number: int, page_label_offset: int, mark_pages: bool) -> str:
    """OCR every image rasterized from one record page, concatenated.

    The ``except`` ORDER here is load-bearing and must not be reordered: ``PdfUnreadableError``
    subclasses ``OcrUnavailableError`` (app/errors.py), so the fail-fast arm has to come first.
    Swapping them would turn a configuration failure into a silently skipped page, which is the
    exact outcome the fail-fast arm exists to prevent. The ``raise`` propagates out of this helper
    to the same caller it reached before the extraction.
    """
    text = ""
    for page_image in images:
        try:
            page_text = _ocr_image(page_image)
        except OcrUnavailableError:
            raise  # Tesseract missing: fail fast
        except Exception as exc:
            logger.warning("OCR skipped page %s: %s", page_number, exc)  # timeout/bad page
            continue
        # Same marker shape as extract_text_from_all_pages, so both extractors read alike.
        marker = page_marker(page_number, page_label_offset)
        text += f"{marker}\n{page_text}\n" if mark_pages else page_text
    return text


def extract_text_from_selected_pages(
    pdf_path, selected_pages, *, mark_pages: bool = False, page_label_offset: int = 0
) -> str:
    """OCR ``selected_pages`` into one string.

    ``mark_pages`` prefixes each page with ``Page <n>:``. Depositions need it: they are summarized in
    page groups, and a model handed concatenated text cannot see where a page ends. Off by default,
    because those markers would otherwise reach every category's model input, and because the
    duplicate check feeds this text into similarity scoring where a shared ``Page 1: Page 2: ...``
    vocabulary would make unrelated documents look alike.

    ``page_label_offset`` is ADDED to the record page number in the marker, so a deposition can be
    labelled with the transcript's OWN printed page numbers instead of positions in our scanned file
    (see services/deposition_pages). Default 0 keeps the marker at the absolute record page, which is
    what every existing caller expects. The offset is applied ONLY to the label - `selected_pages`,
    the rasterizing and the log lines all stay on real record pages, so a skipped page is still
    reported by the number that identifies it in the file.
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
        extracted_text += _ocr_page_images(images, page_number, page_label_offset, mark_pages)
    return extracted_text


def _ocr_page_with_retries(pdf_path, page_number: int, retries: int):
    """OCR one page, retrying only ERRORS. Returns ``(text, failure)``; exactly one is None.

    ``OcrUnavailableError`` is re-raised rather than retried and rather than reported as a failure:
    it means Tesseract or Poppler is missing, so no number of attempts can succeed and every other
    page would fail identically. Note ``PdfUnreadableError`` subclasses it, so this arm catches
    both - it must stay ahead of the general handler below it.

    A blank page is NOT a failure here. It returns ("", None), and the caller decides whether an
    empty read means blank; only errors are retried, because a film or separator sheet is
    legitimately textless and retrying it just costs time.
    """
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
    return page_text, failed


def extract_pages_with_report(
    pdf_path,
    selected_pages,
    *,
    retries: int = 1,
    mark_pages: bool = False,
    page_label_offset: int = 0,
):
    """OCR ``selected_pages``, retrying pages that ERRORED, and report what each page did.

    Returns ``(text, report)`` where report is ``{"pages", "errored", "blank"}``. All three are
    LISTS of page numbers: ``pages`` is what was attempted, ``errored`` those whose rasterize/OCR
    raised on every attempt, ``blank`` those that read cleanly but carried no text.

    ``pages`` was a COUNT here and a LIST in `page_text.get_row_text_with_report`, whose docstring
    asserted the two contracts matched "exactly" (#210). Nothing read the field, so nothing was
    broken - but a docstring that states the false thing is worse than one that says nothing: the
    next caller writes `for page in report["pages"]` and gets a silent iteration over an integer or
    a TypeError depending on which of the two functions they happened to call. A list is the right
    shape of the two, because it is the shape of its siblings and `len()` recovers the count.

    The distinction is the whole point. ``extract_text_from_selected_pages`` collapses both into a
    silent skip, so a row that produced no text is indistinguishable from a row nobody tried to read
    - which is how a dedup run that could not read a fifth of a document presented as a clean one.
    An errored page may be a transient Tesseract timeout worth one more attempt; a film,
    photograph or separator sheet is legitimately textless and no number of retries will yield
    words, so only the errors are retried.

    ``mark_pages`` / ``page_label_offset`` mean exactly what they mean on
    ``extract_text_from_selected_pages``, and exist here so a caller that needs the report does not
    have to give up page markers to get it - summarize reads depositions through this path, and a
    transcript model handed concatenated text cannot see where a page ends. The ``blank`` test reads
    the RAW page text, never the marked string: a marker is text, so testing after prefixing would
    report every page as non-blank and quietly empty that list.
    """
    pages = sorted(set(selected_pages))
    text, errored, blank = "", [], []
    for page_number in pages:
        page_text, failed = _ocr_page_with_retries(pdf_path, page_number, retries)
        if failed is not None:
            logger.warning(
                "OCR gave up on page %s after %d attempt(s): %s", page_number, retries + 1, failed
            )
            errored.append(page_number)
            continue
        if not (page_text or "").strip():
            blank.append(page_number)
        if mark_pages:
            text += f"{page_marker(page_number, page_label_offset)}\n{page_text or ''}\n"
        else:
            text += page_text or ""
    return text, {"pages": pages, "errored": errored, "blank": blank}


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
