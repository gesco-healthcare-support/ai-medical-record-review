"""P3a: MRR Word-document assembly (python-docx; no DB, no network)."""

import html
import io

import pytest
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

from app.services.reporting import (
    CONCLUSION,
    DOCX_MIMETYPE,
    SUMMARY_INTRO,
    build_mrr_document,
    intro_sentence,
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
        lawfirm="Example Law Firm",
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    assert buffer.tell() > 0
    assert DOCX_MIMETYPE.endswith("wordprocessingml.document")


def _intro(doc) -> str:
    """The 'I have received N pages...' sentence, wherever it sits in the document."""
    return next(p.text for p in doc.paragraphs if p.text.startswith("I have received"))


def test_intro_names_the_law_firm_when_there_is_one():
    doc = build_mrr_document(
        [], num_pages=8, patient_name="", patient_dob="", qme_or_ame="", lawfirm="Example Law Firm"
    )
    assert _intro(doc) == (
        "I have received 8 pages of medical records from Example Law Firm. I have reviewed all "
        "of the pages received and my opinion is based upon such received records."
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
            [], num_pages=8, patient_name="", patient_dob="", qme_or_ame="", lawfirm=blank
        )
        text = _intro(doc)
        assert text == (
            "I have received 8 pages of medical records. I have reviewed all of the pages "
            "received and my opinion is based upon such received records."
        )
        assert "from ." not in text
        assert "records from" not in text


def test_intro_has_no_double_space():
    """ "all of the pages  received" carried a double space in the shipped template."""
    doc = build_mrr_document(
        [], num_pages=1, patient_name="", patient_dob="", qme_or_ame="", lawfirm="Firm"
    )
    assert "  " not in _intro(doc)


def test_build_mrr_document_blank_qme_ame_does_not_crash():
    # A blank QME/AME field must not crash (an empty paragraph has no runs -> guarded with " ").
    doc = build_mrr_document(
        [], num_pages=1, patient_name="", patient_dob="", qme_or_ame="", lawfirm=""
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
        lawfirm="Example Law Firm",
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
        lawfirm="Example Law Firm",
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
    return linked_pdf._summary_html([], num_pages, "Synthetic Patient", "-", "QME", lawfirm)


@pytest.mark.parametrize("lawfirm", ["Example Law Firm", "Smith & Jones, LLP", "", "   ", None])
def test_both_renderers_emit_the_same_intro_sentence(lawfirm):
    """The one sentence, identical in both artifacts, for every shape the firm field arrives in.

    The PDF side is compared against the HTML-ESCAPED sentence because that renderer escapes the
    assembled string on its way into the letter markup - which is why "Smith & Jones, LLP" is in the
    parameter list. Same sentence, escaped for its medium.
    """
    expected = intro_sentence(8, lawfirm)
    doc = build_mrr_document(
        [], num_pages=8, patient_name="", patient_dob="", qme_or_ame="", lawfirm=lawfirm
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
        [], num_pages=8, patient_name="", patient_dob="", qme_or_ame="", lawfirm=lawfirm
    )
    letter = _pdf_letter(8, lawfirm)
    for text in (_intro(doc), letter):
        assert "from ." not in text
        assert "records from" not in text


def test_both_renderers_share_the_other_two_sentences():
    """The intro was the one with the bug; these two are the remaining copies of the mechanism."""
    doc = build_mrr_document(
        [], num_pages=8, patient_name="", patient_dob="", qme_or_ame="", lawfirm="Firm"
    )
    paragraphs = [p.text for p in doc.paragraphs]
    letter = _pdf_letter(8, "Firm")
    for sentence in (SUMMARY_INTRO, CONCLUSION):
        assert sentence in paragraphs
        assert html.escape(sentence) in letter
