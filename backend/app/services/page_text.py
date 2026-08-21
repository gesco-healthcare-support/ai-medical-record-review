"""One-time per-page OCR, stored and reused by every stage.

The pipeline used to OCR the same page up to four times per document (segmentation's classification
escalation, classify, dedup, and summarize's fallback), and threw all of it away on a re-run. This
module extracts each page once into `page_texts` and hands it back by page number.

Two design points worth keeping:

* **Page-keyed, not row-keyed.** `review_rows.source_text` is attached to a row, and a row's identity
  changes the moment a reviewer merges or splits it. Page numbers are stable, so this cache survives
  reviewer edits and re-segmentation - which is what makes it reusable at all.
* **A cache, not a source of truth.** Every entry is reproducible from the PDF. A miss is filled
  transparently, so a caller never has to know whether population has run.

Concurrency: population uses a small thread pool because Tesseract is the slow part. It must stay
modest - `OMP_THREAD_LIMIT=1` in compose is what stops concurrent tesseract processes deadlocking on
a shared CPU, and the box also runs the Vertex pacing work.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.errors import OcrUnavailableError
from app.models import PageText
from app.services.ocr import extract_pages_with_report

logger = logging.getLogger(__name__)

ENGINE_TESSERACT = "tesseract"


def _extract(pdf_path, page: int) -> tuple[str, bool]:
    """OCR exactly one page -> (text, ok).

    ``ok`` is False only when extraction ITSELF failed. A successful read of a blank page returns
    ("", True), because that page will never yield words however often it is retried, while a failed
    read may well succeed next time. Callers that surface "unreadable" rows to a reviewer, and any
    comparison between OCR engines, both depend on telling those apart.

    Reads through ``extract_pages_with_report``, NOT ``extract_text_from_selected_pages``. The latter
    catches a per-page Tesseract timeout and continues, so it returns empty text and raises nothing -
    which arrived here as ("", True) and stored a page nobody could read as a legitimately blank one.
    The reporting variant names the pages that failed, and that name is the only thing that keeps the
    errored/blank distinction alive on this path.
    """
    try:
        text, report = extract_pages_with_report(pdf_path, [page])
    except OcrUnavailableError:
        # A MISSING Tesseract or Poppler is a CONFIG failure, and `ocr` raises a distinct type
        # precisely to say so ("A missing Tesseract is a config failure -> fail-fast"). Catching it
        # with everything else erased that distinction one layer above the place that drew it: it
        # became ("", False), identical to a timeout.
        #
        # Harmless while a stored failure was permanent. NOT harmless once failures became retryable
        # (get_page_text re-extracts an errored row, populate_document counts only extract_ok=True as
        # done): with no binary present every page of every document is marked failed, and every later
        # run re-attempts every page against something that cannot succeed. Wasted once became wasted
        # every run.
        #
        # Not hypothetical - a missing Tesseract has produced empty OCR here before and surfaced
        # downstream as a Vertex 400 naming nothing about OCR at all.
        raise
    except Exception:
        # No page content in the log line: this text is PHI-bearing.
        logger.warning("page OCR failed for page %s", page, exc_info=True)
        return "", False
    return (text or ""), page not in report["errored"]


def _store(session, document_id: str, page: int, text: str, ok: bool = True) -> None:
    """Insert one page, tolerating a concurrent writer.

    The unique constraint on (document_id, page) is the real guard: two workers can populate the same
    document at once (a re-segment while a dedup is finishing), and losing that race is harmless -
    the other writer stored the same text.

    One case is NOT harmless: the existing row may be a stored FAILURE while this call carries a
    successful read - which is exactly what a retry of a timed-out page looks like. Discarding that
    would make the failure permanent, so a success replaces an unsuccessful row.
    """
    session.add(
        PageText(
            document_id=document_id,
            page=page,
            text=text,
            ocr_engine=ENGINE_TESSERACT,
            extract_ok=ok,
            char_count=len(text),
        )
    )
    try:
        session.commit()
        return
    except IntegrityError:
        session.rollback()
    if not ok:
        return  # a failure never overwrites whatever is already stored
    existing = session.scalar(
        select(PageText).where(PageText.document_id == document_id, PageText.page == page)
    )
    if existing is not None and not existing.extract_ok:
        existing.text, existing.extract_ok, existing.char_count = text, True, len(text)
        session.commit()


def get_page_text(session, document_id: str, page: int, pdf_path=None) -> str:
    """The text of one page, from the store; OCR'd and stored on a miss when `pdf_path` is given.

    A page stored as ERRORED is re-attempted when a path is given, the same way
    ``get_row_text_with_report`` does it. This path used to serve the cached empty string forever, so a
    single transient Tesseract timeout dropped that page from every stage reading through here - with
    no error to notice, because an unreadable page and a blank one looked identical.
    """
    row = session.scalar(
        select(PageText).where(PageText.document_id == document_id, PageText.page == page)
    )
    if row is not None and (row.extract_ok or pdf_path is None):
        return row.text or ""
    if pdf_path is None:
        return ""
    text, ok = _extract(pdf_path, page)
    if row is None:
        _store(session, document_id, page, text, ok)
    elif ok:  # a fresh failure leaves the stored failure as it stands
        row.text, row.extract_ok, row.char_count = text, ok, len(text)
        session.commit()
    return text


def get_pages_text(
    session, document_id: str, pages, pdf_path=None, mark_pages: bool = False
) -> str:
    """Text for several pages, in ascending page order, joined as one string.

    ``mark_pages`` prefixes each page with ``Page <n>:`` - the same convention as
    ``ocr.extract_text_from_selected_pages``, which depositions rely on to produce one summary line
    per page. Off by default for the same reason it is there: those markers would otherwise reach
    every category's model input, and the duplicate check feeds this text into similarity scoring
    where a shared ``Page 1: Page 2:`` vocabulary makes unrelated documents look alike.
    """
    wanted = sorted(set(int(p) for p in pages))
    if not wanted:
        return ""
    found = {
        r.page: (r.text or "")
        for r in session.scalars(
            select(PageText).where(PageText.document_id == document_id, PageText.page.in_(wanted))
        )
    }
    missing = [p for p in wanted if p not in found]
    if missing and pdf_path is not None:
        for page in missing:
            found[page] = get_page_text(session, document_id, page, pdf_path=pdf_path)
    out = []
    for page in wanted:
        text = found.get(page, "")
        out.append(f"Page {page}:\n{text}\n" if mark_pages else text)
    return "".join(out)


def populate_document(session, document_id: str, pdf_path, total_pages: int, workers=None) -> int:
    """OCR every page of a document that is not already stored. Returns how many pages were added.

    Called once at the start of the segment job, before segmentation, so every later stage finds the
    text already there. Idempotent: a second call stores nothing.
    """
    if workers is None:
        workers = get_settings().page_text_workers
    # scalars() over a single column yields the VALUES, not rows - so this is a set of page numbers.
    #
    # extract_ok pages ONLY. Counting a stored FAILURE as done removed that page from every future
    # population of the document, so one transient timeout became permanent by omission - the page was
    # never missing, so nothing ever looked at it again.
    have = set(
        session.scalars(
            select(PageText.page).where(
                PageText.document_id == document_id, PageText.extract_ok.is_(True)
            )
        )
    )
    missing = [p for p in range(1, int(total_pages) + 1) if p not in have]
    if not missing:
        return 0

    # OCR off-session in the pool, then store on the caller's session: a Session is not thread-safe,
    # so the threads must not touch it. This is the same rule the summarize pool follows.
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        texts = list(pool.map(lambda page: (page, *_extract(pdf_path, page)), missing))
    for page, text, ok in texts:
        _store(session, document_id, page, text, ok)
    logger.info("stored OCR text for %d page(s) of document %s", len(texts), document_id)
    return len(texts)


def get_row_text_with_report(session, document_id: str, pages, pdf_path=None):
    """``(text, report)`` for a page range, served from the store, matching the contract of
    ``ocr.extract_pages_with_report`` exactly: ``report`` is ``{"pages", "errored", "blank"}``.

    That contract is preserved rather than simplified because the duplicate check reports the
    difference to the reviewer: an ERRORED page may be a transient Tesseract timeout worth another
    attempt, while a blank page is a film, photograph or separator sheet that will never yield words.
    Collapsing them is how a dedup run that could not read a fifth of a document once presented as a
    clean one.

    A page stored as errored IS retried here when a path is given, mirroring the retry the direct
    extraction path performs - a cached failure must not become permanent.
    """
    wanted = sorted(set(int(p) for p in pages))
    report = {"pages": wanted, "errored": [], "blank": []}
    if not wanted:
        return "", report

    rows = {
        r.page: r
        for r in session.scalars(
            select(PageText).where(PageText.document_id == document_id, PageText.page.in_(wanted))
        )
    }
    parts = []
    for page in wanted:
        row = rows.get(page)
        if row is None or (not row.extract_ok and pdf_path is not None):
            if pdf_path is None:
                report["errored"].append(page)
                continue
            text, ok = _extract(pdf_path, page)
            if row is None:
                _store(session, document_id, page, text, ok)
            else:
                row.text, row.extract_ok, row.char_count = text, ok, len(text)
                session.commit()
        else:
            text, ok = (row.text or ""), row.extract_ok
        if not ok:
            report["errored"].append(page)
        elif not text.strip():
            report["blank"].append(page)
        parts.append(text)
    return "".join(parts), report
