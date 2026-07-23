"""MRR Word-document assembly (ported from the Flask export blueprint; python-docx, Flask-free).

`build_mrr_document` is shared by the export + bundle-summarize routes: it sorts summary entries
chronologically and assembles the letterhead + intro + per-record body into a python-docx
Document. The classic CSV/on-disk export routes are dropped.
"""

import re
from datetime import datetime

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.shared import Inches, Pt

DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Inline emphasis the summarizer emits: **bold**, *italic*, _italic_. Rendered as real runs so no
# raw markers leak into the Word document (mirrors the web MarkdownText renderer).
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_", re.DOTALL)


def _run(paragraph, s, *, bold=False, italic=False):
    run = paragraph.add_run(s)
    run.bold = bold
    run.italic = italic
    run.font.name = "Times New Roman"
    run.font.size = Pt(11)
    return run


def _add_inline_runs(paragraph, text, *, bold=False, italic=False):
    """Append runs to ``paragraph``, turning **bold** / *italic* / _italic_ markers into real
    formatting; ``bold``/``italic`` set the baseline for the plain segments."""
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos : m.start()], bold=bold, italic=italic)
        if m.group(1) is not None:
            _run(paragraph, m.group(1), bold=True, italic=italic)
        else:
            _run(paragraph, m.group(2) or m.group(3), bold=bold, italic=True)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold=bold, italic=italic)


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
        _run(cells[0].paragraphs[0], entry["summaryDate"])
        body = cells[1].paragraphs[0]
        _add_inline_runs(body, entry["summaryTitle"], bold=True)
        _run(body, ": ")
        _add_inline_runs(body, entry["summaryText"])

    nine_title = doc.add_paragraph(this_concludes_text)  # noqa: F841
    nine_title_format = fourth_title.runs[0]
    nine_title_format.bold = False
    nine_title_format.underline = False
    nine_title_format.font.size = Pt(12)
    nine_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    nine_title_format.font.name = "Times New Roman"

    return doc
