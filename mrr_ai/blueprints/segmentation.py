"""Gemini-driven sub-document segmentation routes."""

import json

from flask import Blueprint, request
from google.genai import types

from mrr_ai import state
from mrr_ai.extensions import genai_client
from mrr_ai.services.classification import classify
from mrr_ai.services.gemini import (
    SEGMENT_RESPONSE_SCHEMA,
    SEGMENTATION_PROMPT,
    parse_segment_item,
    upload_to_gemini,
    wait_for_files_active,
)
from mrr_ai.services.ocr import extract_text_from_selected_pages
from mrr_ai.services.pdf import (
    get_pdf_page_count,
    get_pdf_size,
    segment_pdf,
    segment_pdf_locally,
)

bp = Blueprint("segmentation", __name__)


def _format_segment_line(item, offset):
    """Parse one Gemini segment, categorize it, and format its CSV line.

    Categorization uses the B5 cascade (rules -> embeddings -> Gemini enum). When the title
    alone is low-confidence, it escalates by OCR-ing the sub-document's first page. A
    low-confidence result (or a manual flag from Gemini) sets the manual-review flag.

    Raises KeyError/TypeError/ValueError on a malformed item so the caller can skip it.
    """
    start_page, end_page, title, date, injury_date, manual_check = parse_segment_item(item)

    result = classify(title)
    if result.needs_review:
        # Title alone was inconclusive: escalate with the sub-document's first page of text.
        try:
            page_text = extract_text_from_selected_pages(state.pdf_filepath, [start_page + offset])
            if page_text.strip():
                result = classify(title, page_text=page_text)
        except Exception as exc:
            print(f"Classification escalation OCR failed: {exc}")

    flag = "x" if (result.needs_review or manual_check.strip().lower() == "x") else manual_check
    return (
        f"{start_page + offset},{end_page + offset},{result.category},{date},{injury_date},{flag}"
    )


def _segment_one_pdf(pdf_path, generation_config, segmentation_model):
    """Upload one PDF to Gemini, run segmentation, and return its parsed JSON array."""
    files = [upload_to_gemini(pdf_path, mime_type="application/pdf")]
    wait_for_files_active(files)

    response = genai_client.models.generate_content(
        model=segmentation_model,
        contents=[files[0], SEGMENTATION_PROMPT],
        config=generation_config,
    )
    clean_response = response.text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean_response)


@bp.route("/segmentPDF", methods=["POST"])
def segmentPDF():
    print("inside segmented PDF")

    segment_pdf(state.pdf_filepath, pages_per_segment=100)
    return {"pages": "File segmentation finalyzed. You can get the files from the MRR folder."}


# Automatic segmentation upload
@bp.route("/getPages", methods=["POST"])
def getPages():
    data = request.json

    # Extract the pageDelimiter value, default to 100 if not provided or invalid.
    try:
        page_delimiter = int(data.get("pageDelimiter", 100))
    except (TypeError, ValueError):
        page_delimiter = 100
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
        # The schema (not prose) guarantees parseable, typed records; values still get validated.
        response_schema=SEGMENT_RESPONSE_SCHEMA,
        system_instruction=(
            "You are an expert medical-records clerk. You split scanned workers' compensation "
            "medical-record files into their component documents and report exact page ranges "
            "and metadata."
        ),
    )

    # Large documents are split into page-bounded chunks; each chunk's page numbers are local
    # (1..delimiter) and shifted by current_offset to recover absolute page numbers.
    if pdf_size > 45 or pdf_pages > 100:
        print("PDF is large. Will segment batches.")
        state.sorted_file_paths = segment_pdf_locally(
            state.pdf_filepath, pages_per_segment=page_delimiter
        )
        pdf_paths = state.sorted_file_paths
    else:
        print("PDF is not large. Proceeding.")
        pdf_paths = [state.pdf_filepath]

    current_offset = 0
    lines = []
    for pdf_path in pdf_paths:
        print(f"Working on: {pdf_path}")
        try:
            segments = _segment_one_pdf(pdf_path, generation_config, segmentation_model)
        except Exception as e:
            print(f"An error occurred while sending the message: {e}")
            return {"pages": str(e)}

        for item in segments:
            try:
                lines.append(_format_segment_line(item, current_offset))
            except (KeyError, TypeError, ValueError) as exc:
                print(f"Skipping malformed segment: {exc}")
                continue

        current_offset += page_delimiter

    result = "\n".join(lines)
    return {"pages": result}
