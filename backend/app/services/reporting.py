"""MRR Word-document assembly (ported from the Flask export blueprint; python-docx, Flask-free).

`build_mrr_document` is shared by the export + bundle-summarize routes: it sorts summary entries
chronologically and assembles the letterhead + intro + per-record body into a python-docx
Document. The classic CSV/on-disk export routes are dropped.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The three client-facing sentences of the letter live HERE ONLY, and both renderers import them:
# this module builds the Word document, `linked_pdf` builds the same letter as HTML for the combined
# PDF. They used to hold their own copy of each string, and the copies had already drifted - the
# double space in "pages  received" was in the Word copy alone, so the delivered .docx and the
# delivered .pdf disagreed on a sentence the client reads. Two copies of one sentence silently
# diverging is the mechanism, not the typo, so there is one home for the text and a test that pins
# both renderers to it.
SUMMARY_INTRO = "The following is a summary of those records:"
CONCLUSION = "This concludes the review of submitted records."

# Same reason as the three sentences above: both renderers held their own copy and the copies had
# ALREADY diverged. Checked against the eight human deliverables on disk, which are the standard these
# artifacts are supposed to match:
#
#   heading    "MEDICAL RECORD REVIEW" in 8 of 8 files. The Word renderer said "Medical Record
#              Review"; the PDF renderer already had it in caps.
#   separator  329 date-anchored entries across those 8 files, every one a PERIOD after the title
#              and not one colon. The Word renderer emitted ": "; the PDF renderer already used ". ".
#
# So on both counts the .docx disagreed with the .pdf AND with the human standard, and the .docx is
# the primary deliverable. Centralised here so the next change moves both renderers at once.
REVIEW_HEADING = "MEDICAL RECORD REVIEW"
TITLE_SEPARATOR = ". "
_REPORT_FONT = "Times New Roman"


def header_lines(patient_name, patient_dob) -> tuple[str, str]:
    """The two identifying lines both renderers put at the top of every page.

    Here for the same reason as the sentences above, and it had already gone the same way: the Word
    header wrote the date of birth as a bare value while the linked PDF labelled it ``DOB:``, so the
    two deliverables named the patient differently on every page. `RE:` was labelled in both, which
    is what makes the unlabelled one read as an omission rather than a house style.
    """
    return f"RE: {patient_name}", f"DOB: {patient_dob}"


def intro_sentence(num_pages, lawfirm) -> str:
    """The letter's opening sentence, shared by the Word and linked-PDF renderers.

    The law firm is OPTIONAL - free text on the review page that a reviewer often has no value for.
    Concatenating it unconditionally shipped "medical records from ." into the delivered document,
    dangling preposition and orphan full stop, every time the field was blank; seen in a real export
    on 2026-08-17. So the clause is DROPPED rather than rendered empty - an absent element is left
    out, the convention the title prompt already applies to a missing author. ""/whitespace/None all
    count as absent, which is how the value actually arrives from the form.

    Returns plain text. The PDF renderer escapes the ASSEMBLED sentence for HTML rather than
    escaping `lawfirm` before it gets here, so the escape still covers any field added later.
    """
    firm = (lawfirm or "").strip()
    received_from = f" from {firm}" if firm else ""
    return (
        f"I have received {num_pages} pages of medical records{received_from}. "
        "I have reviewed all of the pages received and my opinion is based upon such "
        "received records."
    )


@dataclass(frozen=True)
class RecordAccounting:
    """What the tail of the letter says about the pages that were received.

    Both reference documents the reviewers sent (the outside MRR and its covering memo) close
    with the same three facts, and ours closed with none of them. They are also the sentences
    this project has spent months PARSING out of human deliverables to score itself against -
    "Of the N pages received, exactly M pages were remarked upon" is the exclusion sentence
    every categorization rule since #134 rests on. Generating it is the same arithmetic read
    the other way round.

    THE THREE BUCKETS ARE DISJOINT, and that is not a stylistic choice. On the one record whose
    sentence reconciles exactly, remarked + other + duplicates equals the total received, which
    is only true if a duplicated page is counted ONCE - in the duplicate bucket - rather than
    also appearing under other documents. So a non-primary duplicate row is removed from the
    excluded set rather than counted twice, and `duplicate_pages` counts pages BEYOND the first
    copy rather than every page in the group.
    """

    pages_received: int
    pages_remarked: int
    excluded_types: tuple[str, ...]
    duplicate_pages: int

    @property
    def pages_other(self) -> int:
        """Pages that are neither remarked upon nor a duplicate copy. Never negative: the
        three counts come from one partition of the same rows, but `pages_received` is the
        PDF's own page count and a row set that does not cover the document would otherwise
        produce a negative remainder in the delivered sentence."""
        return max(0, self.pages_received - self.pages_remarked - self.duplicate_pages)


def _row_pages(row) -> int:
    """Pages a row spans, inclusive of both ends, and never negative."""
    return max(0, (row.end or 0) - (row.start or 0) + 1)


def record_accounting(rows, pages_received: int) -> RecordAccounting:
    """Partition the reviewer's rows into remarked / other / duplicate.

    Takes ORM rows rather than `as_row()` dicts deliberately: `ROW_FIELDS` carries neither
    `include` nor the duplicate columns, which is the same omission #258 had to work around,
    so a dict would not carry the two fields this needs.

    A non-primary member of a confirmed duplicate group is a duplicate FIRST, whatever its
    include flag says - `resolve_duplicate` leaves those rows excluded, so counting them as
    "other documents" as well is exactly the double count the disjoint arithmetic forbids.
    """
    remarked = duplicate = 0
    excluded: list[str] = []
    for row in rows:
        pages = _row_pages(row)
        if row.dupe_group is not None and not row.dupe_primary:
            duplicate += pages
            continue
        if row.include:
            remarked += pages
            continue
        title = (row.title or "").strip()
        if title:
            excluded.append(title)
    seen: dict[str, None] = {}
    for title in excluded:
        # Case-folded for de-duplication only; the FIRST spelling is what ships, because the
        # reference list is lower case prose and a title may legitimately carry an acronym.
        seen.setdefault(title.casefold(), None)
        seen[title.casefold()] = seen[title.casefold()] or title
    ordered = tuple(dict.fromkeys(v for v in seen.values() if v))
    return RecordAccounting(
        pages_received=max(0, pages_received or 0),
        pages_remarked=remarked,
        excluded_types=ordered,
        duplicate_pages=duplicate,
    )


def accounting_sentences(accounting: RecordAccounting | None) -> tuple[str, str]:
    """The two bold sentences that close the letter, or "" for one that does not apply.

    CONDITIONAL, and that is observed rather than assumed: the reference MRR carries a
    duplicates sentence and the two supplemental reports for another patient carry none at all,
    so a count of zero means the sentence is absent, not that it reads "0 pages".

    Returned as text and rendered by each caller, so the Word and PDF renderers cannot drift
    apart on the WORDS the way they twice drifted on formatting (#158, #268).
    """
    if accounting is None or not accounting.pages_received:
        return "", ""
    exclusion = (
        f"Of the {accounting.pages_received} pages received, exactly "
        f"{accounting.pages_remarked} pages were remarked upon, as the remaining "
        f"{accounting.pages_other} pages are other documents such as:"
    )
    duplicates = (
        f"In addition, the records included {accounting.duplicate_pages} pages of duplicate "
        "copies of records already counted above."
        if accounting.duplicate_pages
        else ""
    )
    return exclusion, duplicates


def summary_intro(lawfirm=None) -> str:
    """The line that introduces the entries.

    The reference document names the sending firm - "The following is a summary of records from
    <firm>:" - where ours said only "those records". The firm is OPTIONAL free text a reviewer
    often has no value for, so the clause is DROPPED rather than rendered empty, which is the
    convention `intro_sentence` already applies after #115 shipped "records from ." to a client.
    """
    firm = (lawfirm or "").strip()
    return f"The following is a summary of records from {firm}:" if firm else SUMMARY_INTRO


# Inline emphasis the summarizer emits: **bold**, *italic*, _italic_. Rendered as real runs so no
# raw markers leak into the Word document.
#
# THREE renderers read this - here, `linked_pdf._inline_html`, and the web's `MarkdownText` - and
# each held its own copy. `linked_pdf` now imports this one; the web's is in TypeScript and cannot,
# so `markdown-text.tsx` carries a comment naming this as the definition it tracks. The same file
# pair has diverged twice before (#158 on the heading and separator, #268 on the letter's
# alignment), which is why this is imported rather than repeated a third time.
#
# NOT re.DOTALL, and that is the fix rather than an omission. With it, `\*(.+?)\*` pairs a BULLET on
# one line with the bullet on the next - `* item` / `* item` - and italicises everything between.
# Measured over 3,017 stored summaries: 3 disagreed with the web renderer, and 6 of the 9 offending
# spans opened with "* " (a bullet, not emphasis), italicising 83 to 387 characters of a delivered
# document. The two remaining `**` spans were a wrapped bold heading, which now shows its markers
# exactly as the review screen already shows them - visible to the reviewer and editable, which a
# silently italicised paragraph is not. Emphasis does not cross a line break.
INLINE_EMPHASIS_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_")


def _run(paragraph, s, *, bold=False, italic=False, size=None):
    run = paragraph.add_run(s)
    run.bold = bold
    run.italic = italic
    run.font.name = _REPORT_FONT
    run.font.size = size or Pt(11)
    return run


def _add_inline_runs(paragraph, text, *, bold=False, italic=False):
    """Append runs to ``paragraph``, turning **bold** / *italic* / _italic_ markers into real
    formatting; ``bold``/``italic`` set the baseline for the plain segments."""
    pos = 0
    for m in INLINE_EMPHASIS_RE.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos : m.start()], bold=bold, italic=italic)
        if m.group(1) is not None:
            _run(paragraph, m.group(1), bold=True, italic=italic)
        else:
            _run(paragraph, m.group(2) or m.group(3), bold=bold, italic=True)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold=bold, italic=italic)


def _page_number_field(paragraph) -> None:
    """Append a real Word PAGE field, so the header numbers the pages.

    The header used to end with the literal string ``"Page "`` and nothing after it: python-docx
    writes text, not fields, so every page of the delivered .docx carried a dangling label with no
    number while the linked PDF numbered its pages properly. A field rather than a computed integer
    because a Word header is one object repeated on every page - there is no per-page text to write,
    which is presumably how the bare label got there.

    ``w:fldSimple`` with a cached ``1`` inside: Word and LibreOffice both recalculate the field on
    open, and the cached run is what a reader that does not evaluate fields shows instead of nothing.
    """
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), " PAGE ")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def _fill_header(header, re_line, dob_line, *, numbered: bool) -> None:
    """Write the identifying lines into ``header``, optionally followed by ``Page <n>``.

    Writes into the header's EXISTING first paragraph rather than adding one. A new Word header
    already carries an empty paragraph, so `add_paragraph` left a blank line above the patient's
    name on every page of the deliverable.
    """
    paragraph = header.paragraphs[0]
    # Removing the run ELEMENTS, not `paragraph.text = ""` - that assignment leaves one empty run
    # behind, so the first run of the header carried no font at all and `runs[0]` was not the text.
    # Safe to iterate while deleting: `runs` builds a fresh list from the XML on every access, so
    # this is already a snapshot and the copy that used to wrap it added nothing.
    for run in paragraph.runs:
        run._element.getparent().remove(run._element)
    _run(paragraph, f"{re_line}\n{dob_line}" + ("\nPage " if numbered else ""), size=Pt(10))
    if numbered:
        _page_number_field(paragraph)


UNDATED_LABEL = "Undated"


def parsed_date(entry):
    """The entry's date as a datetime, or None when it states none.

    ONE definition of "undated", shared by the sort key and the label. Written separately they
    disagreed: an entry dated "n/a" sorted last (unparseable) but rendered the raw "n/a", because the
    label only special-cased "-" and "". So an entry could sort as undated while displaying junk. Not
    reachable from today's field spec, which only ever writes "-", but two definitions of one concept
    drift eventually.
    """
    try:
        return datetime.strptime((entry.get("summaryDate") or "").strip(), "%m/%d/%Y")
    except ValueError:
        return None


def date_label(entry) -> str:
    """The date cell text: the date exactly as written, or "Undated".

    The field spec writes "-" when a document carries no date, which in a finished deliverable reads
    as a value nobody filled in rather than a fact about the document. The reviewers call these
    Undated and expect them at the end of the review.

    A parsed date is returned VERBATIM, never reformatted: the factuality rules say copy dates
    exactly, and a reviewer compares them against the page.
    """
    if parsed_date(entry) is None:
        return UNDATED_LABEL
    return (entry.get("summaryDate") or "").strip()


def build_mrr_document(
    entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm, accounting=None
):
    """Assemble the MRR Word document from summary ``entries`` (sorted chronologically).

    ``accounting`` is OPTIONAL and defaults to the previous behaviour - no closing
    page-accounting sentences. The bundle export builds a letter from rows it selected by
    category rather than from a whole record, where a sentence about the pages RECEIVED
    would be answering a question nobody asked, so it passes nothing and is unchanged.
    """

    # Undated entries sort LAST, per the reviewers 2026-08-21: "if it is something important we will
    # still summarize it, it can go at the end of the Review as Undated". This was datetime.min -
    # undated FIRST - so a document stating no date sorted ahead of the earliest real encounter and
    # the deliverable could OPEN on one.
    entries = sorted(entries, key=lambda e: parsed_date(e) or datetime.max)

    doc = Document()

    # HEADER
    section = doc.sections[0]
    # A separate first-page header, so page 1 carries the patient lines WITHOUT a page number and
    # every later page carries all three. That is what `linked_pdf._draw_running_header` already
    # does (`"" if i == 0 else f"\nPage {i + 1}"`), and matching it is the point: the two are the
    # same deliverable in two formats and a reviewer reads them side by side.
    section.different_first_page_header_footer = True
    re_line, dob_line = header_lines(patient_name, patient_dob)
    _fill_header(section.first_page_header, re_line, dob_line, numbered=False)
    _fill_header(section.header, re_line, dob_line, numbered=True)

    doc.add_paragraph("")

    # TITLE. An empty paragraph has no runs, so runs[0] below would crash on a blank
    # QME/AME field (the form allows leaving it empty).
    title = doc.add_paragraph(qme_or_ame or " ")
    title_format = title.runs[0]
    title_format.bold = True
    title_format.underline = True
    title_format.font.size = Pt(12)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    title_format.font.name = _REPORT_FONT

    doc.add_paragraph("")

    second_title = doc.add_paragraph(REVIEW_HEADING)
    second_title_format = second_title.runs[0]
    second_title_format.bold = True
    second_title_format.underline = True
    second_title_format.font.size = Pt(12)
    second_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    second_title_format.font.name = _REPORT_FONT

    intro_text = intro_sentence(num_pages, lawfirm)
    second_intro_text = summary_intro(lawfirm)
    this_concludes_text = CONCLUSION

    third_title = doc.add_paragraph(intro_text)
    third_title_format = third_title.runs[0]
    third_title_format.bold = False
    third_title_format.underline = False
    third_title_format.font.size = Pt(12)
    third_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    third_title_format.font.name = _REPORT_FONT

    fourth_title = doc.add_paragraph(second_intro_text)
    fourth_title_format = fourth_title.runs[0]
    fourth_title_format.bold = True
    fourth_title_format.underline = False
    fourth_title_format.font.size = Pt(12)
    fourth_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    fourth_title_format.font.name = _REPORT_FONT

    # Two-column borderless table: date | title + body. The default "Table Normal" style has no
    # cell borders, matching the canonical MRR summary layout (date sits in its own left column,
    # the summary flows in the right column) instead of the old inline date-tab-title paragraph.
    table = doc.add_table(rows=0, cols=2)
    table.autofit = False
    for entry in entries:
        cells = table.add_row().cells
        cells[0].width = Inches(0.9)
        cells[1].width = Inches(5.6)
        cells[0].vertical_alignment = WD_ALIGN_VERTICAL.TOP
        cells[1].vertical_alignment = WD_ALIGN_VERTICAL.TOP
        _run(cells[0].paragraphs[0], date_label(entry))
        body = cells[1].paragraphs[0]
        # Justified: the report is read as a finished document, and a ragged right edge on every
        # record is what made the export look like a draft.
        body.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        _add_inline_runs(body, entry["summaryTitle"], bold=True)
        _run(body, TITLE_SEPARATOR)
        _add_inline_runs(body, entry["summaryText"])

    # THE PAGE ACCOUNTING, between the entries and the conclusion - the position both
    # reference documents put it in. Bold, because they bold all three of these sentences
    # while leaving the type list beneath plain; that contrast is the whole of the house
    # style here and rendering the list bold too would lose it.
    exclusion_text, duplicates_text = accounting_sentences(accounting)
    if exclusion_text:
        exclusion = doc.add_paragraph()
        exclusion.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        _run(exclusion, exclusion_text, bold=True, size=Pt(12))
        for document_type in accounting.excluded_types:
            item = doc.add_paragraph()
            item.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
            _run(item, document_type, size=Pt(12))
    if duplicates_text:
        duplicates = doc.add_paragraph()
        duplicates.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        _run(duplicates, duplicates_text, bold=True, size=Pt(12))

    # `nine_title_format` read `fourth_title.runs[0]` - the SUMMARY_INTRO paragraph's run, not this
    # one. Two visible defects in the delivered .docx from one wrong name: the conclusion got no
    # formatting at all and shipped in python-docx's default Calibri 11 while every other paragraph
    # is Times New Roman 12, and `bold = False` here UNDID the `bold = True` set on SUMMARY_INTRO
    # thirty lines above. `linked_pdf` renders that sentence with `font-weight:bold`, so the .docx and
    # the .pdf disagreed on the formatting of a sentence the client reads - the same drift this
    # module's docstring records for the sentence TEXT, one layer down.
    #
    # ruff had already found it: `nine_title` was assigned and never used (F841), which is exactly
    # the bug, and the warning was silenced with a noqa rather than fixed.
    #
    # `alignment` is a PARAGRAPH property, so it is set on the paragraph here and on `fourth_title`
    # above. Assigning it to a run is silently a no-op; both wanted LEFT, which is also the default,
    # so nothing was visible - but the next paragraph wanting CENTER would fail the same way.
    nine_title = doc.add_paragraph(this_concludes_text)
    nine_title_format = nine_title.runs[0]
    nine_title_format.bold = False
    nine_title_format.underline = False
    nine_title_format.font.size = Pt(12)
    nine_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    nine_title_format.font.name = _REPORT_FONT

    return doc
