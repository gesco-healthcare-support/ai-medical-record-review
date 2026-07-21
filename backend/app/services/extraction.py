"""Extract the report-header fields (patient name/DOB, law firm) from a record via Vertex.

Replaces the classic OpenAI extraction (extraction.py getpatientnameanddob + getlawfirm). OCRs the
first pages, then asks Gemini for a single structured JSON object so the modern flow can prefill the
export/bundle header in one call. Vertex-only (BAA path); PHI OCR text stays on the BAA endpoint.
"""

import json

from google.genai import types

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages

_HEADER_SYSTEM = (
    "You extract administrative header fields from a California workers'-compensation medical "
    "record. Return the patient's FIRST name and LAST name separately, the patient's date of "
    "birth, and the attorney/law firm that sent the record (this is on the declaration page and "
    "is NOT the treating doctor)."
)

_HEADER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "first_name": {
            "type": "STRING",
            "description": "Patient first (given) name, or '' if not found",
        },
        "last_name": {
            "type": "STRING",
            "description": "Patient last (family) name, or '' if not found",
        },
        "dob": {"type": "STRING", "description": "Patient date of birth mm/dd/yyyy, or ''"},
        "lawfirm": {"type": "STRING", "description": "Sending attorney + law firm, or ''"},
    },
    "required": ["first_name", "last_name", "dob", "lawfirm"],
}

_BLANK = {"first_name": "", "last_name": "", "dob": "", "lawfirm": ""}


def extract_header(pdf_path, pages) -> dict:
    """OCR ``pages`` and extract {first_name, last_name, dob, lawfirm} via Vertex; blanks when
    nothing is found."""
    text = extract_text_from_selected_pages(pdf_path, pages)
    if not text.strip():
        return dict(_BLANK)

    response = generate_with_retry(
        get_genai_client(),
        model=get_settings().genai_model,
        contents=(
            "Extract the patient's first name and last name (separately), the patient's date of "
            "birth (mm/dd/yyyy), and the attorney/law firm that sent the record from this text:"
            "\n\n" + text
        ),
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=_HEADER_SCHEMA,
            system_instruction=_HEADER_SYSTEM,
        ),
    )
    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return dict(_BLANK)
    return {key: (data.get(key) or "") for key in _BLANK}
