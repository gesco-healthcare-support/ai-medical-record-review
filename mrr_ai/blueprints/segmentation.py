"""Gemini-driven sub-document segmentation routes."""

import json

from flask import Blueprint, request
from google.genai import types

from mrr_ai import state
from mrr_ai.extensions import genai_client
from mrr_ai.groups import groups
from mrr_ai.services.categorization import categorize_documents
from mrr_ai.services.gemini import (
    SEGMENTATION_PROMPT,
    parse_segment_item,
    upload_to_gemini,
    wait_for_files_active,
)
from mrr_ai.services.pdf import (
    get_pdf_page_count,
    get_pdf_size,
    segment_pdf,
    segment_pdf_locally,
)

bp = Blueprint("segmentation", __name__)


@bp.route("/segmentPDF", methods=["POST"])
def segmentPDF():
    print("inside segmented PDF")

    segment_pdf(state.pdf_filepath, pages_per_segment=100)
    return {"pages": "File segmentation finalyzed. You can get the files from the MRR folder."}


# Automatic segmentation upload
@bp.route("/getPages", methods=["POST"])
def getPages():
    # Get the JSON data from the request
    data = request.json

    # Extract the pageDelimiter value, default to 100 if not provided
    page_delimiter = data.get("pageDelimiter", 100)

    # Ensure pageDelimiter is an integer
    try:
        page_delimiter = int(page_delimiter)
    except ValueError:
        page_delimiter = 100

    # Process page_delimiter as needed
    print(f"Page delimiter received: {page_delimiter}")

    pdf_size = get_pdf_size(state.pdf_filepath)
    pdf_pages = get_pdf_page_count(state.pdf_filepath)
    print(f"PDF Size: {pdf_size} MB, PDF Pages: {pdf_pages}")

    segmentation_model = "gemini-flash-latest"
    generation_config = types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        top_k=40,
        response_mime_type="application/json",
        system_instruction="You are an assistant that segments a large document into subdocuments and provide their metadata.",
    )

    if pdf_size > 45 or pdf_pages > 100:
        print("PDF is large. Will segment batches.")
        state.sorted_file_paths = segment_pdf_locally(
            state.pdf_filepath, pages_per_segment=page_delimiter
        )

        current_offset = 0
        lines = []

        for pdf_path in state.sorted_file_paths:
            print(f"Working on: {pdf_path}")
            files = [upload_to_gemini(pdf_path, mime_type="application/pdf")]
            wait_for_files_active(files)
            print("The file has been uploaded to sucessfully.")

            print("Preparing for the AI")

            prompt = SEGMENTATION_PROMPT

            try:
                response = genai_client.models.generate_content(
                    model=segmentation_model,
                    contents=[files[0], prompt],
                    config=generation_config,
                )
                print(f"Response received: {response}")
            except Exception as e:
                print(f"An error occurred while sending the message: {e}")
                return {"pages": str(e)}

            print(response)
            print()
            print(response.text)
            print("Response received from server.")

            clean_response = response.text.replace("```json", "").replace("```", "").strip()
            clean_response_json = json.loads(clean_response)

            print("Response converted to JSON")

            print(clean_response_json)
            print("Response formatted.")

            for item in clean_response_json:
                try:
                    start_page, end_page, title, date, injury_date, manual_check = (
                        parse_segment_item(item)
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    print(f"Skipping malformed segment: {exc}")
                    continue

                title_group = categorize_documents(title, groups)

                line = f"{start_page + current_offset},{end_page + current_offset},{title_group},{date},{injury_date},{manual_check}"
                lines.append(line)

            print("All lines are created for this document")
            current_offset += page_delimiter
    else:
        print("PDF is not large. Proceeding.")
        current_offset = 0
        lines = []

        print(f"Working on: {state.pdf_filepath}")
        files = [upload_to_gemini(state.pdf_filepath, mime_type="application/pdf")]
        wait_for_files_active(files)
        print("The file has been uploaded to sucessfully.")

        print("AI Process Starting ....")

        prompt = SEGMENTATION_PROMPT

        try:
            response = genai_client.models.generate_content(
                model=segmentation_model,
                contents=[files[0], prompt],
                config=generation_config,
            )
            print(f"Response received: {response}")
        except Exception as e:
            print(f"An error occurred while sending the message: {e}")
            return {"pages": str(e)}

        print(response)
        print()
        print(response.text)
        print("Response is received from server.")

        clean_response = response.text.replace("```json", "").replace("```", "").strip()
        clean_response_json = json.loads(clean_response)

        print("Response is converted to JSON")

        print(clean_response_json)
        print("Response is formatted.")

        for item in clean_response_json:
            try:
                start_page, end_page, title, date, injury_date, manual_check = parse_segment_item(
                    item
                )
            except (KeyError, TypeError, ValueError) as exc:
                print(f"Skipping malformed segment: {exc}")
                continue

            title_group = categorize_documents(title, groups)

            line = f"{start_page + current_offset},{end_page + current_offset},{title_group},{date},{injury_date},{manual_check}"
            lines.append(line)

    # Join all lines into a single result string
    result = "\n".join(lines)
    return {"pages": result}
