"""Category-filtered document bundles (Diagnostic & Operative, Depositions, ...) - ported.

Pull the review rows whose category is in a requested set, then either concatenate their source
pages into one PDF (no LLM) or summarize just those records into a filtered report. Both are
per-document and stay in memory - no ~/MRRs artifacts (HIPAA); ids-only logging lives in caller.
"""

import html
import io
import logging
import re

import pymupdf
from pypdf import PdfReader, PdfWriter

from app.errors import EmptyExtractionError
from app.services import summarize_engine

logger = logging.getLogger(__name__)

# Letter, with the same margins the linked PDF uses for its own letter pages.
_COVER_PAGE = pymupdf.paper_rect("letter")
_COVER_CONTENT = pymupdf.Rect(54, 54, _COVER_PAGE.width - 54, _COVER_PAGE.height - 54)


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


# --------------------------------------------------------------------------------------------
# THE COVER PAGE.
#
# Asked whether the folder should carry the combined PDF or a written report for Diagnostics,
# the reviewers answered: "for the diagnostics we just want a PDF with the documents together.
# Preferably with a cover page that includes a list of reports", and sent one of their own.
#
# Theirs is a bordered three-column table - Date | PROVIDER | REPORT TITLE - under the heading
# LIST OF DIAGNOSTIC AND OPERATIVE REPORTS, running onto a second page when the list is long.
# That is what this reproduces. Rendered through Story like the linked PDF, so pagination is
# the library's problem rather than ours.

_CREDENTIALS = re.compile(
    r"\b(?:M\.D|D\.O|D\.C|P\.T|R\.N|P\.A|N\.P|PSY\.D|L\.V\.N|O\.D|D\.D\.S|PH\.D|D\.P\.M)\b",
    re.IGNORECASE,
)

COVER_COLUMNS = ("Date", "PROVIDER", "REPORT TITLE")

# `_store_rows` writes "-" for an empty row field, so a delivered page has to read it as absent
# rather than print it. Same sentinel `reporting.date_label` already special-cases.
_EMPTY_FIELD = {"", "-"}


def _value(field) -> str:
    """A row field as text, with the field spec's "no value" sentinel read as no value."""
    text = (field or "").strip()
    return "" if text in _EMPTY_FIELD else text


def split_deliverable_title(title) -> tuple[str, str]:
    """A delivered header line -> (provider, report title) for the two right-hand columns.

    `TITLE_PROMPT` specifies one shape - ``AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.`` - and
    says an absent element is omitted ALONG WITH ITS SEPARATOR. So the document type is the last
    element whenever there is one, whatever else survived, and everything before it identifies
    who produced the document. That is the contract this splits on, rather than trying to
    recognise a study by name: measured over the diagnostic summaries on one server, a modality
    regex found the study in the last element on far fewer rows than the shape itself holds,
    because document types are worded far more freely than any list can enumerate.

    WHAT IT WILL NOT DO IS GUESS WHICH PART IS THE AUTHOR. Their column reads facility first,
    and ours states the author first, so the two are reordered ONLY when the leading element
    carries a credential - which is the one unambiguous signal that it is a person. On the same
    measurement that held for 117 of 183 three-part titles; the other 66 keep our own order,
    which may read facility-last but never puts a clinic where a doctor's name belongs.

    A title with nothing to split - 72 of 268 on that server arrive as a single element - puts
    everything under REPORT TITLE and leaves PROVIDER empty, rather than splitting on a guess.
    """
    parts = [part.strip(" .") for part in re.split(r"\.\s+", (title or "").strip())]
    parts = [part for part in parts if part]
    if not parts:
        return "", ""
    provider, report = parts[:-1], parts[-1]
    if len(provider) >= 2 and _CREDENTIALS.search(provider[0]):
        provider = provider[1:] + provider[:1]
    # `M.D.` ends with the very character the separator is, so the split eats it and a client
    # would read `JANE SMITH, M.D`. Put the period back on any element ending in a lone capital,
    # which is a credential and essentially nothing else - a facility name ends in a word.
    provider = [re.sub(r"\b([A-Z])$", r"\1.", part) for part in provider]
    return " \u2013 ".join(provider), report


def cover_entries(rows, delivered=None) -> list[tuple[str, str, str]]:
    """(date, provider, report title) per row, in the order the rows appear in the record.

    ``delivered`` maps a row's start page to the ``(date, title)`` the DELIVERABLE states, which
    is not what the row carries. The row holds what segmentation read off the page - a ~31
    character title, and a date the reviewer never has to fill in - while the summary holds the
    formatted header line the summarizer writes (~87 characters) and the date the MRR itself
    prints. Only the second pair splits into their columns and agrees with the report the client
    reads beside it, which is the whole point: a list page contradicting its own report is the
    drift this export path keeps rediscovering.

    It falls back to the row for a record whose rows have not been summarized - a cover page
    naming the documents roughly still beats no cover page.

    `_store_rows` writes the literal string ``"-"`` for a row field the reviewer left empty, so
    that is what an absent title or date arrives as rather than None. Read naively it is content,
    and a record with no titles produced a bordered table of dashes in front of the documents.
    `reporting.date_label` special-cases the same sentinel for the same reason.
    """
    delivered = delivered or {}
    entries = []
    for row in rows:
        date, title = delivered.get(row.get("start")) or ("", "")
        title = _value(title) or _value(row.get("title"))
        provider, report = split_deliverable_title(title)
        entries.append((_value(date) or _value(row.get("date")), provider, report))
    return entries


_COVER_CSS = """
  body { font-family: 'Times New Roman', serif; font-size: 11pt; }
  h1 { font-size: 12pt; font-weight: bold; text-align: center; margin: 0 0 12pt 0; }
  table { width: 100%; border: 1px solid #000; }
  th { font-weight: bold; text-align: left; border: 1px solid #000; padding: 3pt; }
  td { text-align: left; border: 1px solid #000; padding: 3pt; vertical-align: top; }
  td.d { width: 15%; }
  td.p { width: 45%; }
"""


def build_cover_pdf(heading: str, entries) -> bytes:
    """The list, as its own one-or-more-page PDF.

    Story paginates a table across pages by itself, which is why the list is one table rather
    than a hand-placed grid - theirs runs onto a second page and any record with enough imaging
    will too.
    """
    body = "".join(
        "<tr><td class='d'>{}</td><td class='p'>{}</td><td>{}</td></tr>".format(
            html.escape(date), html.escape(provider), html.escape(report)
        )
        for date, provider, report in entries
    )
    header = "".join(f"<th>{html.escape(column)}</th>" for column in COVER_COLUMNS)
    doc = (
        f"<html><head><style>{_COVER_CSS}</style></head><body>"
        f"<h1>{html.escape(heading)}</h1>"
        f"<table><tr>{header}</tr>{body}</table>"
        "</body></html>"
    )
    story = pymupdf.Story(html=doc)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    more = 1
    while more:
        device = writer.begin_page(_COVER_PAGE)
        more, _ = story.place(_COVER_CONTENT)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()


def build_bundle_pdf(pdf_path, rows, cover=None):
    """Concatenate the pages of ``rows`` into an in-memory PDF buffer. Out-of-range pages are
    skipped rather than raising: one bad row must not sink the whole bundle.

    ``cover`` is the optional list page, already rendered, which goes in FRONT of the documents.
    Optional because the depositions bundle was not asked for one, and a record whose rows carry
    no dates or titles would produce an empty table rather than a useful page."""
    reader = PdfReader(pdf_path)
    last = len(reader.pages)
    writer = PdfWriter()
    if cover:
        for page in PdfReader(io.BytesIO(cover)).pages:
            writer.add_page(page)
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
