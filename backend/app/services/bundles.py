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


def resolved_clusters(rows) -> set:
    """The `dupe_group` ids a reviewer actually resolved with keep_one, i.e. that HAVE a primary.

    Cluster state, not row state, and that distinction is the whole of this function. A cluster the
    dedup worker has just written has NO primary: `worker/tasks.py` sets `dupe_group`,
    `dupe_similarity` and `dupe_dismissed` on each member and never touches `dupe_primary`, which
    becomes true only in `resolve_duplicate`'s keep_one. Measured on the box, **48 of 138 clusters
    (35%) sit in exactly that state** - written, never opened.

    Judging a row in isolation therefore reads "no primary named" as "resolved away" and drops EVERY
    member of an unresolved cluster, delivering the document zero times instead of once - strictly
    worse than the double delivery this module is fixing. Of the 165 rows the row-only test removed
    corpus-wide, **125 came from unresolved clusters**. Caught on review by @adrian-g.

    Group ids are per-document and `matched_rows` is called with one document's rows, so there is no
    cross-document collision to worry about.
    """
    return {
        row.get("dupe_group")
        for row in rows
        if row.get("dupe_group") is not None and row.get("dupe_primary")
    }


def is_resolved_duplicate(row, resolved_groups) -> bool:
    """True for a copy the reviewer resolved AWAY: a non-primary member of a RESOLVED cluster.

    `resolve_duplicate`'s keep_one marks one member `dupe_primary` and clears `dupe_dismissed` on
    all of them, so the others are copies of a document already being delivered once.

    Three conditions, each excluding a state that must stay in the bundle:
      * in a resolved cluster - an UNRESOLVED one is a question nobody has answered, so all of its
        members are still real documents (see `resolved_clusters`);
      * not the primary - that is the copy being kept;
      * not dismissed - dismissing means "these are NOT duplicates", so both copies are real again.

    A row that LEFT a cluster has `dupe_group` cleared by `_leave_cluster`, so it reads as an
    ordinary row here, which is correct.
    """
    return (
        row.get("dupe_group") in resolved_groups
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
      * measured on the box: an `include` filter would return NOTHING for **11 of 30** documents
        holding depositions. The Depositions preset is `["9"]`, so it would simply stop working for
        older records.
      * diagnostic/operative is NOT affected by that migration - **0 of 67** such documents would be
        emptied. An earlier version of this note said 30, from a query using `bool_and(include)`,
        which answers "has ANY unchecked row" where it needed `bool_or`. The two coincide for
        depositions precisely because the migration unchecked them ALL, which is how a wrong query
        produced a right number there and hid the error.

    `resolved_groups` is computed over ALL the document's rows, BEFORE the category filter: cluster
    state belongs to the cluster, and `duplicate_gate` can group rows of different categories
    (`same_date and (same_title or same_category)`), so a primary may sit outside the requested set.

    Here rather than in the API layer because this is where the category rule lives, so the next
    caller cannot reintroduce the bug by selecting on category alone. `ReviewRow.as_row()` carries
    `dupe_group`, `dupe_primary` and `dupe_dismissed`, so nothing needs threading through.
    """
    wanted = {str(c) for c in categories}
    resolved_groups = resolved_clusters(rows)
    return [
        row
        for row in rows
        if str(row["category"]) in wanted and not is_resolved_duplicate(row, resolved_groups)
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
