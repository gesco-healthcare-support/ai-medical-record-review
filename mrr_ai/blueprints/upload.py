"""PDF and CSV/page-range upload routes."""

import csv
import os

from flask import Blueprint, current_app, request
from pypdf import PdfReader

from mrr_ai import state
from mrr_ai.services.files import count_lines_in_file, is_valid_date, safe_name

bp = Blueprint("upload", __name__)


@bp.route("/upload", methods=["POST"])
def upload():
    print("Upload is pressed.")

    file = request.files["pdf"]
    if file:
        # Sanitize the user-supplied filename before building a path (prevents traversal).
        filename = safe_name(file.filename)
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        # Read PDF and extract page count
        reader = PdfReader(filepath)
        state.num_pages = len(reader.pages)

        state.pdf_filepath = filepath
        state.main_filename = filename
        state.main_filename = str(state.main_filename.replace(".pdf", ""))

        print("num_pages", state.num_pages)
        print("main_filename", state.main_filename)

        return {"filepath": filename, "num_pages": state.num_pages}


@bp.route("/uploadAndCheckCSV", methods=["POST"])
def uploadAndCheckCSV():
    print("Inside uploadAndCheckCSV")

    file = request.files["txt"]
    output_messages = []  # Accumulate messages here

    if file:
        filename = safe_name(file.filename)
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        state.txt_filepath = filepath
        state.main_txt_filename = filename
        state.main_txt_filename = str(state.main_filename.replace(".txt", ""))

        # Read and validate the CSV file
        error_lines = []
        duplicate_groups = {}
        unique_rows = []

        try:
            with open(state.txt_filepath) as file:
                csv_reader = csv.reader(file)
                for line_number, row in enumerate(csv_reader, start=1):
                    if len(row) < 4:
                        error_lines.append((line_number, "Missing column(s)"))
                        continue

                    # Check for date validity
                    date_value = row[3]
                    if not is_valid_date(date_value):
                        error_lines.append((line_number, row))

                    # Check for duplicates (type and date)
                    duplicate_key = (
                        row[2].strip(),
                        row[3].strip(),
                    )  # Ensure both fields are stripped of whitespace
                    if duplicate_key in duplicate_groups:
                        duplicate_groups[duplicate_key].append((line_number, row))
                    else:
                        duplicate_groups[duplicate_key] = [(line_number, row)]

            # Separate duplicates and unique rows
            duplicate_rows = []
            for key, group in duplicate_groups.items():  # noqa: B007
                if len(group) > 1:
                    duplicate_rows.extend(group)
                else:
                    unique_rows.append(group[0])

            # Output the results
            if error_lines:
                output_messages.append("Lines with errors or missing columns:")
                output_messages.append("--------------------------------------")
                for error in error_lines:
                    output_messages.append(f"Line {error[0]}: {error[1]}")

            output_messages.append("")

            if duplicate_rows:
                output_messages.append("Duplicate rows detected:")
                output_messages.append("------------------------")
                grouped_duplicates = {}
                for line_number, row in duplicate_rows:
                    key = (row[2].strip(), row[3].strip())
                    if key not in grouped_duplicates:
                        grouped_duplicates[key] = []
                    grouped_duplicates[key].append((line_number, row))

                for group in grouped_duplicates.values():
                    for line_number, row in group:
                        output_messages.append(f"Line {line_number}: {row}")
                    output_messages.append("")  # Add a space between groups

            if not error_lines and not duplicate_rows:
                output_messages.append("All rows are valid and no duplicates were found.")

        except Exception as e:
            output_messages.append(f"Error reading file: {str(e)}")

    else:
        output_messages.append("No file was uploaded.")

    # Return the messages in a formatted way for a textbox
    return {"errors_and_duplicates": "\n".join(output_messages)}


@bp.route("/uploadPages", methods=["POST"])
def uploadPages():
    file = request.files["txt"]
    if file:
        filename = safe_name(file.filename)
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        state.txt_filepath = filepath
        state.main_txt_filename = filename
        state.main_txt_filename = str(state.main_filename.replace(".txt", ""))

        line_count = count_lines_in_file(state.txt_filepath)

        return {"filepath": state.txt_filepath, "line_count": line_count}
