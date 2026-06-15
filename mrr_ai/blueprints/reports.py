"""Diagnostic/operative + deposition report extraction, and individual-MRR page ranges."""

import csv
import os

from flask import Blueprint, jsonify, request
from pypdf import PdfReader, PdfWriter

from mrr_ai import state
from mrr_ai.config import UPLOAD_BASE_DIR
from mrr_ai.services.files import safe_name

bp = Blueprint("reports", __name__)


@bp.route("/getDiagOpRep", methods=["POST"])
def getDiagOpRep():
    model = request.json.get("model")  # noqa: F841

    # File paths
    csv_file = state.txt_filepath
    pdf_file = state.pdf_filepath

    output_folder = os.path.join(
        os.path.expanduser("~"), "MRRs", state.main_filename + "_diag_and_op_reports"
    )
    final_pdf_path = os.path.join(output_folder, state.main_filename + "_diag_and_op_reports.pdf")

    # Create output directory if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # List to keep track of extracted PDFs
    extracted_pdfs = []

    # Process CSV
    with open(csv_file, newline="", encoding="utf-8") as file:
        reader = csv.reader(file)

        for row in reader:
            if len(row) >= 3 and row[2].strip() in ["3", "8"]:  # Check if 3rd value is "3" or "8"
                try:
                    start_page = int(row[0]) - 1  # Convert to 0-based index
                    end_page = int(row[1])  # End page (inclusive)

                    # Extract pages from PDF
                    with open(pdf_file, "rb") as pdf:
                        reader = PdfReader(pdf)
                        writer = PdfWriter()

                        for page_num in range(start_page, end_page):
                            writer.add_page(reader.pages[page_num])

                        # Save extracted PDF
                        output_pdf_path = os.path.join(
                            output_folder, f"segment_{start_page + 1}_to_{end_page}.pdf"
                        )
                        with open(output_pdf_path, "wb") as output_pdf:
                            writer.write(output_pdf)

                        extracted_pdfs.append(output_pdf_path)
                        print(
                            f"Extracted pages {start_page + 1} to {end_page} into {output_pdf_path}"
                        )

                except Exception as e:
                    print(f"Error processing row {row}: {e}")

    # Combine all extracted PDFs into a single PDF
    if extracted_pdfs:
        final_writer = PdfWriter()

        for pdf_path in extracted_pdfs:
            with open(pdf_path, "rb") as pdf_file:
                reader = PdfReader(pdf_file)
                for page in reader.pages:
                    final_writer.add_page(page)

        # Save combined PDF
        with open(final_pdf_path, "wb") as final_pdf:
            final_writer.write(final_pdf)

        print(f"Combined all segments into {final_pdf_path}")

        # Delete individual extracted PDFs
        for pdf_path in extracted_pdfs:
            os.remove(pdf_path)
            print(f"Deleted {pdf_path}")

    print("Process completed successfully.")
    return {"summaryText": "success"}  # we are only using big text to show though


@bp.route("/getDepoRep", methods=["POST"])
def getDepoRep():
    model = request.json.get("model")  # noqa: F841

    csv_file = state.txt_filepath
    pdf_file = state.pdf_filepath

    output_text = ""
    deposition_count = 0  # Counter for depositions

    output_folder = state.main_filename + "_depositions"
    # Define output directory in ~/MRRs/
    output_folder = os.path.join(
        os.path.expanduser("~"), "MRRs", state.main_filename + "_depositions"
    )

    # Create output directory if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # List to keep track of extracted PDFs
    extracted_pdfs = []

    # Process CSV
    with open(csv_file, newline="", encoding="utf-8") as file:
        reader = csv.reader(file)

        for row in reader:
            if len(row) >= 3 and row[2].strip() in ["9"]:  # Check if 3rd value is "9"
                try:
                    start_page = int(row[0]) - 1  # Convert to 0-based index
                    end_page = int(row[1])  # End page (inclusive)

                    # Extract pages from PDF
                    with open(pdf_file, "rb") as pdf:
                        reader = PdfReader(pdf)
                        writer = PdfWriter()

                        for page_num in range(start_page, end_page):
                            writer.add_page(reader.pages[page_num])

                        # Save extracted PDF
                        output_pdf_path = os.path.join(
                            output_folder,
                            f"{state.main_filename}_Deposition_{start_page + 1}_to_{end_page}.pdf",
                        )
                        with open(output_pdf_path, "wb") as output_pdf:
                            writer.write(output_pdf)

                        extracted_pdfs.append(output_pdf_path)
                        deposition_count += 1  # Increment deposition count
                        print(
                            f"Extracted pages {start_page + 1} to {end_page} into {output_pdf_path}"
                        )

                except Exception as e:
                    print(f"Error processing row {row}: {e}")

    print("Process completed successfully.")
    output_text = f"Total Depositions: {deposition_count}\n"

    return {"summaryText": output_text}  # we are only using big text to show though


@bp.route("/compute_page_ranges", methods=["POST"])
def compute_page_ranges():
    """Compute page ranges for PDFs and merge them into a single file."""

    data = request.json
    folder_name = data.get("folder_name")

    if not folder_name:
        return jsonify({"error": "Missing folder name"}), 400

    # Sanitize before building a path (prevents traversal via the folder name).
    folder_path = os.path.join(UPLOAD_BASE_DIR, safe_name(folder_name))

    if not os.path.exists(folder_path):
        return jsonify({"error": f"Folder '{folder_path}' does not exist"}), 400

    pdf_files = sorted([f for f in os.listdir(folder_path) if f.endswith(".pdf")])
    if not pdf_files:
        return jsonify({"error": "No PDF files found in folder"}), 400

    page_ranges = []
    current_page = 1
    merger = PdfWriter()

    for pdf_file in pdf_files:
        pdf_path = os.path.join(folder_path, pdf_file)

        try:
            pdf_reader = PdfReader(pdf_path)
            num_pages = len(pdf_reader.pages)
        except Exception as e:
            print(f"Error reading {pdf_file}: {e}")
            num_pages = 0

        if num_pages > 0:
            start_page = current_page
            end_page = current_page + num_pages - 1
            page_ranges.append(f"{start_page}-{end_page}")
            current_page = end_page + 1  # Move to the next range

            # Add PDF to merger
            merger.append(pdf_path)

    # Save the merged PDF as 'AAA.pdf' inside the folder
    merged_pdf_path = os.path.join(
        os.path.expanduser("~"), "MRRs", state.patientNameGlobal + " - AGGREGATED_RECORDS.pdf"
    )
    merger.write(merged_pdf_path)
    merger.close()

    return jsonify({"status": "success", "page_ranges": page_ranges, "merged_pdf": merged_pdf_path})
