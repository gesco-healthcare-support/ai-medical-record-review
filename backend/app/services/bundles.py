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


def is_resolved_duplicate(row) -> bool:
    """True for a copy the reviewer resolved AWAY: a live cluster's non-primary member.

    `resolve_duplicate`'s keep_one marks one member `dupe_primary` and clears `dupe_dismissed` on
    all of them, so the others are copies of a document already being delivered once.

    `dupe_dismissed` is checked because dismissing means "these are NOT duplicates" - both rows are
    real documents again and both belong in a bundle. A row that LEFT a cluster has `dupe_group`
    cleared by `_leave_cluster`, so it reads as an ordinary row here, which is correct.
    """
    return (
        row.get("dupe_group") is not None
        and not row.get("dupe_primary")
        and not row.get("dupe_dismissed")
    )


def matched_rows(rows, categories):
    """Rows whose category is in ``categories`` (int/str mix ok), original order kept - minus any
    copy the reviewer already resolved away as a duplicate.

    A bundle is a DELIVERABLE: a combined PDF or a Word report a client receives. Selecting purely
    on category put a confirmed duplicate's pages into the PDF a SECOND time and made
    bundle-summarize spend three model calls writing an independent summary of the document the
    reviewer had just resolved away - because keep_one changes `include` and `dupe_primary` and
    leaves the CATEGORY alone, so the copy still looks like an ordinary category-3 row.

    Filtered on the DUPLICATE fields rather than on `include`, and that choice is the whole design
    of this fix. `include` looks like the natural filter and is not:

      * migration `a7c3f2e9b1d4` ran `UPDATE review_rows SET include = false WHERE category IN
        ('9','100')`, and `a9c4e13f70b2` turned depositions back on in `categories.summarize_default`
        WITHOUT backfilling the rows. So a blanket migration unchecked them, not a reviewer.
      * measured on the box: filtering on `include` would return NOTHING for **11 of 30** documents
        holding depositions and **30 of 67** holding diagnostic/operative records. The Depositions
        bundle preset is `["9"]`, so that preset would simply stop working for older records.
      * filtering on the duplicate fields instead removes **16 of 441** rows corpus-wide and leaves
        **zero** documents empty - it takes exactly the copies the defect is about.

    Here rather than in the API layer because this is where the category rule lives, so the next
    caller cannot reintroduce the bug by selecting on category alone. `ReviewRow.as_row()` carries
    `dupe_group`, `dupe_primary` and `dupe_dismissed`, so nothing needs threading through.
    """
    wanted = {str(c) for c in categories}
    return [
        row for row in rows if str(row["category"]) in wanted and not is_resolved_duplicate(row)
    ]


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
    if rows and not entries:
        # EVERY row was blank. Skipping per-row must not turn a clear error into a silently empty
        # deliverable: without this the caller hands `[]` to `build_mrr_document` and streams a Word
        # file containing a letterhead and no summaries, with a 200. Re-raising restores the 422
        # ("No readable text was found in this document") that the un-isolated loop produced, which
        # is the honest answer when there is nothing to deliver.
        raise EmptyExtractionError(f"no readable text in any of the {len(rows)} matching documents")
    return entries
