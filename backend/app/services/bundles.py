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

from app.errors import EmptyExtractionError, TranscriptPagesUnreadableError
from app.services import summarize_engine
from app.services.reporting import is_diagnostic

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

# A short all-letters token before a period is an ABBREVIATION, not the end of an element:
# `ST. MARY'S`, `MT. SINAI`, `U.S. HEALTHWORKS` are ordinary facility names. Splitting on ". "
# alone tore them in half and the PROVIDER column read `ST - MARY'S HOSPITAL`, in a page a client
# reads. Bounded at three characters because that is what the real cases need and a longer bound
# starts swallowing genuine one-word elements.
_ABBREVIATION = re.compile(r"^[A-Za-z][A-Za-z.]{0,2}$")

# The separator and an abbreviation's own final period are the same character, so the split eats
# it and a client read `JANE SMITH, M.D`. Restored only after an actual abbreviation - a lone
# letter with a period in front of it. Anchoring on "ends in a capital" instead was wrong twice:
# it missed a reviewer-edited lowercase title (`m.d` -> still `m.d`, since `Summary
# .effective_title` returns `edited_title` first and nothing normalises its case), and it added a
# period to `IMAGING CENTER A`, on a docstring's claim that "a facility name ends in a word".
_ABBREVIATION_TAIL = re.compile(r"(?<=\.)([A-Za-z])$")


def _elements(title: str) -> list[str]:
    """The title's elements, with abbreviations kept whole."""
    raw = [part.strip(" .") for part in re.split(r"\.\s+", title)]
    elements: list[str] = []
    for part in raw:
        if not part:
            continue
        # A fragment this short followed by more text is the front half of an abbreviated name,
        # so it rejoins what the split separated. The `elements` guard keeps a genuinely short
        # LAST element - a document type of "CT" - from being merged into nothing.
        if elements and _ABBREVIATION.match(elements[-1]):
            elements[-1] = f"{elements[-1]}. {part}"
        else:
            elements.append(part)
    return elements


def _restore_abbreviation(part: str) -> str:
    """`JANE SMITH, M.D` -> `JANE SMITH, M.D.`, in either case, and nothing else touched."""
    return _ABBREVIATION_TAIL.sub(r"\1.", part)


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

    Splitting on ". " alone is NOT enough, and `_elements` is why: the separator and an
    abbreviation's own period are the same character, so `ST. MARY'S HOSPITAL` came apart into two
    elements and the column read `ST - MARY'S HOSPITAL`.
    """
    parts = _elements((title or "").strip())
    if not parts:
        return "", ""
    provider, report = parts[:-1], parts[-1]
    if len(provider) >= 2 and _CREDENTIALS.search(provider[0]):
        provider = provider[1:] + provider[:1]
    provider = [_restore_abbreviation(part) for part in provider]
    # EN DASH, and deliberately - U+2013 appears 19 times in the reference list they sent, in
    # exactly this position. It is not a mistyped hyphen.
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


# THE COLUMN WIDTHS, and both halves of this are load-bearing.
#
# Story takes each column's width from the FIRST ROW, which is the header, and it silently
# IGNORES a percentage width - given percentages it lays the columns out in equal thirds. The
# widths used to be declared on `td` only and as percentages, so the header cells carried none
# and the columns collapsed to their minimum content width: measured 8.7pt / 11.9pt / 448.4pt
# against a 504pt content rect. Date could not fit `04/25/18`, PROVIDER could not fit one word,
# so both wrapped a word per line, and the three header labels overlapped on top of each other
# because each began inside the previous one's text. A client read that page.
#
# So: ABSOLUTE POINTS, declared on `th` AND `td`. The shares are the reviewers' proportions;
# the points are derived from the content rect so a page-size change carries.
_COVER_COLUMN_SHARES = (0.15, 0.45, 0.40)

# What Story adds on top of each declared width (3pt padding a side plus the cell border), and
# how far the table itself sits inside the content rect. Both MEASURED off the rendered
# geometry rather than reasoned from the box model, and both pinned by
# `test_the_cover_table_spans_the_content_rect` - if a MuPDF release moves either, that test
# fails rather than the table quietly overflowing the right margin.
_COVER_CELL_OVERHEAD_PT = 11
_COVER_TABLE_INSET_PT = 11


def _cover_column_widths() -> tuple[int, ...]:
    """Absolute point widths for the cover table, filling the content rect exactly."""
    usable = (
        _COVER_CONTENT.width
        - _COVER_TABLE_INSET_PT
        - _COVER_CELL_OVERHEAD_PT * len(_COVER_COLUMN_SHARES)
    )
    return tuple(round(usable * share) for share in _COVER_COLUMN_SHARES)


_COVER_COLUMN_CLASSES = ("d", "p", "r")

_COVER_CSS = """
  body {{ font-family: 'Times New Roman', serif; font-size: 11pt; }}
  h1 {{ font-size: 12pt; font-weight: bold; text-align: center; margin: 0 0 12pt 0; }}
  table {{ border: 1px solid #000; }}
  th {{ font-weight: bold; text-align: left; border: 1px solid #000; padding: 3pt; }}
  td {{ text-align: left; border: 1px solid #000; padding: 3pt; vertical-align: top; }}
  th.d, td.d {{ width: {0}pt; }}
  th.p, td.p {{ width: {1}pt; }}
  th.r, td.r {{ width: {2}pt; }}
"""


def build_cover_pdf(heading: str, entries) -> bytes:
    """The list, as its own one-or-more-page PDF.

    Story paginates a table across pages by itself, which is why the list is one table rather
    than a hand-placed grid - theirs runs onto a second page and any record with enough imaging
    will too.
    """
    body = "".join(
        "<tr>"
        + "".join(
            f"<td class='{cls}'>{html.escape(value)}</td>"
            for cls, value in zip(_COVER_COLUMN_CLASSES, row, strict=True)
        )
        + "</tr>"
        for row in entries
    )
    # The header carries the same classes as the body. It is not decoration: Story reads the
    # column widths off this row, so a bare <th> collapses the whole table - see _COVER_CSS.
    header = "".join(
        f"<th class='{cls}'>{html.escape(column)}</th>"
        for cls, column in zip(_COVER_COLUMN_CLASSES, COVER_COLUMNS, strict=True)
    )
    css = _COVER_CSS.format(*_cover_column_widths())
    doc = (
        f"<html><head><style>{css}</style></head><body>"
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
    # Which exception types caused a row to be skipped, so the all-rows-skipped floor below can name
    # what actually happened instead of always reporting the blank-text case.
    skipped_causes: set[type[Exception]] = set()
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
            skipped_causes.add(EmptyExtractionError)
            continue
        except TranscriptPagesUnreadableError:
            # Same argument, different trigger, and it is here for the same reason the class exists
            # at all. A deposition whose printed page numbers could not be read is one document's
            # problem, not the export's: every row already summarized cost real model calls, and
            # `except PipelineError` upstream would discard all of them over this one.
            #
            # It reaches here only on a TRUNCATED reply. A transcript whose offset genuinely cannot
            # be established still returns None and summarizes normally without citations, so this
            # branch is not the ordinary "no page numbers" case - it is the case where we do not
            # know whether there were page numbers to read.
            logger.warning(
                "bundle: transcript page numbers unreadable for pages %s-%s; that document is "
                "omitted",
                row.get("start"),
                row.get("end"),
            )
            skipped_causes.add(TranscriptPagesUnreadableError)
            continue
        entries.append(
            {
                "summaryDate": output.get("summaryDate") or "-",
                "summaryTitle": summarize_engine.presentable_title(output["summaryTitle"]),
                "summaryText": output["summaryText"],
                "diagnostic": is_diagnostic(row.get("category")),
            }
        )
    if rows and not entries:
        # EVERY row was skipped. Skipping per-row must not turn a clear error into a silently empty
        # deliverable: without this the caller hands `[]` to `build_mrr_document` and streams a Word
        # file containing a letterhead and no summaries, with a 200. Re-raising restores the 422
        # that the un-isolated loop produced, which is the honest answer when there is nothing to
        # deliver.
        #
        # WHICH error is raised follows what actually happened, rather than defaulting to the blank
        # case. A bundle of depositions whose page numbers all failed to read is not "no readable
        # text was found" - that message sends the reader looking at the scan quality of pages that
        # read fine. Only a single, unanimous cause can be named; a mixture falls back to the
        # blank-text message, which is the one that describes the commonest reason a row is dropped.
        if skipped_causes == {TranscriptPagesUnreadableError}:
            raise TranscriptPagesUnreadableError(
                f"transcript page numbers unreadable in all {len(rows)} matching documents"
            )
        raise EmptyExtractionError(f"no readable text in any of the {len(rows)} matching documents")
    # One spelling per provider across the bundle, as the record export does.
    titles = summarize_engine.consistent_authors([e["summaryTitle"] for e in entries])
    return [{**e, "summaryTitle": t} for e, t in zip(entries, titles, strict=True)]
