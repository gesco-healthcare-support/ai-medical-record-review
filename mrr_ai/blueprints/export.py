"""Export routes: results CSV and the assembled MRR Word documents."""

import os
from datetime import datetime

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.shared import Pt
from flask import Blueprint, jsonify, request, send_file

from mrr_ai import state

bp = Blueprint("export", __name__)


@bp.route("/exportresultstoCSV", methods=["POST"])
def exportresultstoCSV():
    try:
        # Get the JSON data from the request
        data = request.get_json()

        # Extract the text content and strip unwanted quotes
        csv_content = data.get("TXTText", "").strip('"')
        print(csv_content)

        # Prepare the file path
        file_path = os.path.join(os.path.expanduser("~"), "MRRs", state.main_filename + ".csv")
        print(state.main_filename)

        # Write the content to the CSV file without using csv.writer
        with open(file_path, "w", encoding="utf-8") as csv_file:
            csv_file.write(csv_content)

        # Return a success response
        return jsonify({"message": "Content saved to CSV file successfully!"}), 200
    except Exception as e:
        # Handle any errors
        return jsonify({"error": str(e)}), 500


@bp.route("/exportresultstoword", methods=["POST"])
def exportresultstoword():
    patientName = request.json.get("patientName")
    patientdob = request.json.get("patientdob")
    QMEorAME = request.json.get("QMEorAME")
    lawfirm = request.json.get("lawfirm")

    # Check if main_filename is None and set a default value if needed
    if state.main_filename is None:
        state.main_filename = "default_filename"

    def safe_date_parse(entry):
        try:
            return datetime.strptime(entry.get("summaryDate", ""), "%m/%d/%Y")
        except ValueError:
            return datetime.min  # Assign a very early date as fallback

    state.all_data = sorted(state.all_data, key=safe_date_parse)

    # Create a new Word document and add the content
    doc = Document()

    # HEADER
    section = doc.sections[0]  # Access the first section of the document
    header = section.header  # Access the header of the section
    header_paragraph = header.add_paragraph(
        "RE: " + patientName + "\n" + patientdob + "\n" + "Page "
    )
    for run in header_paragraph.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(10)  # Optional: Set a font size for the header

    # Add empty lines for spacing
    doc.add_paragraph("")

    # TITLE
    title = doc.add_paragraph(QMEorAME)
    title_format = title.runs[0]  # Access the first run in the paragraph
    title_format.bold = True  # Make the text bold
    title_format.underline = True  # Underline the text
    title_format.font.size = Pt(12)  # Optional: Set the font size
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER  # Center the title
    title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    # Add empty lines for spacing
    doc.add_paragraph("")

    # Medical Record Review
    second_title = doc.add_paragraph("Medical Record Review")
    second_title_format = second_title.runs[0]  # Access the first run in the paragraph
    second_title_format.bold = True  # Make the text bold
    second_title_format.underline = True  # Underline the text
    second_title_format.font.size = Pt(12)  # Optional: Set the font size
    second_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    second_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    intro_text = (
        "I have received "
        + str(state.num_pages)
        + " pages of medical records from "
        + lawfirm
        + ". I have reviewed all of the pages  received and my opinion is based upon such received records."
    )

    second_intro_text = "The following is a summary of those records:"
    this_concludes_text = "This concludes the review of submitted records."

    # First Line
    third_title = doc.add_paragraph(intro_text)
    third_title_format = third_title.runs[0]  # Access the first run in the paragraph
    third_title_format.bold = False  # Make the text bold
    third_title_format.underline = False  # Underline the text
    third_title_format.font.size = Pt(12)  # Optional: Set the font size
    third_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    third_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    # Second Line
    fourth_title = doc.add_paragraph(second_intro_text)
    fourth_title_format = fourth_title.runs[0]  # Access the first run in the paragraph
    fourth_title_format.bold = True  # Make the text bold
    fourth_title_format.underline = False  # Underline the text
    fourth_title_format.font.size = Pt(12)  # Optional: Set the font size
    fourth_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    fourth_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    big_text = ""
    for entry in state.all_data:
        big_text += (
            f"_{entry['summaryDate']}_\t****{entry['summaryTitle']}****: {entry['summaryText']}"
        )

        main_paragraph = doc.add_paragraph(big_text)
        big_text = ""

    for run in main_paragraph.runs:  # Ensure all runs in the paragraph use Times New Roman
        run.font.name = "Times New Roman"
        run.font.size = Pt(11)  # Optional: Set a standard font size for the content

    nine_title = doc.add_paragraph(this_concludes_text)  # noqa: F841
    nine_title_format = fourth_title.runs[0]  # Access the first run in the paragraph
    nine_title_format.bold = False  # Make the text bold
    nine_title_format.underline = False  # Underline the text
    nine_title_format.font.size = Pt(12)  # Optional: Set the font size
    nine_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    nine_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    file_path_int = os.path.join(os.path.expanduser("~"), "MRRs", state.main_filename + "_int.docx")
    file_path_rep = os.path.join(os.path.expanduser("~"), "MRRs", state.main_filename + "_rep.docx")

    doc.save(file_path_int)

    # Return the document as a downloadable file with explicit headers
    response_int = send_file(
        file_path_int,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    doc.save(file_path_rep)

    response_rep = send_file(
        file_path_rep,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    response_int.headers["Content-Disposition"] = "attachment; filename=summaries.docx"
    response_rep.headers["Content-Disposition"] = "attachment; filename=summaries.docx"

    return response_int


@bp.route("/exportResultsToWordFileIndivRecords", methods=["POST"])
def exportResultsToWordFileIndivRecords():
    patientName = request.json.get("patientName")
    patientdob = request.json.get("patientdob")
    QMEorAME = request.json.get("QMEorAME")
    lawfirm = request.json.get("lawfirm")

    # Check if main_filename is None and set a default value if needed
    if state.main_filename is None:
        state.main_filename = "default_filename"

    def safe_date_parse(entry):
        try:
            return datetime.strptime(entry.get("summaryDate", ""), "%m/%d/%Y")
        except ValueError:
            return datetime.min  # Assign a very early date as fallback

    state.all_data = sorted(state.all_data, key=safe_date_parse)

    # Create a new Word document and add the content
    doc = Document()

    # HEADER
    section = doc.sections[0]  # Access the first section of the document
    header = section.header  # Access the header of the section
    header_paragraph = header.add_paragraph(
        "RE: " + patientName + "\n" + patientdob + "\n" + "Page "
    )
    for run in header_paragraph.runs:
        run.font.name = "Times New Roman"
        run.font.size = Pt(10)  # Optional: Set a font size for the header

    # Add empty lines for spacing
    doc.add_paragraph("")

    # TITLE
    title = doc.add_paragraph(QMEorAME)
    title_format = title.runs[0]  # Access the first run in the paragraph
    title_format.bold = True  # Make the text bold
    title_format.underline = True  # Underline the text
    title_format.font.size = Pt(12)  # Optional: Set the font size
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER  # Center the title
    title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    # Add empty lines for spacing
    doc.add_paragraph("")

    # Medical Record Review
    second_title = doc.add_paragraph("Medical Record Review")
    second_title_format = second_title.runs[0]  # Access the first run in the paragraph
    second_title_format.bold = True  # Make the text bold
    second_title_format.underline = True  # Underline the text
    second_title_format.font.size = Pt(12)  # Optional: Set the font size
    second_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    second_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    intro_text = (
        "I have received "
        + str(state.num_pages)
        + " pages of medical records from "
        + lawfirm
        + ". I have reviewed all of the pages  received and my opinion is based upon such received records."
    )

    second_intro_text = "The following is a summary of those records:"
    this_concludes_text = "This concludes the review of submitted records."

    # First Line
    third_title = doc.add_paragraph(intro_text)
    third_title_format = third_title.runs[0]  # Access the first run in the paragraph
    third_title_format.bold = False  # Make the text bold
    third_title_format.underline = False  # Underline the text
    third_title_format.font.size = Pt(12)  # Optional: Set the font size
    third_title.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    third_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    # Second Line
    fourth_title = doc.add_paragraph(second_intro_text)
    fourth_title_format = fourth_title.runs[0]  # Access the first run in the paragraph
    fourth_title_format.bold = True  # Make the text bold
    fourth_title_format.underline = False  # Underline the text
    fourth_title_format.font.size = Pt(12)  # Optional: Set the font size
    fourth_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    fourth_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    big_text = ""
    for entry in state.all_data:
        big_text += (
            f"_{entry['summaryDate']}_\t****{entry['summaryTitle']}****: {entry['summaryText']}"
        )

        main_paragraph = doc.add_paragraph(big_text)
        big_text = ""

    for run in main_paragraph.runs:  # Ensure all runs in the paragraph use Times New Roman
        run.font.name = "Times New Roman"
        run.font.size = Pt(11)  # Optional: Set a standard font size for the content

    nine_title = doc.add_paragraph(this_concludes_text)  # noqa: F841
    nine_title_format = fourth_title.runs[0]  # Access the first run in the paragraph
    nine_title_format.bold = False  # Make the text bold
    nine_title_format.underline = False  # Underline the text
    nine_title_format.font.size = Pt(12)  # Optional: Set the font size
    nine_title_format.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT  # Align the title to the left
    nine_title_format.font.name = "Times New Roman"  # Set the font to Times New Roman

    file_path = os.path.join(os.path.expanduser("~"), "MRRs", patientName + " - MRR.docx")
    doc.save(file_path)

    # Return the document as a downloadable file with explicit headers
    response = send_file(
        file_path,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    response.headers["Content-Disposition"] = "attachment; filename=summaries.docx"

    return response
