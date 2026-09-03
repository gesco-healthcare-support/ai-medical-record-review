"""Sliding-window segmentation engine: scanned PDF -> categorized sub-document rows (ported).

Windows are byte-budgeted and OVERLAP; each seam page is decided by the window that saw the page
before it (ownership), so no document is cut at a window edge. Output rows always TILE the PDF.
This is the segment worker's core; it runs on the P4 `segment` (torch/classifier) worker tier.
"""

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from google.genai import types
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.errors import OcrUnavailableError
from app.errors import PipelineTimeoutError
from app.services.classification import classify
from app.services.gemini import (
    SEGMENT_RESPONSE_SCHEMA,
    SEGMENTATION_PROMPT,
    SEGMENTATION_SYSTEM,
    parse_segment_item,
)
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_pages_with_report
from app.services.pools import PoolTimeout, drain_pool
from app.services.summary_doi import extract_injury_date
from app.services.taxonomy import DEFAULT_ID
from app.services.verify_pass import verify_and_merge
from app.services.windows import byte_budgeted_windows

logger = logging.getLogger(__name__)


def _generation_config():
    # Segmentation keeps thinking (segment_thinking_budget, default dynamic): an A/B on labeled
    # cases showed thinking-off regresses strict doc-F1 here, unlike the other structured calls.
    return types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        top_k=40,
        response_mime_type="application/json",
        response_schema=SEGMENT_RESPONSE_SCHEMA,
        system_instruction=SEGMENTATION_SYSTEM,
        thinking_config=types.ThinkingConfig(
            thinking_budget=get_settings().segment_thinking_budget
        ),
    )


def _window_rows(pdf_path, window_start, window_end, client):
    """Segment pages [window_start, window_end] in one inline call; absolute-page rows."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for p in range(window_start - 1, window_end):
        writer.add_page(reader.pages[p])
    buffer = io.BytesIO()
    writer.write(buffer)
    part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="application/pdf")

    response = generate_with_retry(
        client,
        model=get_settings().genai_model,
        contents=[part, SEGMENTATION_PROMPT],
        config=_generation_config(),
    )
    clean = (response.text or "").replace("```json", "").replace("```", "").strip()
    rows = []
    for item in json.loads(clean):
        try:
            s, e, title, date, manual = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue  # one malformed element must not abort the window
        rows.append(
            {
                "start": s + window_start - 1,
                "end": e + window_start - 1,
                "title": title,
                "date": date,
                # Filled by the isolated per-sub-document read at the end of run_segmentation;
                # the segmentation call no longer reports one. "-" means "states none".
                "injury_date": "-",
                "flag": manual,
            }
        )
    return sorted(rows, key=lambda r: (r["start"], r["end"]))


def merge_window_rows(window_reports, windows, total_pages):
    """Ownership merge: window k owns starts in (ws_k, ws_{k+1}]; the last window owns through the
    end. Metadata comes from the owning window's row. Ends are re-derived so rows tile."""
    surviving = []
    for k, rows in enumerate(window_reports):
        ws = windows[k][0]
        owned_cap = total_pages if k == len(windows) - 1 else windows[k + 1][0]
        floor = 0 if k == 0 else ws  # window 0 keeps its first-page row (absolute page 1)
        surviving.extend(r for r in rows if floor < r["start"] <= owned_cap)

    surviving.sort(key=lambda r: r["start"])
    deduped = []
    for row in surviving:
        if deduped and row["start"] == deduped[-1]["start"]:
            continue  # same start seen by two windows: the earlier (owning) row wins
        deduped.append(dict(row))

    if not deduped or deduped[0]["start"] > 1:
        # Never leave front pages uncovered; an explicit low-confidence row is honest.
        deduped.insert(
            0, {"start": 1, "end": 1, "title": "-", "date": "-", "injury_date": "-", "flag": "x"}
        )
    for i, row in enumerate(deduped):
        row["end"] = (deduped[i + 1]["start"] - 1) if i + 1 < len(deduped) else total_pages
    return deduped


# How many of a row's leading pages the low-confidence re-classify may read, and the character
# ceiling on their combined text.
#
# THREE, not one, and the number is measured rather than chosen. Escalating on ONE page makes a
# document's category rest on the single page the boundary happens to point at, and boundaries move:
# the same 463-page PDF (sha256 c8c514fc) segmented twice on the same build, 24 seconds apart, put
# one document's start at page 94 in one run and 93 in the other. Page 93 is the previous document's
# last page. Replayed on the segment worker, 3/3 each:
#
#     title alone .......... 13
#     title + p94 .......... 13     the real first page
#     title + p93 ......... 100     llm+embedding AGREE, so needs_review is False
#     title + p93+94 ...... 100     still wrong, now merely uncertain
#     title + p93+94+95 .... 13     recovered
#
# So a 52-page medico-legal evaluation - the richest category in the taxonomy - was assigned 100,
# which is unchecked for summarization, and the cascade reported high confidence while doing it.
#
# TWO pages is NOT enough, which is why this is a flat page count rather than a "read more when the
# first page looks thin" test. Page 93 holds 425 characters against page 94's 1153; any threshold
# that fires on 425 is satisfied by their 1578 combined, and 1578 characters still answered 100.
#
# The character ceiling bounds the prompt: llm_classify inlines this text whole and truncates
# nothing, and three pages of a dense deposition run far longer than three pages of a form.
_ESCALATION_PAGES = 3
_ESCALATION_CHARS = 12_000


def _escalation_text(pdf_path, row, page_text_fn=None):
    """Combined text of the row's first few pages, for the low-confidence re-classify.

    Never reads past the row's own end, so the evidence always belongs to the document being
    classified. A page whose text is missing is skipped rather than ending the read - a blank
    scanned backside between two real pages must not truncate the evidence.
    """
    start = int(row["start"])
    end = int(row.get("end") or start)
    pages = list(range(start, min(start + _ESCALATION_PAGES - 1, end) + 1))
    if page_text_fn is None:
        # Standalone path: one extraction call for the whole span, keeping this module DB-free.
        #
        # Through `extract_pages_with_report` rather than `extract_text_from_selected_pages` (#212),
        # which catches a per-page Tesseract failure and continues - so this returned a short
        # escalation window and raised nothing. PRODUCTION NEVER TAKES THIS BRANCH: the worker
        # always supplies `page_text_fn`. Which is exactly who it could mislead - the eval harnesses
        # in `scripts/eval/` run segmentation outside the worker, so a silently short window changed
        # what the classifier was scored on while the run reported a clean number. Twice this
        # month a measurement defect has inverted a conclusion, so an unattributable one matters.
        text, report = extract_pages_with_report(pdf_path, pages)
        if report["errored"]:
            logger.warning(
                "escalation window for pages %s-%s is SHORT: %d of %d page(s) could not be OCR'd "
                "(%s); a classification scored on this text is scored on part of it",
                start,
                end,
                len(report["errored"]),
                len(report["pages"]),
                report["errored"],
            )
        return text[:_ESCALATION_CHARS]
    parts = []
    total = 0
    for page in pages:
        text = page_text_fn(page) or ""
        if not text.strip():
            continue
        parts.append(text)
        total += len(text)
        if total >= _ESCALATION_CHARS:
            break
    return "\n".join(parts)[:_ESCALATION_CHARS]


def _categorize(pdf_path, row, page_text_fn=None):
    """B5 cascade on the title, escalating to the row's first pages when inconclusive; any
    low-confidence result routes the row to human review via the flag.

    ``page_text_fn(page) -> str`` lets the caller supply already-extracted text (the worker passes a
    reader over the `page_texts` store). Without it this OCRs the pages itself, which keeps this
    module DB-free and standalone-runnable - but means they are extracted twice in a full run. In the
    worker every page is already in the store, so widening the escalation from one page to
    ``_ESCALATION_PAGES`` costs extra row reads and prompt tokens, not extra OCR.
    """
    result = classify(row["title"])
    if result.needs_review:
        try:
            page_text = _escalation_text(pdf_path, row, page_text_fn)
            if page_text.strip():
                result = classify(row["title"], page_text=page_text)
        except OcrUnavailableError:
            # Narrow on purpose. The broad catch below is RIGHT for a per-page failure - one
            # unreadable page must not stop a document being categorized on its title alone. It is
            # wrong for a config failure, which fails identically on EVERY row: the whole document
            # would be quietly categorized title-only, and so would every document after it, leaving
            # one WARNING per row and nothing naming the missing binary.
            raise
        except Exception as exc:
            logger.warning("classification escalation OCR failed: %s", exc)
    row["category"] = result.category
    # Which cascade path decided it, persisted from here on (#188). `result` is the ESCALATED call
    # where one happened, so this is the verdict that stood rather than the title-only one it
    # overruled. It is what separates "both signals agreed this is paperwork" from "a low-confidence
    # guess" inside category 100 - a distinction the category alone cannot carry.
    row["method"] = result.method
    if result.needs_review or row["flag"].strip().lower() == "x":
        row["flag"] = "x"
    return row


def run_segmentation(pdf_path, total_pages, progress=None, page_text_fn=None):
    """PDF -> tiled, categorized sub-document rows, reporting progress per stage.

    progress(stage, current, total) is called around every model interaction; it must never raise.
    ``page_text_fn(page) -> str`` is an optional reader for already-extracted page text; see
    ``_categorize``. Threaded through rather than imported so this module stays DB-free.
    """
    settings = get_settings()
    client = get_genai_client()
    # Every pool drain is bounded by the size-aware budget, so no as_completed waits forever.
    pool_timeout = settings.pool_timeout(total_pages)

    def report(stage, current, total):
        if progress is not None:
            progress(stage, current, total)

    windows = byte_budgeted_windows(
        pdf_path,
        total_pages,
        settings.window_overlap,
        int(settings.window_budget_mb * 1024 * 1024),
        settings.window_max_pages,
    )
    # Windows are independent (each builds its own sub-PDF and calls the model), so run them on a
    # small pool - the seam's rate limiter caps the aggregate request rate. Results are placed by
    # window index so the downstream ownership merge still sees them in order.
    report("segmenting", 0, len(windows))
    reports = [None] * len(windows)
    with ThreadPoolExecutor(max_workers=settings.segment_window_workers) as pool:
        futures = {
            pool.submit(_window_rows, pdf_path, ws, we, client): k
            for k, (ws, we) in enumerate(windows)
        }
        done = 0
        try:
            for future in drain_pool(futures, pool_timeout):
                reports[futures[future]] = future.result()  # fail loudly; never drop a window
                done += 1
                report("segmenting", done, len(windows))
        except PoolTimeout as pt:
            # A lost window is lost coverage, so a stall here is terminal (friendly message) rather
            # than a silently short document.
            logger.warning(
                "segmentation windows timed out after %ss; %d window(s) unfinished",
                pool_timeout,
                len(pt.unfinished),
            )
            raise PipelineTimeoutError(
                f"segmentation timed out with {len(pt.unfinished)} window(s) unfinished"
            ) from pt

    rows = merge_window_rows(reports, windows, total_pages)

    # Rows are independent, so categorize on a small pool. Each worker owns its row (no shared
    # mutation) and classify() opens its own short session for catalog reads (thread-safe).
    report("categorizing", 0, len(rows))
    with ThreadPoolExecutor(max_workers=settings.classify_workers) as pool:
        futures = {pool.submit(_categorize, pdf_path, row, page_text_fn): row for row in rows}
        done = 0
        try:
            for future in drain_pool(futures, pool_timeout):
                future.result()  # a worker failure must fail the job loudly, not vanish
                done += 1
                report("categorizing", done, len(rows))
        except PoolTimeout as pt:
            # A stalled categorization is recoverable: the row still exists, it just lacks a
            # confident category. Default it to the catch-all + review flag and keep going, rather
            # than failing the whole document (a missing category would break the verify/merge).
            for future in pt.unfinished:
                row = futures[future]
                row["category"] = DEFAULT_ID
                row["flag"] = "x"
                # No classify() answer exists for this row, so there is no method to copy. Record
                # the reason explicitly rather than leaving it unset: unset is NULL, which means
                # "segmented before the column existed" and is shown unchanged by the review
                # filter. A row nothing could classify is a different thing and has to look like it.
                row["method"] = "timeout"
            logger.warning(
                "categorization timed out after %ss; %d row(s) defaulted to review",
                pool_timeout,
                len(pt.unfinished),
            )

    if settings.verify_merge:
        rows, stats = verify_and_merge(pdf_path, rows, progress=progress, pool_timeout=pool_timeout)
        logger.info("verify pass: %s", stats)

    # Injury date, read per sub-document in ISOLATION. This runs LAST and that is load-bearing:
    # verify_and_merge can MERGE rows, and the read is defined by a page range, so reading before the
    # ranges are final would read the wrong pages.
    #
    # It used to be read twice - once as field "i" of the segmentation call, which sees a whole window
    # and so propagates one document's DOI onto neighbours that state none, and again at summarize
    # time. The two never reconciled and the summarize-stage value won, so the review page displayed a
    # date the client never received and a reviewer's correction was silently discarded. One read,
    # here, is what makes the row the single source of truth.
    report("injury-dates", 0, len(rows))
    with ThreadPoolExecutor(max_workers=settings.doi_workers) as pool:
        futures = {
            pool.submit(extract_injury_date, pdf_path, row["start"], row["end"]): row
            for row in rows
        }
        done = 0
        try:
            for future in drain_pool(futures, pool_timeout):
                futures[future]["injury_date"] = future.result()
                done += 1
                report("injury-dates", done, len(rows))
        except PoolTimeout as pt:
            # Recoverable, so follow the CATEGORIZATION precedent rather than the segmentation one: a
            # row with no injury date keeps "-", which means "this document states none" and simply
            # produces no DOI prefix. A lost segmentation window is lost coverage; a lost date is not.
            logger.warning(
                "injury-date reads timed out after %ss; %d row(s) left without one",
                pool_timeout,
                len(pt.unfinished),
            )
    return rows
