"""MRR Word-document assembly (ported from the Flask export blueprint; python-docx, Flask-free).

`build_mrr_document` is shared by the export + bundle-summarize routes: it sorts summary entries
chronologically and assembles the letterhead + intro + per-record body into a python-docx
Document. The classic CSV/on-disk export routes are dropped.
"""

from datetime import datetime

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.shared import Pt

DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def build_mrr_document(entries, num_pages, patient_name, patient_dob, qme_or_ame, lawfirm):
    """Assemble the MRR Word document from summary ``entries`` (sorted chronologically)."""

    def safe_date_parse(entry):
        try:
            return datetime.strptime(entry.get("summaryDate", ""), "%m/%d/%Y")
        except ValueError:
            return datetime.min  # undated entries sort first

    entries = sorted(entries, key=safe_date_parse)

    doc = Document()

    # HEADER
    section = doc.sections[0]
    header = section.header
    header_paragraph = header.add_paragraph(
        "RE: " + patient_name + "\n" + patient_dob + "\n" + "Page "
    )
    for run in header_paragraph.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(10)

    doc.add_paragraph("")

    # TITLE. An empty paragraph has no runs, so runs[0] below would crash on a blank
    # QME/AME field (the form allows leaving it empty).
    title = doc.add_paragraph(qme_or_ame or " ")
    title_format = title.runs[0]
    title_format.bold = True
    title_format.underline = True
    title_format.font.size = Pt(12)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    title_format.font.name = "Times New Roman"

    doc.add_paragraph("")

    second_title = doc.add_paragraph("Medical Record Review")
    second_title_format = second_title.runs[0]
    second_title_format.bold = True
    second_title_format.underline = True
    second_title_format.font.size = Pt(12)
    second_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    second_title_format.font.name = "Times New Roman"

    intro_text = (
        "I have received "
        + str(num_pages)
        + " pages of medical records from "
        + lawfirm
        + ". I have reviewed all of the pages  received and my opinion is based upon such received records."
    )
    second_intro_text = "The following is a summary of those records:"
    this_concludes_text = "This concludes the review of submitted records."

    third_title = doc.add_paragraph(intro_text)
    third_title_format = third_title.runs[0]
    third_title_format.bold = False
    third_title_format.underline = False
    third_title_format.font.size = Pt(12)
    third_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    third_title_format.font.name = "Times New Roman"

    fourth_title = doc.add_paragraph(second_intro_text)
    fourth_title_format = fourth_title.runs[0]
    fourth_title_format.bold = True
    fourth_title_format.underline = False
    fourth_title_format.font.size = Pt(12)
    fourth_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    fourth_title_format.font.name = "Times New Roman"

    big_text = ""
    main_paragraph = None
    for entry in entries:
        big_text += (
            f"_{entry['summaryDate']}_\t****{entry['summaryTitle']}****: {entry['summaryText']}"
        )
        main_paragraph = doc.add_paragraph(big_text)
        big_text = ""

    if main_paragraph is not None:
        for run in main_paragraph.runs:  # ensure the body uses Times New Roman
            run.font.name = "Times New Roman"
            run.font.size = Pt(11)

    nine_title = doc.add_paragraph(this_concludes_text)  # noqa: F841
    nine_title_format = fourth_title.runs[0]
    nine_title_format.bold = False
    nine_title_format.underline = False
    nine_title_format.font.size = Pt(12)
    nine_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    nine_title_format.font.name = "Times New Roman"

    return doc
