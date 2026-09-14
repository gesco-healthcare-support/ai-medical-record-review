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

# Each evaluator reads their reports in their own typeface, so the Word document is written
# in theirs. Supplied by the reviewers 2026-09-14, verbatim except that `Amasis MT Pro medium`
# is spelled with the capital the font itself carries.
#
# WORD ONLY, and that is the reviewers' own decision: python-docx writes the font NAME and the
# reader's Word resolves it, so nothing has to be installed here. The linked PDF is rendered
# by us and would need the real font files - five of these are licensed - and they answered
# that the PDF "does not need the font on that one, the rest of the files are for internal
# use". So `build_linked_pdf` deliberately does not take a doctor.
#
# The single source of truth for BOTH halves: the API serves `DOCTORS` to the dropdown and
# this module reads the font. Two lists would drift, which is the failure this repo keeps
# finding.
DOCTOR_FONTS: dict[str, str] = {
    "Falkinstein": "Times New Roman",
    "Pelton": "Tahoma",
    "Longacre": "Calibri",
    "Mikhael": "Century Gothic",
    "Hekmat": "Arial",
    "Andersen": "Georgia",
    "Nguyen": "Amasis MT Pro Medium",
    "Ziv": "Aptos Serif",
    "Grossman": "Aptos",
    "Perez": "Abadi",
    "Ahdoot": "Bierstadt Display",
}

DOCTORS: tuple[str, ...] = tuple(DOCTOR_FONTS)


def report_font(doctor: str | None) -> str:
    """The typeface for ``doctor``, or the house default for an unknown or absent one.

    Falls back rather than raising: the field is free-form on the way in, a doctor could be
    removed from the list while records still name them, and a delivered document in the
    wrong font is a smaller failure than an export that refuses."""
    return DOCTOR_FONTS.get((doctor or "").strip(), _REPORT_FONT)


# The covering letter that arrived WITH the records. `advocacy` is the initial request and
# `interrogatory` the supplemental one - the reviewers' own distinction, 2026-09-14. `none`
# is a real answer rather than a missing value: plenty of records arrive with no letter, and
# the opening paragraph then omits the clause entirely.
LETTER_TYPES: tuple[str, ...] = ("advocacy", "interrogatory", "none")

# The article travels WITH the label because it changes: `a defense advocacy letter` but
# `an interrogatory letter`. Computing it from the first letter would be a rule that happens
# to work on two values and breaks on the third someone adds.
LETTER_LABELS: dict[str, str] = {
    "advocacy": "a defense advocacy letter",
    "interrogatory": "an interrogatory letter",
}

# The disclosure the reviewers' own reports carry, verbatim from the format they sent. Fixed
# text: they were asked what goes in the credential slot and answered "Trained Medical Record
# Processor" - one string for everyone rather than a qualification per person.
#
# It names who did the record work, and it cites the statute that requires saying so. The
# reviewers flagged that this part may still need Patrick and Richard, so it is emitted ONLY
# when a reviewer name is supplied and is absent by default - a legal assertion with a blank
# name in it is not something to ship on a guess.
REVIEWER_CREDENTIAL = "Trained Medical Record Processor"

LABOR_CODE_CITE = "(California Labor Code \u00a7 4628(b)(c))"


@dataclass(frozen=True)
class ReportDetails:
    """The record-level facts the opening paragraph and the typeface need.

    One object rather than five more parameters because they travel together, arrive from one
    place - the header a reviewer fills in - and are read by one caller. Every field is
    optional and empty means absent, so the default instance renders the document exactly as
    it rendered before any of this existed."""

    doctor: str = ""
    attorney_name: str = ""
    letter_type: str = ""
    letter_date: str = ""
    reviewer_name: str = ""


def header_lines(patient_name, patient_dob) -> tuple[str, str]:
    """The two identifying lines both renderers put at the top of every page.

    Here for the same reason as the sentences above, and it had already gone the same way: the Word
    header wrote the date of birth as a bare value while the linked PDF labelled it ``DOB:``, so the
    two deliverables named the patient differently on every page. `RE:` was labelled in both, which
    is what makes the unlabelled one read as an omission rather than a house style.
    """
    return f"RE: {patient_name}", f"DOB: {patient_dob}"


def _sender_clause(attorney_name, lawfirm) -> str:
    """The sender: ` from <person>, of <firm>`, or whichever of the two is known, or nothing.

    Both are optional free text on the review page, and the clause is DROPPED rather than
    rendered empty: concatenating the firm unconditionally shipped "medical records from ."
    into a real export on 2026-08-17, dangling preposition and orphan full stop.

    `from <person>, of <firm>` only reads as intended with BOTH, so a person with no firm
    is named alone rather than shipped with a dangling "of"."""
    person = (attorney_name or "").strip()
    firm = (lawfirm or "").strip()
    if person and firm:
        return f" from {person}, of {firm}"
    if person or firm:
        return f" from {person or firm}"
    return ""


def _letter_clause(letter_type, letter_date) -> str:
    """The covering letter: `a defense advocacy letter dated 08/12/2026 along with `, or nothing.

    A letter with no date still gets its clause, because the TYPE is the fact worth stating
    and "dated" with nothing after it is worse than no date at all.

    A type with no label prints nothing at all. That covers `none`, which is a real answer
    rather than a missing value - it is how a reviewer says they checked and there was no
    letter, which is different from not having been asked."""
    label = LETTER_LABELS.get((letter_type or "").strip())
    if not label:
        return ""
    date = (letter_date or "").strip()
    return f"{label} dated {date} along with " if date else f"{label} along with "


def intro_sentence(
    num_pages,
    lawfirm,
    *,
    attorney_name="",
    letter_type="",
    letter_date="",
    reviewer_name="",
) -> str:
    """The letter's opening paragraph, shared by the Word and linked-PDF renderers.

    Follows the format the reviewers supplied on 2026-09-14, and every added element is
    CONDITIONAL - with no keyword arguments this returns what it always returned, so a record
    that predates the new header fields is unchanged.

    Each clause is dropped rather than rendered empty; `""`/whitespace/None all count as absent,
    which is how these arrive from the form.

    The two optional clauses are assembled by `_sender_clause` and `_letter_clause`, which
    carry the rules for dropping each one.

    The Labor Code sentences appear ONLY with a reviewer name. They are a legal assertion about
    who performed the record work, and emitting one with a blank name in it is not a guess to
    make - the reviewers flagged that this part may still need Patrick and Richard.

    Returns plain text. The PDF renderer escapes the ASSEMBLED string for HTML rather than
    escaping the fields before they get here, so the escape still covers any field added later.
    """
    received_from = _sender_clause(attorney_name, lawfirm)
    letter = _letter_clause(letter_type, letter_date)

    opening = (
        f"I have received {letter}{num_pages} pages of medical records{received_from}. "
        "I have reviewed all of the pages received and my opinion is based upon such records."
    )

    reviewer = (reviewer_name or "").strip()
    if not reviewer:
        return opening
    return (
        f"{opening} The initial organization, outlining, and excerpting of medical records "
        f"were performed by {reviewer}, {REVIEWER_CREDENTIAL}. I personally reviewed the "
        "excerpts, the entire outline, and the pages that were received, making additional "
        "inquiries and examinations as necessary to determine the relevant medical issues. "
        f"{LABOR_CODE_CITE}"
    )


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


def _run(paragraph, s, *, bold=False, italic=False, size=None, font=None):
    run = paragraph.add_run(s)
    run.bold = bold
    run.italic = italic
    run.font.name = font or _REPORT_FONT
    run.font.size = size or Pt(11)
    return run


def _add_inline_runs(paragraph, text, *, bold=False, italic=False, font=None):
    """Append runs to ``paragraph``, turning **bold** / *italic* / _italic_ markers into real
    formatting; ``bold``/``italic`` set the baseline for the plain segments."""
    pos = 0
    for m in INLINE_EMPHASIS_RE.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos : m.start()], bold=bold, italic=italic, font=font)
        if m.group(1) is not None:
            _run(paragraph, m.group(1), bold=True, italic=italic, font=font)
        else:
            _run(paragraph, m.group(2) or m.group(3), bold=bold, italic=True, font=font)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold=bold, italic=italic, font=font)


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


def _letter_paragraph(doc, text, *, bold=False, underline=False, centered=False, font=None):
    """One styled paragraph of the letter: its whole run formatting decided in one place.

    Five paragraphs each styled their own run with the same six lines, and the copies had
    already drifted. `nine_title_format = fourth_title.runs[0]` read the WRONG paragraph's
    run, which cost two visible defects in one name: the conclusion got no formatting at all
    and shipped in python-docx's default Calibri 11 while every other paragraph is Times New
    Roman 12, and its `bold = False` UNDID the `bold = True` set on SUMMARY_INTRO thirty lines
    above - so the .docx and the .pdf disagreed on the formatting of a sentence the client
    reads. ruff had already found it (F841, a local assigned and never used, which IS the bug)
    and it was silenced with a noqa rather than fixed. One helper is what stops the sixth
    paragraph being written by hand.

    `alignment` is a PARAGRAPH property, so it is set on the paragraph and not on the run;
    assigning it to a run is silently a no-op. Both were done at the two call sites that
    wanted LEFT, which is also the default - so nothing was visible, but the CENTER one would
    have failed the same way."""
    paragraph = doc.add_paragraph("")
    run = _run(paragraph, text, bold=bold, size=Pt(12), font=font)
    run.underline = underline
    paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER if centered else WD_PARAGRAPH_ALIGNMENT.LEFT
    return paragraph


def _fill_header(header, re_line, dob_line, *, numbered: bool, font=None) -> None:
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
    _run(
        paragraph,
        f"{re_line}\n{dob_line}" + ("\nPage " if numbered else ""),
        size=Pt(10),
        font=font,
    )
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
    entries,
    num_pages,
    patient_name,
    patient_dob,
    qme_or_ame,
    lawfirm,
    *,
    details: ReportDetails | None = None,
):
    """Assemble the MRR Word document from summary ``entries`` (sorted chronologically).

    ``details`` carries the doctor and the covering letter. Omitted, the document renders
    exactly as it did before those fields existed."""
    details = details or ReportDetails()
    font = report_font(details.doctor)

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
    _fill_header(section.first_page_header, re_line, dob_line, numbered=False, font=font)
    _fill_header(section.header, re_line, dob_line, numbered=True, font=font)

    doc.add_paragraph("")

    # TITLE. The form allows an empty QME/AME field, and this used to guard a crash:
    # `title.runs[0]` on a paragraph built from an empty string raises, because an empty
    # paragraph has no runs. `_letter_paragraph` ADDS the run itself, so that is gone -
    # measured, not assumed. The fallback is kept only so the emitted document does not
    # change: both render as a blank line, but one writes a space and one writes nothing.
    _letter_paragraph(doc, qme_or_ame or " ", bold=True, underline=True, centered=True, font=font)

    doc.add_paragraph("")

    _letter_paragraph(doc, REVIEW_HEADING, bold=True, underline=True, font=font)
    _letter_paragraph(
        doc,
        intro_sentence(
            num_pages,
            lawfirm,
            attorney_name=details.attorney_name,
            letter_type=details.letter_type,
            letter_date=details.letter_date,
            reviewer_name=details.reviewer_name,
        ),
        font=font,
    )
    _letter_paragraph(doc, SUMMARY_INTRO, bold=True, font=font)

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
        _run(cells[0].paragraphs[0], date_label(entry), font=font)
        body = cells[1].paragraphs[0]
        # Justified: the report is read as a finished document, and a ragged right edge on every
        # record is what made the export look like a draft.
        body.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        _add_inline_runs(body, entry["summaryTitle"], bold=True, font=font)
        _run(body, TITLE_SEPARATOR, font=font)
        _add_inline_runs(body, entry["summaryText"], font=font)

    # This paragraph is why `_letter_paragraph` exists - it used to style the WRONG run, and
    # the helper's docstring records what that cost.
    _letter_paragraph(doc, CONCLUSION, font=font)

    return doc
