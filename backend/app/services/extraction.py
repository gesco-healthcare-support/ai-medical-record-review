"""Extract the report-header fields (patient name/DOB, law firm) from a record.

Replaces the classic OpenAI extraction (extraction.py getpatientnameanddob + getlawfirm). OCRs the
first pages, then asks for a single structured JSON object so the modern flow can prefill the
export/bundle header in one call.

WHERE THE PHI GOES IS NOW A CONFIG VALUE, not a property of this file. This used to read
"Vertex-only (BAA path)", which stopped being true the moment `extract` could resolve to another
backend: the destination is whatever `backend_for("extract")` selects, and the production guard on
that is `_validate_vllm_backend`'s approved-origin check rather than anything here.
"""

import json
import logging

from app.config import get_settings
from app.services.llm import TextPart, get_provider
from app.services.ocr import extract_pages_with_report

logger = logging.getLogger(__name__)

_HEADER_SYSTEM = (
    "You extract administrative header fields from a California workers'-compensation medical "
    "record. Return the patient's FIRST name and LAST name separately, the patient's date of "
    "birth, and the attorney/law firm that sent the record (this is on the declaration page and "
    "is NOT the treating doctor)."
)

# Ordinary JSON Schema, lowercase. The seam translates per backend - `gemini.py`'s
# `to_gemini_schema` rewrites these type names into google-genai's uppercase dialect - so writing
# one vendor's spelling here would silently make that vendor the default and the other the special
# case. The shape is otherwise unchanged from the uppercase version this replaced.
_HEADER_SCHEMA = {
    "type": "object",
    "properties": {
        "first_name": {
            "type": "string",
            "description": "Patient first (given) name, or '' if not found",
        },
        "last_name": {
            "type": "string",
            "description": "Patient last (family) name, or '' if not found",
        },
        "dob": {"type": "string", "description": "Patient date of birth mm/dd/yyyy, or ''"},
        "lawfirm": {"type": "string", "description": "Sending attorney + law firm, or ''"},
    },
    "required": ["first_name", "last_name", "dob", "lawfirm"],
}

_BLANK = {"first_name": "", "last_name": "", "dob": "", "lawfirm": ""}


def extract_header(pdf_path, pages) -> dict:
    """OCR ``pages`` and extract {first_name, last_name, dob, lawfirm} via Vertex; blanks when
    nothing is found.

    Reads through ``extract_pages_with_report``, NOT ``extract_text_from_selected_pages`` (#211).
    The latter catches a per-page Tesseract failure and continues, so a dropped page is
    indistinguishable from a page with no words on it - and the two failures are not equally safe
    here:

    * EVERY page fails -> empty text -> `_BLANK` -> the reviewer meets four empty fields and fills
      them in. Visible and recoverable.
    * ONE page fails -> a header extracted from what survived, which LOOKS complete. The four
      fields are reviewer-facing on the landing table and travel into the deliverable, so a name or
      a date of birth taken from a partial read is indistinguishable from one taken from the whole.

    A partial read is now attributable: the errored pages are logged by number. That is deliberately
    all it does - surfacing "this header came from a partial read" to the REVIEWER is a product
    decision about what the landing table should then show, and is left open on the issue rather
    than invented here.
    """
    text, report = extract_pages_with_report(pdf_path, pages)
    if report["errored"]:
        # Page numbers only: non-PHI, and enough to re-read those pages by hand.
        logger.warning(
            "header extracted from a PARTIAL read: %d of %d page(s) could not be OCR'd (%s)",
            len(report["errored"]),
            len(report["pages"]),
            report["errored"],
        )
    if not text.strip():
        return dict(_BLANK)

    response = get_provider().generate_structured(
        # Resolved for the backend that will answer this stage, not read from genai_model directly.
        # That setting is shared by four stages (segment, extract, doi, deposition), so reading it
        # here would pin this call to whatever Gemini name they share even when `extract` has been
        # routed elsewhere.
        model=get_settings().model_for_stage("extract"),
        system=_HEADER_SYSTEM,
        parts=[
            TextPart(
                "Extract the patient's first name and last name (separately), the patient's date of "
                "birth (mm/dd/yyyy), and the attorney/law firm that sent the record from this text:"
                "\n\n" + text
            )
        ],
        schema=_HEADER_SCHEMA,
        temperature=0.0,
        # No max_output_tokens: this call has never had one, and the seam sends the field only when
        # it is set, so omitting it keeps the call uncapped exactly as before.
        stage="extract",
    )
    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return dict(_BLANK)
    return {key: (data.get(key) or "") for key in _BLANK}
