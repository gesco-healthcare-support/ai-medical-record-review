"""Category-filtered document bundles (Diagnostic & Operative, Depositions, ...) - ported.

Pull the review rows whose category is in a requested set, then either concatenate their source
pages into one PDF (no LLM) or summarize just those records into a filtered report. Both are
per-document and stay in memory - no ~/MRRs artifacts (HIPAA); ids-only logging lives in caller.
"""

import io
import logging

from pypdf import PdfReader, PdfWriter

from app.errors import EmptyExtractionError
from app.services import summarize_engine

logger = logging.getLogger(__name__)


def matched_rows(rows, categories):
    """Rows whose category is in ``categories`` (int/str mix ok), original order kept."""
    wanted = {str(c) for c in categories}
    return [row for row in rows if str(row["category"]) in wanted]


def pages_for_rows(rows):
    """The 1-indexed pages covered by ``rows``, in row order (ranges inclusive)."""
    pages = []
    for row in rows:
        pages.extend(range(int(row["start"]), int(row["end"]) + 1))
    return pages


def build_bundle_pdf(pdf_path, rows):
    """Concatenate the pages of ``rows`` into an in-memory PDF buffer. Out-of-range pages are
    skipped rather than raising: one bad row must not sink the whole bundle."""
    reader = PdfReader(pdf_path)
    last = len(reader.pages)
    writer = PdfWriter()
    for page in pages_for_rows(rows):
        if 1 <= page <= last:
            writer.add_page(reader.pages[page - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def bundle_summary_entries(pdf_path, rows, model=None, prompt_for=None):
    """Summarize each row with its category prompt -> Word-export entry dicts. ``prompt_for`` is
    an optional row -> prompt resolver injected by the caller (DB-first via catalog.get_prompt).

    Titles go through ``presentable_title`` because ``summarize_row`` returns them decorated with the
    three internal review markers the app displays, and this is a delivered Word document. Taking the
    raw value shipped those markers to the client while the review export stripped them; the manual
    check marker reaches most rows, because of the row flag it keys on.
    """
    entries = []
    for row in rows:
        prompt = prompt_for(row) if prompt_for is not None else None
        # The bundle export is a bounded quick path: skip the faithfulness verify pass to keep it fast.
        try:
            output = summarize_engine.summarize_row(
                pdf_path, row, model, prompt=prompt, verify=False
            )
        except EmptyExtractionError:
            # ONE blank row must not discard the whole bundle. `summarize_row` raises this for a row
            # whose pages read cleanly and yield no words - a photograph, a film, a separator
            # sheet - and `_pipeline_error_response` already classifies it 422, "a property of the
            # document", i.e. an expected per-row outcome rather than a systemic failure.
            #
            # Without this the exception left `bundle_summary_entries` entirely and the caller's
            # `except PipelineError` discarded `entries` - throwing away every summary generated
            # BEFORE the blank row, each of which cost real model calls, and returning an error
            # for a bundle that was mostly fine. The main summarize worker treats the identical class
            # per-row for the same reason.
            #
            # Deliberately NOT a bare `except PipelineError`: an OcrUnavailableError means Tesseract
            # or Poppler is missing, which fails identically on every remaining row, so continuing
            # would spend the rest of the loop discovering that one row at a time. That one must
            # still abort the bundle.
            logger.warning(
                "bundle: no readable text for pages %s-%s; that document is omitted",
                row.get("start"),
                row.get("end"),
            )
            continue
        entries.append(
            {
                "summaryDate": output.get("summaryDate") or "-",
                "summaryTitle": summarize_engine.presentable_title(output["summaryTitle"]),
                "summaryText": output["summaryText"],
            }
        )
    return entries
