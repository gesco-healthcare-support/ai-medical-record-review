"""P3a: MRR Word-document assembly (python-docx; no DB, no network)."""

import html
import io
import re
from dataclasses import dataclass

import pytest
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.shared import Pt

from app.services.reporting import (
    CONCLUSION,
    DOCTOR_FONTS,
    DOCTORS,
    DOCX_MIMETYPE,
    LETTER_LABELS,
    LETTER_TYPES,
    MEMO_GREETING,
    MEMO_SOURCES_LEAD,
    MEMO_VERIFIED_BY,
    REVIEW_HEADING,
    SUMMARY_INTRO,
    TITLE_SEPARATOR,
    UNDATED_LABEL,
    MemoDetails,
    RecordAccounting,
    ReportDetails,
    accounting_sentences,
    build_memo_document,
    build_mrr_document,
    date_label,
    intro_sentence,
    memo_opening,
    page_count_note,
    record_accounting,
    report_font,
    summary_intro,
)


def test_build_mrr_document_saves():
    entries = [
        {"summaryDate": "01/02/2020", "summaryTitle": "Report A", "summaryText": "text A"},
        {"summaryDate": "03/04/2019", "summaryTitle": "Report B", "summaryText": "text B"},
    ]
    doc = build_mrr_document(
        entries,
        num_pages=42,
        patient_name="Synthetic Patient",
        patient_dob="-",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    assert buffer.tell() > 0
    assert DOCX_MIMETYPE.endswith("wordprocessingml.document")


def _intro(doc) -> str:
    """The 'I have received N pages...' sentence, wherever it sits in the document."""
    return next(p.text for p in doc.paragraphs if p.text.startswith("I have received"))


def test_intro_names_the_law_firm_when_there_is_one():
    """The closing words moved from "such received records" to "such records" on 2026-09-14.

    Not a tidy-up: the reviewers supplied the paragraph verbatim and asked us to fit it, and
    theirs reads "such records". Recorded here rather than quietly swapped, because this is a
    pinned wording in a delivered document and the pin is being overruled on their say-so."""
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )
    assert _intro(doc) == (
        "I have received 8 pages of medical records from Example Law Firm. I have reviewed all "
        "of the pages received and my opinion is based upon such records."
    )


def test_intro_drops_the_clause_when_the_law_firm_is_blank():
    """A blank firm must not ship "medical records from ." into the delivered document.

    The field is optional free text on the review page and is routinely empty, so the old
    unconditional concatenation put a dangling preposition and an orphan full stop in front of every
    such export. Seen in a real one on 2026-08-17. An absent element is left out, which is the
    convention the title prompt already applies to a missing author.

    Asserted on both the presence of the correct sentence and the absence of the broken fragment,
    because a future edit could satisfy one without the other.
    """
    for blank in ("", "   ", None):
        doc = build_mrr_document(
            [],
            num_pages=8,
            patient_name="",
            patient_dob="",
            qme_or_ame="",
            details=ReportDetails(lawfirm=blank),
        )
        text = _intro(doc)
        assert text == (
            "I have received 8 pages of medical records. I have reviewed all of the pages "
            "received and my opinion is based upon such records."
        )
        assert "from ." not in text
        assert "records from" not in text


def test_intro_has_no_double_space():
    """ "all of the pages  received" carried a double space in the shipped template."""
    doc = build_mrr_document(
        [],
        num_pages=1,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm="Firm"),
    )
    assert "  " not in _intro(doc)


def test_build_mrr_document_blank_qme_ame_does_not_crash():
    # A blank QME/AME field must not crash (an empty paragraph has no runs -> guarded with " ").
    doc = build_mrr_document(
        [],
        num_pages=1,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm=""),
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    assert buffer.tell() > 0


def test_build_mrr_document_renders_two_column_table():
    # Entries render as a borderless 2-column table (date | title + text), sorted chronologically.
    entries = [
        {"summaryDate": "01/02/2020", "summaryTitle": "Report A", "summaryText": "text A"},
        {"summaryDate": "03/04/2019", "summaryTitle": "Report B", "summaryText": "text B"},
    ]
    doc = build_mrr_document(
        entries,
        num_pages=2,
        patient_name="Synthetic Patient",
        patient_dob="-",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert len(table.columns) == 2
    assert len(table.rows) == 2
    # 03/04/2019 sorts before 01/02/2020; left cell = date, right cell = title + text.
    assert table.rows[0].cells[0].text == "03/04/2019"
    assert "Report B" in table.rows[0].cells[1].text
    assert "text B" in table.rows[0].cells[1].text


def test_summary_body_is_justified():
    # A finished report reads as justified prose; the date column stays left-aligned (default).
    entries = [
        {"summaryDate": "01/02/2020", "summaryTitle": "Report A", "summaryText": "text A"},
        {"summaryDate": "03/04/2019", "summaryTitle": "Report B", "summaryText": "text B"},
    ]
    doc = build_mrr_document(
        entries,
        num_pages=2,
        patient_name="Synthetic Patient",
        patient_dob="-",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )
    for row in doc.tables[0].rows:
        assert row.cells[1].paragraphs[0].alignment == WD_PARAGRAPH_ALIGNMENT.JUSTIFY
        assert row.cells[0].paragraphs[0].alignment != WD_PARAGRAPH_ALIGNMENT.JUSTIFY


# --------------------------------------------------------------------------------------------------
# Both renderers, one sentence. `reporting` builds the Word document and `linked_pdf` builds the same
# letter as HTML for the combined PDF; each used to carry its own copy of every client-facing string.
# The copies had drifted before anyone noticed - the double space in "pages  received" was in the Word
# copy alone - so these pin the two renderers to each OTHER rather than each to its own expected
# literal. A test that only checks each renderer against a hardcoded string passes happily while the
# two disagree, which is exactly how the delivered .docx and .pdf came to differ.


def _pdf_letter(num_pages, lawfirm) -> str:
    """The linked-PDF letter HTML. Imported lazily so a missing pymupdf skips instead of erroring."""
    linked_pdf = pytest.importorskip("app.services.linked_pdf")
    return linked_pdf._summary_html([], num_pages, "QME", ReportDetails(lawfirm=lawfirm))


@pytest.mark.parametrize("lawfirm", ["Example Law Firm", "Smith & Jones, LLP", "", "   ", None])
def test_both_renderers_emit_the_same_intro_sentence(lawfirm):
    """The one sentence, identical in both artifacts, for every shape the firm field arrives in.

    The PDF side is compared against the HTML-ESCAPED sentence because that renderer escapes the
    assembled string on its way into the letter markup - which is why "Smith & Jones, LLP" is in the
    parameter list. Same sentence, escaped for its medium.
    """
    expected = intro_sentence(8, lawfirm)
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm=lawfirm),
    )
    assert _intro(doc) == expected
    assert html.escape(expected) in _pdf_letter(8, lawfirm)


@pytest.mark.parametrize("lawfirm", ["", "   ", None])
def test_neither_renderer_ships_the_orphan_clause(lawfirm):
    """A blank firm must not put "medical records from ." in EITHER deliverable.

    The original fix only reached the Word document. The linked PDF interpolated the firm
    unconditionally, so a merge would have left the combined PDF - the artifact the defect was
    actually spotted in - still emitting it.
    """
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm=lawfirm),
    )
    letter = _pdf_letter(8, lawfirm)
    for text in (_intro(doc), letter):
        assert "from ." not in text
        assert "records from" not in text


def test_both_renderers_share_the_other_two_sentences():
    """The intro was the one with the bug; these two are the remaining copies of the mechanism."""
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="",
        patient_dob="",
        qme_or_ame="",
        details=ReportDetails(lawfirm="Firm"),
    )
    paragraphs = [p.text for p in doc.paragraphs]
    letter = _pdf_letter(8, "Firm")
    for sentence in (summary_intro("Firm"), CONCLUSION):
        assert sentence in paragraphs
        assert html.escape(sentence) in letter


# Undated entries, per the reviewers 2026-08-21: "if it is something important we will still
# summarize it, it can go at the end of the Review as Undated". Both renderers had it backwards - the
# sort key was datetime.min, so a document stating NO date sorted ahead of the earliest real
# encounter and the deliverable could OPEN on one - and the date cell showed the raw "-" the field
# spec writes, which reads as a value nobody filled in rather than a fact about the document.
def _entry(date, title="A REPORT", text="body text"):
    return {"summaryDate": date, "summaryTitle": title, "summaryText": text, "linkTitle": title}


@pytest.mark.parametrize("undated", ["-", "", "n/a", "   "])
def test_undated_entries_sort_last_in_the_word_document(undated):
    entries = [_entry(undated, "UNDATED"), _entry("01/02/2020", "EARLIEST"), _entry("03/04/2021")]
    doc = build_mrr_document(
        entries, 10, "A B", "01/01/1980", "QME", details=ReportDetails(lawfirm="Firm")
    )
    # One borderless table, one row per entry, NO header row - see
    # test_build_mrr_document_renders_two_column_table. Indexing from row 1 silently skips the
    # first entry, which is how the first version of this test passed for the wrong reason.
    dates = [row.cells[0].text for row in doc.tables[0].rows]

    assert dates == ["01/02/2020", "03/04/2021", UNDATED_LABEL]


@pytest.mark.parametrize("undated", ["-", "", "   ", "n/a", "unknown"])
def test_the_date_cell_says_undated_rather_than_a_dash(undated):
    """One definition of undated, shared by the sort and the label. Written separately they
    disagreed: "n/a" sorted last but rendered as "n/a"."""
    assert date_label({"summaryDate": undated}) == UNDATED_LABEL


def test_a_missing_key_is_undated_rather_than_a_crash():
    assert date_label({}) == UNDATED_LABEL


def test_a_real_date_is_left_exactly_as_written():
    """Copy dates EXACTLY - the factuality rules say so, and a reviewer compares them to the page."""
    assert date_label({"summaryDate": "01/02/2020"}) == "01/02/2020"


def test_both_renderers_share_the_label_and_the_ordering():
    """They produce the same deliverable in two formats and a reviewer compares them side by side, so
    the wording and the ordering must not drift. Same function object, not merely equal behaviour."""
    from datetime import datetime

    from app.services import linked_pdf

    assert linked_pdf.date_label is date_label
    assert linked_pdf._sort_key(_entry("-")) == datetime.max
    assert linked_pdf._sort_key(_entry("01/02/2020")) < datetime.max


# The two client-facing sentences below are formatted by the LAST block of `build_mrr_document`,
# which set every property on the WRONG paragraph's run: `nine_title_format = fourth_title.runs[0]`.
# Two visible consequences in the delivered .docx, and ruff had flagged the cause as F841 (a local
# assigned and never used) before it was silenced with a noqa instead of fixed.
def _paragraph_named(doc, text):
    return next(p for p in doc.paragraphs if p.text == text)


def test_the_summary_intro_stays_bold_in_the_word_document():
    """It is set bold, then the conclusion block un-bolds it by reusing the same run.

    `linked_pdf` renders this sentence with `font-weight:bold`, so the delivered .docx and the
    delivered .pdf disagreed on the formatting of a sentence the client reads - the same drift the
    module docstring records for the sentence TEXT, one layer down in the formatting.
    """
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    runs = _paragraph_named(doc, summary_intro("Firm")).runs
    assert runs, "the summary-intro paragraph has no runs"
    assert all(r.bold for r in runs), "the summary intro is not bold in the Word document"


def test_the_conclusion_is_formatted_like_the_rest_of_the_letter():
    """It got NO formatting, so the last sentence shipped in python-docx's default Calibri 11
    while every other paragraph is Times New Roman."""
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    runs = _paragraph_named(doc, CONCLUSION).runs
    assert runs, "the conclusion paragraph has no runs"
    for run in runs:
        assert run.font.name == "Times New Roman"
        assert run.font.size == Pt(12)
        assert not run.bold
        assert not run.underline


def test_paragraph_alignment_is_set_on_paragraphs_not_runs():
    """`alignment` is a paragraph property; assigning it to a run is silently a no-op.

    Two of these blocks set it on the run, so those paragraphs never had their alignment applied.
    Both happen to want LEFT, which python-docx also gives by default, so nothing was visible - but
    the next paragraph that wants CENTER would fail the same way and look like a docx quirk.
    """
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    for sentence in (summary_intro("Firm"), CONCLUSION):
        paragraph = _paragraph_named(doc, sentence)
        assert paragraph.alignment == WD_PARAGRAPH_ALIGNMENT.LEFT, (
            f"alignment never reached the paragraph for: {sentence!r}"
        )


def test_both_renderers_agree_that_the_summary_intro_is_bold():
    """The invariant that actually broke: not "is it bold in Word" but "do the two artifacts agree".

    Each renderer was individually self-consistent - the PDF hardcodes `font-weight:bold`, the Word
    side intended bold - and they still disagreed, because a later block in the Word assembly reused
    the wrong run and cleared it. Asserting the two sides together is what makes that visible.
    """
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    word_is_bold = all(r.bold for r in _paragraph_named(doc, summary_intro("Firm")).runs)

    letter = _pdf_letter(8, "Firm")
    escaped = html.escape(summary_intro("Firm"))
    paragraph = next(
        block for block in re.findall(r"<p[^>]*>.*?</p>", letter, re.S) if escaped in block
    )
    pdf_is_bold = "font-weight:bold" in paragraph.replace(" ", "")

    assert word_is_bold == pdf_is_bold, (
        "the delivered .docx and .pdf disagree on whether the summary intro is bold "
        f"(word={word_is_bold}, pdf={pdf_is_bold})"
    )
    assert word_is_bold, "both renderers agree, but on NOT bold - the intended style is bold"


def test_both_renderers_leave_the_letter_ragged_and_justify_only_the_bodies():
    """`reporting.py` draws a distinction the PDF collapsed: four explicit LEFT assignments for
    the letter paragraphs against one explicit JUSTIFY for the table bodies. `linked_pdf` set a
    blanket `p { text-align: justify }`, so the intro sentence shipped STRETCHED in the .pdf and
    ragged in the .docx - measured 14.5pt apart at the right edge of its first line.

    Only that sentence wraps at 157 characters; REVIEW_HEADING (21), SUMMARY_INTRO (44) and
    CONCLUSION (47) are one line each, and a paragraph's last line is never justified, so the
    other three were invisible. It is the third defect in this one sentence after #115 and #158.

    Geometry rather than CSS text, because the CSS is the thing under test: the justified bodies
    reach the measure, so a ragged letter paragraph must fall SHORT of it. That is page-size
    independent, which a hardcoded x-coordinate would not be.
    """
    linked_pdf = pytest.importorskip("app.services.linked_pdf")

    intro = intro_sentence(259, "Example Law Firm")
    entries = [
        {
            "summaryDate": "01/02/2020",
            "summaryTitle": "A REPORT",
            "linkTitle": "A REPORT",
            "summaryText": "Body sentence that has to wrap. " * 12,
            "dateLabel": "01/02/2020",
        }
    ]

    # Word: the letter paragraph is LEFT, the body cell is JUSTIFY.
    doc = build_mrr_document(
        entries,
        num_pages=259,
        patient_name="Synthetic Patient",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )
    word_letter = _paragraph_named(doc, intro).alignment
    word_body = doc.tables[0].rows[0].cells[1].paragraphs[0].alignment
    assert word_letter == WD_PARAGRAPH_ALIGNMENT.LEFT
    assert word_body == WD_PARAGRAPH_ALIGNMENT.JUSTIFY

    # PDF: same distinction, read off the rendered page.
    rendered, _ = linked_pdf._render_summary_pdf(
        linked_pdf._summary_html(entries, 259, "QME", ReportDetails(lawfirm="Example Law Firm"))
    )
    # Read the two COLUMNS off the page rather than matching on text: a wrapped body line starts
    # mid-sentence, so a text prefix is luck. The letter sits at the page margin and the table
    # body is indented past the date column, which separates them by left edge alone.
    wrapped = [
        line
        for block in rendered[0].get_text("dict")["blocks"]
        for line in block.get("lines", [])
        if len("".join(span["text"] for span in line["spans"]).strip()) >= 45
    ]
    assert wrapped, "nothing wrapped in the rendered PDF, so there is no alignment to read"
    margin = min(line["bbox"][0] for line in wrapped)
    letter = [line for line in wrapped if line["bbox"][0] < margin + 10]
    body = [line for line in wrapped if line["bbox"][0] >= margin + 10]

    intro_line = next(
        (
            line
            for line in letter
            if "".join(span["text"] for span in line["spans"]).lstrip().startswith(intro.split()[0])
        ),
        None,
    )
    assert intro_line is not None, "the intro sentence did not wrap in the rendered PDF"
    assert body, "no wrapped body line to read the measure from"
    letter_right = intro_line["bbox"][2]
    body_right = max(line["bbox"][2] for line in body)  # justified, so this IS the measure
    assert letter_right < body_right - 2, (
        "the .pdf stretches the intro sentence to the measure while the .docx leaves it ragged "
        f"(letter right edge {letter_right:.1f}, justified measure {body_right:.1f})"
    )


# The heading and the title separator were the two strings each renderer still held its own copy of,
# and the copies had already diverged. Both are checked against the eight human deliverables on disk:
# "MEDICAL RECORD REVIEW" in 8 of 8 files, and a PERIOD after the title in 329 of 329 date-anchored
# entries with not one colon. The Word renderer was wrong on both, the PDF renderer right on both.
def test_the_review_heading_is_the_form_the_human_deliverables_use():
    """8 of 8 human deliverables write it in caps. The Word renderer said "Medical Record Review"."""
    assert REVIEW_HEADING == "MEDICAL RECORD REVIEW"
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    paragraphs = [p.text for p in doc.paragraphs]
    assert REVIEW_HEADING in paragraphs
    assert "Medical Record Review" not in paragraphs, "the title-case heading is back"


def test_both_renderers_use_the_same_review_heading():
    doc = build_mrr_document(
        [],
        num_pages=8,
        patient_name="A B",
        patient_dob="01/01/1980",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    assert REVIEW_HEADING in [p.text for p in doc.paragraphs]
    assert html.escape(REVIEW_HEADING) in _pdf_letter(8, "Firm")


def test_the_title_is_separated_from_the_body_by_a_period_not_a_colon():
    """329 of 329 date-anchored human entries use a period. The Word renderer emitted ": "."""
    assert TITLE_SEPARATOR == ". "
    doc = build_mrr_document(
        [_entry("01/02/2020", "A REPORT", "body text")],
        10,
        "A B",
        "01/01/1980",
        "QME",
        details=ReportDetails(lawfirm="Firm"),
    )
    cell = doc.tables[0].rows[0].cells[1].text
    assert cell.startswith(f"A REPORT{TITLE_SEPARATOR}"), cell
    assert "A REPORT:" not in cell, "the colon separator is back"


def test_both_renderers_use_the_same_title_separator():
    entries = [_entry("01/02/2020", "A REPORT", "body text")]
    doc = build_mrr_document(
        entries, 10, "A B", "01/01/1980", "QME", details=ReportDetails(lawfirm="Firm")
    )
    word_cell = doc.tables[0].rows[0].cells[1].text
    linked_pdf = pytest.importorskip("app.services.linked_pdf")
    letter = linked_pdf._summary_html(entries, 10, "QME", ReportDetails(lawfirm="Firm"))
    assert word_cell.startswith(f"A REPORT{TITLE_SEPARATOR}")
    assert f"</a>{html.escape(TITLE_SEPARATOR)}" in letter


# One entry and one call, so the header tests below read as assertions about the header rather than
# as six copies of the same six-argument call.
_ENTRY = [
    {
        "summaryDate": "03/14/2026",
        "summaryTitle": "A REPORT",
        "summaryText": "**Diagnoses**: Lumbar strain.",
    }
]


def _build(entries):
    return build_mrr_document(
        entries,
        num_pages=42,
        patient_name="Doe, Jane",
        patient_dob="01/10/1961",
        qme_or_ame="QME",
        details=ReportDetails(lawfirm="Example Law Firm"),
    )


# ---------------------------------------------------------------------------------------------
# The running header. Three defects, all in the .docx - which is the PRIMARY deliverable - and all
# found by rendering both artifacts and diffing them, the same way the letter sentences were.


def _headers(doc):
    """(first-page header, later-page header) for the document's only section."""
    section = doc.sections[0]
    return section.first_page_header, section.header


def test_the_word_header_labels_the_date_of_birth():
    """WHEN the header names the patient, THE SYSTEM SHALL label the date of birth.

    The Word renderer wrote it as a bare value while the linked PDF wrote `DOB:`, so the two
    deliverables identified the patient differently on every page. `RE:` was labelled in both, which
    is what makes the unlabelled one an omission rather than a house style.
    """
    doc = _build(_ENTRY)
    first, later = _headers(doc)
    for header in (first, later):
        assert "DOB: 01/10/1961" in header.paragraphs[0].text


def test_the_word_header_carries_a_real_page_number_field():
    """IT SHALL number the pages, rather than writing the label and no number.

    The header ended with the literal string "Page " and nothing after it: python-docx writes text,
    not fields, so every page of the delivered .docx showed a dangling label while the linked PDF
    numbered its pages. A field is the only mechanism available - a Word header is ONE object
    repeated on every page, so there is no per-page text to write.
    """
    from docx.oxml.ns import qn

    doc = _build(_ENTRY)
    _first, later = _headers(doc)
    fields = later.paragraphs[0]._p.findall(qn("w:fldSimple"))
    assert [f.get(qn("w:instr")) for f in fields] == [" PAGE "]


def test_page_one_carries_the_patient_lines_and_no_number():
    """The first page SHALL show the identifying lines WITHOUT a page number, matching the linked
    PDF, which writes the number only from page 2 (`"" if i == 0`). A Word header applies to every
    page, so this needs a separate first-page header - which is also what stops "Page 1" appearing
    on a letter that opens with the patient's name."""
    from docx.oxml.ns import qn

    doc = _build(_ENTRY)
    assert doc.sections[0].different_first_page_header_footer is True
    first, _later = _headers(doc)
    assert "RE: Doe, Jane" in first.paragraphs[0].text
    assert "Page" not in first.paragraphs[0].text
    assert first.paragraphs[0]._p.findall(qn("w:fldSimple")) == []


def test_the_header_adds_no_blank_line_above_the_patient():
    """IT SHALL write into the header's existing paragraph rather than adding one.

    A new Word header already carries an empty paragraph, so `header.add_paragraph` left a blank
    line above the patient's name on every page of the deliverable. Asserted on the paragraph COUNT
    because that is the defect; the text assertions above pass either way.
    """
    doc = _build(_ENTRY)
    first, later = _headers(doc)
    assert len(first.paragraphs) == 1
    assert len(later.paragraphs) == 1
    assert first.paragraphs[0].text.startswith("RE:")
    assert later.paragraphs[0].text.startswith("RE:")


def test_both_renderers_take_the_header_lines_from_one_place():
    """WHEN either renderer names the patient, THE SYSTEM SHALL use the same two lines.

    The pin that matters: this module's docstring records that the letter's sentences drifted
    because each renderer held its own copy, and the header had drifted the same way for the same
    reason. Asserting each side separately would let them diverge again, so this asserts they AGREE.
    """
    from app.services.reporting import header_lines

    re_line, dob_line = header_lines("Doe, Jane", "01/10/1961")
    doc = _build(_ENTRY)
    _first, later = _headers(doc)
    assert re_line in later.paragraphs[0].text
    assert dob_line in later.paragraphs[0].text
    # and the linked PDF builds its running header from the same call
    import inspect

    from app.services import linked_pdf

    assert "header_lines(" in inspect.getsource(linked_pdf._draw_running_header)


def test_the_header_stays_times_new_roman_at_ten_point():
    """Unchanged by the rewrite: the header is 10pt Times New Roman, smaller than the 11pt body, and
    the linked PDF draws its own at fontsize=10. Pinned because the rewrite moved the run creation
    into a helper whose default size is the BODY's 11pt."""
    from docx.shared import Pt

    doc = _build(_ENTRY)
    _first, later = _headers(doc)
    run = later.paragraphs[0].runs[0]
    assert run.font.size == Pt(10)
    assert run.font.name == "Times New Roman"


def test_fill_header_clears_whatever_the_paragraph_already_held():
    """WHEN the header's first paragraph already carries runs, THE SYSTEM SHALL remove all of them.

    `_fill_header` reuses the existing paragraph rather than adding one, so anything already in it
    has to go or it would sit above the patient line on every page. Nothing else reaches this loop:
    a fresh header's first paragraph has no runs, which is why the removal was uncovered until now.

    It also guards the `list()` that used to wrap the loop. Deleting the elements being iterated is
    only safe because python-docx rebuilds `paragraph.runs` from the XML on each access; if that
    ever stops being true, this test fails rather than the deliverable silently keeping stale runs.
    """
    import docx

    from app.services.reporting import _fill_header

    document = docx.Document()
    header = document.sections[0].header
    header.paragraphs[0].add_run("stale name")
    header.paragraphs[0].add_run(" and a stale dob")
    assert len(header.paragraphs[0].runs) == 2

    _fill_header(header, "RE: Synthetic Patient", "DOB: 01/01/1990", numbered=False)

    text = header.paragraphs[0].text
    assert "stale" not in text
    assert "Synthetic Patient" in text
    assert "01/01/1990" in text


def test_emphasis_does_not_pair_two_bullets_across_a_line():
    """A `* item` list is not one italic block.

    Under re.DOTALL a star-delimited span paired the bullet on one line with the bullet
    on the next and italicised everything between. Measured over 3,017 stored summaries: 3 rendered
    differently in the .docx than on the review screen, and 6 of the 9 offending spans opened with
    "* " - a bullet, not emphasis - italicising 83 to 387 characters of a delivered document.
    """
    from app.services.reporting import INLINE_EMPHASIS_RE

    bullets = "* first item\n* second item\n* third item"
    assert INLINE_EMPHASIS_RE.findall(bullets) == []


def test_emphasis_does_not_cross_a_line_break():
    """The general rule the bullet case is one instance of. A marker pair must close on its own
    line, so an UNCLOSED marker cannot swallow the paragraphs after it."""
    from app.services.reporting import INLINE_EMPHASIS_RE

    assert INLINE_EMPHASIS_RE.findall("**Diagnoses:\nLumbar strain**") == []
    # ...while emphasis WITHIN a line still works, on every marker the summarizer emits.
    assert INLINE_EMPHASIS_RE.findall("**bold** and *it* and _it_") == [
        ("bold", "", ""),
        ("", "it", ""),
        ("", "", "it"),
    ]


def test_both_document_renderers_read_one_definition():
    """The Word and linked-PDF renderers share the emphasis DECISION, not merely a pattern.

    They are the same file pair that diverged on the heading and separator (#158) and on
    the letter's alignment (#268); a third private copy is how it would happen again.
    Identity, not equality - two definitions of the same rule would satisfy an equality
    check and still be two things to edit.

    This used to pin the shared regex. It now pins the whole parser, which is strictly
    more: the pattern only said where the markers are, while `entry_body_segments` decides
    which tier each one is in, and THAT is what the two renderers must not disagree on."""
    from app.services import linked_pdf, reporting

    assert linked_pdf.entry_body_segments is reporting.entry_body_segments


def _pdf_tagged(html_out: str, tag: str) -> list[str]:
    """The text inside every <tag> of `html_out`, with any nested tags stripped."""
    inner = re.findall(rf"<{tag}>(.*?)</{tag}>", html_out, re.DOTALL)
    return [re.sub(r"<[^>]+>", "", x) for x in inner]


def test_the_two_renderers_emphasise_the_same_spans():
    """Same input, same emphasis out of both - the invariant the shared parser exists for.

    Compares what each renderer EMITS rather than the parser they hold, so a change to
    either one's own loop is caught too. Now covers UNDERLINE as well, because that is the
    tier distinction their own format turns on."""
    import docx

    from app.services.linked_pdf import _inline_html
    from app.services.reporting import _run, entry_body_segments

    body = "* one\n* two\n**real bold** here\n_and italic_"

    paragraph = docx.Document().add_paragraph()
    for chunk, bold, italic, underline in entry_body_segments(body):
        _run(paragraph, chunk, bold=bold, italic=italic, underline=underline)
    html_out = _inline_html(body)

    for flag, tag in (("bold", "b"), ("italic", "i"), ("underline", "u")):
        word = [r.text for r in paragraph.runs if getattr(r, flag)]
        assert word == _pdf_tagged(html_out, tag), flag

    # `real bold` is not a KEY label, so it is underlined rather than bold.
    assert [r.text for r in paragraph.runs if r.underline] == ["real bold"]
    assert [r.text for r in paragraph.runs if r.italic] == ["and italic"]
    # The bullets survive as literal text in both, rather than becoming one italic block.
    assert "<i>one\n* two\n" not in html_out


def test_a_key_label_is_bold_and_underlined_and_carries_its_text():
    """DEMONSTRATES the reviewers' first tier: what was FOUND and what HAPPENS NEXT is emphasised
    whole, so the label is bold + underlined and the sentence after it is bold too."""
    from app.services.reporting import entry_body_segments

    segs = entry_body_segments("**Diagnoses**: Lumbar strain. **Treatment Plan**: PT weekly.")
    assert segs == [
        ("Diagnoses", True, False, True),
        (": Lumbar strain. ", True, False, False),
        ("Treatment Plan", True, False, True),
        (": PT weekly.", True, False, False),
    ]


def test_an_ordinary_label_is_underlined_only_and_its_text_stays_plain():
    """DEMONSTRATES the second tier, and that the bold does not leak past the next label - which is
    the whole reason the parser tracks a carry rather than a single flag."""
    from app.services.reporting import entry_body_segments

    segs = entry_body_segments(
        "**Diagnoses**: strain. **Physical Examination**: tender. **DOI**: 03/14/2026"
    )
    assert segs == [
        ("Diagnoses", True, False, True),
        (": strain. ", True, False, False),
        ("Physical Examination", False, False, True),
        (": tender. ", False, False, False),
        ("DOI", False, False, True),
        (": 03/14/2026", False, False, False),
    ]


def test_a_label_is_matched_however_it_is_spelled():
    """Ours are not spelled consistently - `Diagnosis` beside `Diagnoses`, a trailing colon
    optional, case varying - and all of those are the same tier."""
    from app.services.reporting import entry_body_segments

    for written in ("Diagnosis", "DIAGNOSES", "  Treatment Plan:  ", "work status"):
        segs = entry_body_segments(f"**{written}** x")
        assert segs[0][1] is True, written


def test_text_before_the_first_label_is_plain():
    """GUARD: a body that opens with prose rather than a label must not inherit anything."""
    from app.services.reporting import entry_body_segments

    segs = entry_body_segments("Opening prose. **Diagnoses**: strain.")
    assert segs[0] == ("Opening prose. ", False, False, False)


def test_the_entry_header_is_no_longer_bold_in_the_word_document():
    """DEMONSTRATES the third difference: the reviewers' entry header is PLAIN - date, author,
    facility and type read as a sentence rather than a heading. Ours bolded it."""
    doc = build_mrr_document(
        [
            {
                "summaryDate": "03/14/2026",
                "summaryTitle": "DR. SMITH. CLINIC. PROGRESS REPORT",
                "summaryText": "**Diagnoses**: strain.",
            }
        ],
        8,
        "Pat",
        "01/01/1980",
        "AME",
        details=ReportDetails(lawfirm="Acme LLP"),
    )
    cells = doc.tables[0].rows[0].cells
    title_runs = [r for r in cells[1].paragraphs[0].runs if "DR. SMITH" in r.text]
    assert title_runs, "the entry header should still be rendered"
    assert not any(r.bold for r in title_runs)


def test_the_delivered_entry_carries_both_tiers():
    """End to end through the real Word renderer, because the parser being right is not the same
    as the renderer asking it."""
    doc = build_mrr_document(
        [
            {
                "summaryDate": "03/14/2026",
                "summaryTitle": "A REPORT",
                "summaryText": "**DOI**: 03/14/2026. **Work Status**: modified duty.",
            }
        ],
        8,
        "Pat",
        "01/01/1980",
        "AME",
        details=ReportDetails(lawfirm="Acme LLP"),
    )
    runs = doc.tables[0].rows[0].cells[1].paragraphs[0].runs
    by_text = {r.text: (bool(r.bold), bool(r.underline)) for r in runs}
    assert by_text["DOI"] == (False, True)
    assert by_text["Work Status"] == (True, True)
    assert by_text[": modified duty."] == (True, False)


def test_each_doctor_gets_their_own_typeface():
    """The reviewers gave a font per evaluator and the Word document is written in theirs.

    Word only, and that is their decision rather than a limitation we settled for: python-docx
    writes the font NAME and the reader's Word resolves it, so nothing is installed here. They were
    asked about the linked PDF - which we render ourselves and which would need the licensed files -
    and answered that it does not need the font.
    """
    assert report_font("Falkinstein") == "Times New Roman"
    assert report_font("Pelton") == "Tahoma"
    assert report_font("Ahdoot") == "Bierstadt Display"
    assert len(DOCTOR_FONTS) == 11
    assert tuple(DOCTOR_FONTS) == DOCTORS


def test_an_unknown_doctor_falls_back_rather_than_failing():
    """GUARD on a delivered document. The field is free-form on the way in and a name could be
    retired while records still carry it. A report in the house font is a smaller failure than an
    export that refuses, so this never raises."""
    assert report_font(None) == "Times New Roman"
    assert report_font("") == "Times New Roman"
    assert report_font("   ") == "Times New Roman"
    assert report_font("Nobody In The List") == "Times New Roman"


def test_a_doctor_name_is_matched_after_trimming():
    """A value typed with a stray space still finds its font."""
    assert report_font("  Hekmat  ") == "Arial"


def test_the_letter_vocabulary_is_what_the_reviewers_named():
    """`interrogatory` is the SUPPLEMENTAL request letter and `advocacy` the initial one - their
    distinction, 2026-09-14. `none` is a real answer: many records arrive with no letter and the
    opening paragraph then omits the clause rather than leaving a gap."""
    assert LETTER_TYPES == ("advocacy", "interrogatory", "none")
    # The article is part of the label: `a defense advocacy letter` but `an interrogatory
    # letter`. Deriving it would be a rule that happens to work on two values.
    assert LETTER_LABELS["advocacy"] == "a defense advocacy letter"
    assert LETTER_LABELS["interrogatory"] == "an interrogatory letter"
    assert "none" not in LETTER_LABELS


def test_the_opening_paragraph_follows_the_format_the_reviewers_supplied():
    """DEMONSTRATES the whole of request 1, assembled from the fields the header now carries.

    The expected text is their format with the placeholders filled, so this fails if any clause is
    dropped, reordered or reworded.
    """
    text = intro_sentence(
        241,
        "Blitstein, Young & Blinder",
        attorney_name="Mitchell Garrett",
        letter_type="advocacy",
        letter_date="08/12/2026",
        reviewer_name="Jane Roe",
    )
    assert text == (
        "I have received a defense advocacy letter dated 08/12/2026 along with 241 pages of "
        "medical records from Mitchell Garrett, of Blitstein, Young & Blinder. I have reviewed "
        "all of the pages received and my opinion is based upon such records. The initial "
        "organization, outlining, and excerpting of medical records were performed by Jane Roe, "
        "Trained Medical Record Processor. I personally reviewed the excerpts, the entire "
        "outline, and the pages that were received, making additional inquiries and examinations "
        "as necessary to determine the relevant medical issues. "
        "(California Labor Code \u00a7 4628(b)(c))"
    )


def test_an_interrogatory_letter_takes_the_other_article():
    """`a defense advocacy letter` but `an interrogatory letter`. The article travels with the label
    rather than being computed, so a third type added later cannot inherit a wrong rule."""
    text = intro_sentence(50, "Acme LLP", letter_type="interrogatory")
    assert "I have received an interrogatory letter along with 50 pages" in text


def test_a_letter_with_no_date_still_says_which_letter():
    """The TYPE is the fact worth stating, and "dated" with nothing after it reads worse
    than no date at all."""
    text = intro_sentence(50, "Acme LLP", letter_type="advocacy")
    assert "a defense advocacy letter along with 50 pages" in text
    assert "dated" not in text


def test_no_letter_is_a_real_answer_and_prints_nothing():
    """ "none" is how a reviewer says they checked and there was no letter - different from not
    having been asked, and the paragraph reads as it always did."""
    text = intro_sentence(50, "Acme LLP", letter_type="none")
    assert text.startswith("I have received 50 pages of medical records from Acme LLP.")
    assert "letter" not in text


def test_the_attorney_needs_the_firm_to_read_as_intended():
    """ "from <person>, of <firm>" only works with both. A person alone is named alone rather than
    shipped with a dangling "of"."""
    assert "from Mitchell Garrett. " in intro_sentence(8, "", attorney_name="Mitchell Garrett")
    assert "from Acme LLP. " in intro_sentence(8, "Acme LLP")
    # The dangling form specifically. A bare `of` check is useless here: the sentence already
    # says "pages of medical records".
    assert ", of" not in intro_sentence(8, "", attorney_name="Mitchell Garrett")
    assert ", of" in intro_sentence(8, "Acme LLP", attorney_name="Mitchell Garrett")


def test_the_labor_code_sentences_need_a_named_reviewer():
    """GUARDS a legal assertion. Those sentences state who performed the record work; emitting them
    with a blank name is not a guess to make, so no name means no claim."""
    without = intro_sentence(8, "Acme LLP", letter_type="advocacy")
    assert "4628" not in without
    assert "Trained Medical Record Processor" not in without

    with_name = intro_sentence(8, "Acme LLP", reviewer_name="Jane Roe")
    assert "4628" in with_name
    assert "performed by Jane Roe, Trained Medical Record Processor." in with_name


def test_the_paragraph_is_unchanged_when_no_new_field_is_supplied():
    """GUARD. Every added clause is conditional, so a record that predates the header fields renders
    the sentence it always did - apart from the requested wording change above."""
    assert intro_sentence(241, "Acme LLP") == (
        "I have received 241 pages of medical records from Acme LLP. I have reviewed all of the "
        "pages received and my opinion is based upon such records."
    )


def _all_fonts(doc):
    """Every font name the document actually sets, body and table and page header alike."""
    names = {r.font.name for p in doc.paragraphs for r in p.runs if r.font.name}
    names |= {
        r.font.name
        for t in doc.tables
        for row in t.rows
        for cell in row.cells
        for p in cell.paragraphs
        for r in p.runs
        if r.font.name
    }
    names |= {
        r.font.name
        for s in doc.sections
        for hdr in (s.header, s.first_page_header)
        for p in hdr.paragraphs
        for r in p.runs
        if r.font.name
    }
    return names


def test_the_word_document_is_written_in_the_doctors_typeface():
    """DEMONSTRATES request 2. EVERY run, not just the letter: the page header, the date column and
    the summary text all have to move together or the document is two typefaces."""
    doc = build_mrr_document(
        _ENTRY,
        8,
        "Pat",
        "01/01/1980",
        "AME",
        details=ReportDetails(lawfirm="Acme LLP", doctor="Pelton"),
    )
    assert _all_fonts(doc) == {"Tahoma"}


def test_an_absent_or_unknown_doctor_keeps_the_house_typeface():
    """GUARD. Every record that predates the field, and any name not in the eleven."""
    for doctor in (None, "", "Nobody In The List"):
        doc = build_mrr_document(
            _ENTRY,
            8,
            "Pat",
            "01/01/1980",
            "AME",
            # Straight through rather than coerced: the export route does the `or ""`, and
            # `report_font` defends against None anyway, so all three values stay distinct.
            details=ReportDetails(lawfirm="Acme LLP", doctor=doctor),
        )
        assert _all_fonts(doc) == {"Times New Roman"}, doctor


def test_both_renderers_open_with_the_same_paragraph():
    """The letter clause, the sender and the Labor Code sentences are facts about the RECORD, so
    both artifacts state them. The linked PDF took none of them - it passed no details at all and
    `build_linked_pdf` had no parameter to take them - so the two deliverables opened differently
    on every record that named a covering letter. Found on review by @adrian-g.

    Only the FONT is Word-only, and that is the reviewers' own answer ("does not need the font on
    that one"); this pins the paragraph, which is the half that was never theirs to differ on.
    """
    details = ReportDetails(
        attorney_name="Mitchell Garrett",
        lawfirm="Acme LLP",
        letter_type="advocacy",
        letter_date="08/12/2026",
        reviewer_name="Jane Roe",
    )
    doc = build_mrr_document(
        [], num_pages=241, patient_name="P", patient_dob="", qme_or_ame="QME", details=details
    )
    linked_pdf = pytest.importorskip("app.services.linked_pdf")
    letter = linked_pdf._summary_html([], 241, "QME", details)

    expected = intro_sentence(
        241,
        "Acme LLP",
        attorney_name="Mitchell Garrett",
        letter_type="advocacy",
        letter_date="08/12/2026",
        reviewer_name="Jane Roe",
    )
    assert _intro(doc) == expected
    assert html.escape(expected) in letter
    for fragment in ("a defense advocacy letter dated 08/12/2026", "Mitchell Garrett", "4628"):
        assert fragment in _intro(doc)
        assert html.escape(fragment) in letter


@dataclass
class _Row:
    """The two fields `as_row()` does not carry are exactly the two this needs, so the accounting
    reads ORM rows. This stands in for one.

    A dataclass rather than a hand-written __init__ because the duplicate states need a third
    field and seven positional parameters is where both ruff and Sonar start objecting. Field
    order is unchanged, so the positional calls below still read the same."""

    start: int
    end: int
    include: bool
    title: str
    dupe_group: int | None = None
    dupe_primary: bool = False
    dupe_dismissed: bool = False


def test_the_three_buckets_partition_every_page_received():
    """The arithmetic has to CLOSE, because theirs does: on the one reference record whose
    sentence reconciles exactly, remarked + other + duplicates equals the total received."""
    rows = [
        _Row(1, 10, True, "MRI of the Lumbar Spine"),
        _Row(11, 14, False, "Cover Letter"),
        _Row(15, 20, False, "Duplicate copy", dupe_group=1),
        _Row(21, 26, True, "Original report", dupe_group=1, dupe_primary=True),
    ]
    acc = record_accounting(rows, 30)
    assert acc.pages_remarked == 16
    assert acc.duplicate_pages == 6
    assert acc.pages_other == 8
    assert acc.pages_remarked + acc.duplicate_pages + acc.pages_other == acc.pages_received


def test_a_duplicate_copy_is_not_also_counted_as_another_document():
    """DEMONSTRATES the double count the disjoint reading forbids. `resolve_duplicate` leaves a
    non-primary member `include=False`, so counting it by its include flag would put the same
    pages in both buckets and the delivered sentence would not add up.

    The group carries a PRIMARY, which is what makes it a resolved one. An earlier version of
    this fixture had no primary and still asserted the pages were counted - encoding the very
    bug the three tests below now cover.
    """
    rows = [
        _Row(1, 5, False, "Duplicate copy", dupe_group=1),
        _Row(6, 10, True, "Original report", dupe_group=1, dupe_primary=True),
    ]
    acc = record_accounting(rows, 10)
    assert acc.duplicate_pages == 5
    assert acc.excluded_types == ()
    assert acc.pages_other == 0


def test_an_unresolved_duplicate_group_is_not_announced_to_the_client():
    """DEMONSTRATES. Detection alone is a SUGGESTION - `resolve_duplicate` is what decides,
    and until it runs no member is primary. Reading `not dupe_primary` alone counted every
    page of every such group: measured on the box, 102 of 147 groups have no primary at all,
    which turned 171 surplus pages into 1,140.

    The copies are still being remarked upon while nobody has resolved them, so they belong in
    the remarked bucket and the duplicates sentence stays off."""
    rows = [
        _Row(1, 5, True, "Report", dupe_group=1),
        _Row(6, 10, True, "Report", dupe_group=1),
    ]
    acc = record_accounting(rows, 10)
    assert acc.duplicate_pages == 0
    assert acc.pages_remarked == 10


def test_a_dismissed_group_is_never_called_a_duplicate():
    """DEMONSTRATES, and it is the worst of the three states: `dismiss` is the reviewer saying
    these are NOT duplicates. Counting them anyway inverts that decision inside a document the
    client reads.

    The mechanism is the primary check rather than a `dupe_dismissed` test - dismiss clears
    every primary - but the behaviour is what matters and it is what this pins."""
    rows = [
        _Row(1, 5, True, "Report", dupe_group=1, dupe_dismissed=True),
        _Row(6, 10, True, "Report", dupe_group=1, dupe_dismissed=True),
    ]
    acc = record_accounting(rows, 10)
    assert acc.duplicate_pages == 0
    assert acc.pages_remarked == 10


def test_only_the_surplus_copies_of_a_resolved_group_are_counted():
    """GUARD. With one primary kept, the count is the pages BEYOND the first copy - which is
    what the class docstring promises and what the reference sentence's arithmetic needs.
    Three copies of a five-page report are ten surplus pages, not fifteen."""
    rows = [
        _Row(1, 5, True, "Report", dupe_group=1, dupe_primary=True),
        _Row(6, 10, False, "Report", dupe_group=1),
        _Row(11, 15, False, "Report", dupe_group=1),
    ]
    acc = record_accounting(rows, 15)
    assert acc.duplicate_pages == 10
    assert acc.pages_remarked == 5


def test_the_excluded_types_are_deduplicated_but_keep_their_first_spelling():
    rows = [
        _Row(1, 1, False, "Cover Letter"),
        _Row(2, 2, False, "cover letter"),
        _Row(3, 3, False, "Proof of Service"),
    ]
    acc = record_accounting(rows, 3)
    assert acc.excluded_types == ("Cover Letter", "Proof of Service")


def test_the_remainder_never_goes_negative():
    """GUARDS a delivered sentence. `pages_received` is the PDF's own count while the other two
    come from rows, so a row set covering more than the document would otherwise ship
    "the remaining -4 pages" to a client."""
    acc = record_accounting([_Row(1, 10, True, "A")], 6)
    assert acc.pages_other == 0


def test_the_duplicates_sentence_is_absent_when_there_are_none():
    """OBSERVED, not assumed: the reference MRR carries a duplicates sentence and the two
    supplemental reports for another patient carry none, so zero means absent rather than "0"."""
    exclusion, duplicates = accounting_sentences(record_accounting([_Row(1, 5, True, "A")], 5))
    assert exclusion.startswith("Of the 5 pages received")
    assert duplicates == ""


def test_a_record_with_no_accounting_renders_exactly_as_before():
    """GUARDS the bundle export, which builds a letter from rows chosen by CATEGORY - a sentence
    about the pages RECEIVED would answer a question nobody asked of it, so it passes nothing."""
    doc = build_mrr_document(
        [], 10, "P", "01/01/1990", "PQME", details=ReportDetails(lawfirm="Firm")
    )
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "pages were remarked upon" not in text
    assert CONCLUSION in text


def test_both_renderers_close_the_letter_with_the_same_sentences():
    """The test that matters. Each renderer was individually self-consistent when the Word and
    PDF letters disagreed in #158 and #268; only comparing them catches the drift."""
    linked_pdf = pytest.importorskip("app.services.linked_pdf")
    acc = record_accounting(
        [
            _Row(1, 10, True, "MRI"),
            _Row(11, 14, False, "Cover Letter"),
            _Row(15, 18, False, "Dup", dupe_group=1),
        ],
        20,
    )
    doc = build_mrr_document(
        [], 20, "P", "01/01/1990", "PQME", details=ReportDetails(lawfirm="Firm", accounting=acc)
    )
    word = "\n".join(p.text for p in doc.paragraphs)
    markup = linked_pdf._summary_html([], 20, "PQME", ReportDetails(lawfirm="Firm", accounting=acc))
    plain = html.unescape(re.sub(r"<[^>]+>", "\n", markup))

    exclusion, duplicates = accounting_sentences(acc)
    for sentence in (exclusion, duplicates):
        assert sentence in word
        assert sentence in plain
    # and the excluded type appears beneath it in both
    assert "Cover Letter" in word
    assert "Cover Letter" in plain


def test_the_summary_line_names_the_firm_and_drops_it_when_absent():
    """The reference document names the sending firm. The clause is DROPPED rather than rendered
    empty for the same reason #115 fixed: "records from ." shipped to a client."""
    assert summary_intro("Smith & Co") == "The following is a summary of records from Smith & Co:"
    assert summary_intro("   ") == SUMMARY_INTRO
    assert summary_intro(None) == SUMMARY_INTRO


def test_the_two_readings_of_a_resolved_duplicate_agree():
    """GUARD across two modules, which is where this kind of predicate actually goes wrong.

    `bundles.is_resolved_duplicate` and `record_accounting` both have to decide 'is this row a
    surplus copy'. They cannot share code - one reads `as_row()` dicts, the other reads ORM rows
    for the two fields `ROW_FIELDS` omits - so this asserts they answer the same for every state
    a row can be in. #306 shipped with the two disagreeing on the unresolved and dismissed
    states, which is the pair this exists to catch.
    """
    from app.services.bundles import is_resolved_duplicate, resolved_clusters

    states = [
        # (dupe_group, dupe_primary, dupe_dismissed, what it represents)
        (None, False, False, "not in any group"),
        (1, False, False, "unresolved - nobody has acted"),
        (1, True, False, "the kept copy of a resolved group"),
        (1, False, True, "dismissed - the reviewer said NOT duplicates"),
    ]
    for group, primary, dismissed, label in states:
        # the group is resolved only when SOME member is primary, so a lone non-primary row
        # stands for an unresolved group and a primary sibling is added where one is resolved
        rows = [
            _Row(
                1,
                5,
                True,
                "Report",
                dupe_group=group,
                dupe_primary=primary,
                dupe_dismissed=dismissed,
            )
        ]
        dicts = [
            {
                "dupe_group": r.dupe_group,
                "dupe_primary": r.dupe_primary,
                "dupe_dismissed": r.dupe_dismissed,
            }
            for r in rows
        ]
        theirs = is_resolved_duplicate(dicts[0], resolved_clusters(dicts))
        ours = record_accounting(rows, 5).duplicate_pages > 0
        assert ours == theirs, f"{label}: reporting says surplus={ours}, bundles says {theirs}"


def test_the_list_is_introduced_only_when_there_is_a_list():
    """Every excluded row can carry a blank title, which leaves `excluded_types` empty and ended
    the delivered sentence on a dangling colon - #115's "medical records from ." in a new place.
    The counts are still true, so they are still stated; only the introduction goes."""
    acc = record_accounting(
        [_Row(1, 130, True, "x"), _Row(131, 191, False, "   ")],
        191,
    )
    assert acc.excluded_types == ()
    exclusion, _ = accounting_sentences(acc)
    assert exclusion.endswith("61 pages are other documents.")
    assert "such as" not in exclusion


def test_with_nothing_left_over_the_remainder_clause_goes_entirely():
    """Reachable whenever the only excluded rows are duplicate copies. "0 pages are other
    documents" is true and reads like a defect."""
    exclusion, _ = accounting_sentences(record_accounting([_Row(1, 191, True, "x")], 191))
    assert exclusion == "Of the 191 pages received, exactly 191 pages were remarked upon."


def test_rows_that_overrun_the_record_ship_no_sentence_at_all():
    """DEMONSTRATES the defect `test_the_remainder_never_goes_negative` could not see: that test
    asserts `pages_other == 0` and never renders, so the clamp passed while the sentence said
    "exactly 200 pages were remarked upon" of 191 received, then "0 pages are other documents
    such as:" above a list of two. The duplicates sentence goes too - it opens "In addition"."""
    acc = record_accounting(
        [
            _Row(1, 200, True, "x"),
            _Row(201, 250, False, "cover letter"),
            _Row(251, 260, False, "proof of service"),
        ],
        191,
    )
    assert acc.pages_remarked > acc.pages_received
    assert accounting_sentences(acc) == ("", "")


def _memo_accounting(**kw):
    """A RecordAccounting shaped like the record their reference memo describes."""
    fields = {
        "pages_received": 191,
        "pages_remarked": 88,
        "excluded_types": ("cover letter", "declaration", "proof of service"),
        "duplicate_pages": 24,
    }
    fields.update(kw)
    return RecordAccounting(**fields)


def _memo_text(doc):
    return [p.text for p in doc.paragraphs if p.text.strip()]


def test_the_memo_leads_with_the_page_count_the_reviewers_called_the_main_thing():
    """DEMONSTRATES the sentence the memo exists for. Asked what this document needed they
    answered that the header "is not too important" and "the main thing would be the actual
    page count vs declared page count" - so both counts are named, the difference is spelled
    out, and it comes BEFORE the sentences the letter already carries."""
    text = _memo_text(
        build_memo_document(
            _memo_accounting(pages_received=309),
            MemoDetails(pages_stated=309, pages_on_file=311),
        )
    )
    note = (
        "The cover sheet states 309 pages and the file received contains 311 pages - "
        "2 more than stated. The page count above follows the cover sheet."
    )
    assert note in text
    assert text.index(note) < text.index(MEMO_SOURCES_LEAD)


def test_the_memo_states_both_counts_even_when_they_agree():
    """A memo that went silent when the counts matched would leave the reader unable to tell a
    check that passed from a check nobody ran - which is the defect #133 fixed on the
    duplicates tab and #290 on the summary card."""
    note = page_count_note(MemoDetails(pages_stated=241, pages_on_file=241))
    assert note == (
        "The cover sheet states 241 pages and the file received contains 241 pages. The two agree."
    )


def test_a_file_shorter_than_the_cover_sheet_is_reported_the_other_way_round():
    """The measured cases all run the same way - the file is longer, because pages are attached
    to it downstream - but a short file is the direction that means pages are MISSING, so the
    sentence must not hard-code "more"."""
    assert "3 fewer than stated" in page_count_note(
        MemoDetails(pages_stated=244, pages_on_file=241)
    )


def test_no_cover_sheet_figure_means_no_page_count_sentence():
    """GUARD, and the reason it is a guard rather than a nicety: with `pages_stated` defaulted
    to the file's own count the memo would print "the two agree" on every record, which is the
    memo agreeing with itself. Nobody has said, so it says nothing.

    Reading the figure out of the declaration instead was measured on the box and does not
    work - 21 of 245 declaration rows state a page count at all - so it is typed or absent."""
    assert page_count_note(MemoDetails(pages_on_file=311)) == ""
    assert not any(
        "cover sheet" in t
        for t in _memo_text(build_memo_document(_memo_accounting(), MemoDetails(pages_on_file=311)))
    )


def test_the_memo_follows_the_shape_the_reviewers_sent():
    """DEMONSTRATES the memo end to end against the structure of their own covering memo:
    an addressed block, a greeting, what arrived, the accounting, and a signature."""
    doc = build_memo_document(
        _memo_accounting(),
        MemoDetails(
            doctor="Falkinstein",
            patient_name="Synthetic Patient",
            attorney_name="Mitchell Garrett",
            lawfirm="Acme LLP",
            reviewer_name="Jane Roe",
            memo_date="August 25, 2026",
        ),
    )
    text = _memo_text(doc)
    assert text[0] == "TO:\tDR. FALKINSTEIN\u2019S OFFICE"
    assert text[1] == "FROM:\tJane Roe"
    assert text[2] == "RE:\tReview of Synthetic Patient"
    assert text[3] == "DATE:\tAugust 25, 2026"
    assert MEMO_GREETING in text
    assert (
        "We have received 191 pages of medical records from Mitchell Garrett, of Acme LLP." in text
    )
    assert text[-3:] == [MEMO_VERIFIED_BY, "Jane Roe", "August 25, 2026"]


def test_the_memo_and_the_letter_report_the_same_page_accounting():
    """The invariant worth having: a memo that disagrees with its own letter about how many
    pages arrived is the first thing a client would notice. Both read the SAME
    `RecordAccounting` rather than recomputing, so this compares the sentences they emit."""
    accounting = _memo_accounting()
    exclusion, duplicates = accounting_sentences(accounting)

    memo = _memo_text(build_memo_document(accounting, MemoDetails()))
    letter = build_mrr_document(
        [],
        191,
        "Pat",
        "01/01/1980",
        "AME",
        details=ReportDetails(lawfirm="Acme LLP", accounting=accounting),
    )
    letter_text = [p.text for p in letter.paragraphs if p.text.strip()]

    assert exclusion in memo
    assert exclusion in letter_text
    assert duplicates in memo
    assert duplicates in letter_text


def test_the_memo_and_the_letter_name_the_sender_the_same_way():
    """Both openings run through `_sender_clause`, so "from <person>, of <firm>" cannot come
    out one way on the report and another on the note stapled to it. This is the drift that
    cost #158 (two renderers) and #162 (three export paths) - one function, checked here
    across the two sentences that use it."""
    details = MemoDetails(attorney_name="Mitchell Garrett", lawfirm="Acme LLP")
    sender = "from Mitchell Garrett, of Acme LLP"
    assert sender in memo_opening(_memo_accounting(), details)
    assert sender in intro_sentence(191, "Acme LLP", attorney_name="Mitchell Garrett")


def test_the_excluded_types_are_listed_plain_under_their_bold_sentence():
    """Their list is plain even though the sentence introducing it is bold; that contrast is
    the whole of the house style here."""
    doc = build_memo_document(_memo_accounting(), MemoDetails())
    bold = {p.text for p in doc.paragraphs if p.runs and all(r.bold for r in p.runs if r.text)}
    assert any(t.startswith("Of the 191 pages received") for t in bold)
    for kind in ("cover letter", "declaration", "proof of service"):
        assert kind in _memo_text(doc)
        assert kind not in bold


def test_a_memo_with_no_duplicates_omits_that_sentence():
    """CONDITIONAL, like the letter's: a count of zero means the sentence is absent rather than
    reading "0 pages" - observed in their two supplemental reports, which carry none."""
    doc = build_memo_document(_memo_accounting(duplicate_pages=0), MemoDetails())
    assert not any("duplicate copies" in t for t in _memo_text(doc))


def test_a_header_line_with_nothing_to_say_is_dropped():
    """GUARD. A memo on a record with no doctor recorded must not address the reader as
    `DR. \u2019S OFFICE`, which is what filling the template unconditionally would print."""
    text = _memo_text(
        build_memo_document(_memo_accounting(), MemoDetails(reviewer_name="Jane Roe"))
    )
    assert not any(t.startswith("TO:") for t in text)
    assert not any(t.startswith("RE:") for t in text)
    assert not any(t.startswith("DATE:") for t in text)
    assert text[0] == "FROM:\tJane Roe"


def test_the_memo_renders_with_no_details_at_all():
    """GUARD: the whole header is optional, so a memo is still a document when nothing but the
    page accounting is known."""
    text = _memo_text(build_memo_document(_memo_accounting(), None))
    assert MEMO_GREETING in text
    assert text[-1] == MEMO_VERIFIED_BY
    assert any(t.startswith("We have received 191 pages") for t in text)


def test_the_memo_opening_drops_the_sender_it_cannot_name():
    """The same convention `intro_sentence` follows: an absent field removes its clause rather
    than leaving a dangling preposition. #115 shipped "medical records from ." to a client."""
    accounting = _memo_accounting()
    assert (
        memo_opening(accounting, MemoDetails()) == "We have received 191 pages of medical records."
    )
    assert memo_opening(accounting, MemoDetails(lawfirm="Acme LLP")) == (
        "We have received 191 pages of medical records from Acme LLP."
    )


def test_the_memo_and_the_letter_open_with_the_same_two_clauses():
    """Their memo and their report state the covering letter and the sender identically - only
    the pronoun differs. Both run through `_letter_clause` and `_sender_clause`, so this pins
    that the memo cannot name either one differently from the report stapled to it."""
    details = MemoDetails(
        attorney_name="Mitchell Garrett",
        lawfirm="Acme LLP",
        letter_type="advocacy",
        letter_date="07/31/26",
    )
    memo = memo_opening(_memo_accounting(), details)
    letter = intro_sentence(
        191,
        "Acme LLP",
        attorney_name="Mitchell Garrett",
        letter_type="advocacy",
        letter_date="07/31/26",
    )
    clauses = (
        "a defense advocacy letter dated 07/31/26 along with 191 pages of medical "
        "records from Mitchell Garrett, of Acme LLP"
    )
    assert memo == f"We have received {clauses}."
    assert letter.startswith(f"I have received {clauses}.")
