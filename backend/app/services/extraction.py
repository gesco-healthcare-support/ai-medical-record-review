"""Extract the report-header fields (patient name/DOB, law firm) from a record.

Replaces the classic OpenAI extraction (extraction.py getpatientnameanddob + getlawfirm). OCRs the
first pages, then asks for a single structured JSON object so the modern flow can prefill the
export/bundle header in one call.

The same call can also read the covering letter's fields (attorney, evaluating doctor, letter date,
pages received) - only those `HEADER_PREFILL_FIELDS` turns on, so with it empty the call is exactly
the four-field one. `prefill_letter_fields` writes them onto a record where they are still empty.

WHERE THE PHI GOES IS NOW A CONFIG VALUE, not a property of this file. This used to read
"Vertex-only (BAA path)", which stopped being true the moment `extract` could resolve to another
backend: the destination is whatever `backend_for("extract")` selects, and the production guard on
that is `_validate_vllm_backend`'s approved-origin check rather than anything here.
"""

import json
import logging
import re

from app.config import HEADER_LETTER_FIELDS, get_settings
from app.services.llm import TextPart, provider_for_stage
from app.services.ocr import extract_pages_with_report
from app.services.reporting import DOCTORS

logger = logging.getLogger(__name__)

_HEADER_SYSTEM = (
    "You extract administrative header fields from a California workers'-compensation medical "
    "record. Return the patient's FIRST name and LAST name separately, the patient's date of "
    "birth, and the attorney/law firm that sent the record (this is on the declaration page and "
    "is NOT the treating doctor)."
)
# Added to the system message only when a letter field is asked for.
_LETTER_SYSTEM = (
    " The record usually opens with a covering letter that sends the records to an evaluating "
    "physician (a QME, AME or PQME). Where asked, also return from that letter: the attorney who "
    "signed it, the evaluating physician it is addressed to (never a treating doctor named in the "
    "records), its date, and the number of pages it says are enclosed."
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

# One property per `HEADER_LETTER_FIELDS` entry, added to the schema only when it is asked for.
_LETTER_PROPERTIES = {
    "attorney_name": {
        "type": "string",
        "description": "Name of the attorney who signed the covering letter (the person, not the "
        "firm), or ''",
    },
    "doctor": {
        "type": "string",
        "description": "Last name of the evaluating physician (QME, AME or PQME) the covering "
        "letter sends the records to - not a treating doctor - or ''",
    },
    "letter_date": {
        "type": "string",
        "description": "Date of the covering letter, mm/dd/yyyy, or ''",
    },
    "pages_received": {
        "type": "string",
        "description": "Number of pages the covering letter says are enclosed, digits only, or ''",
    },
}

# What each letter field is called in the instruction sentence.
_LETTER_ASKS = {
    "attorney_name": "the attorney who signed the covering letter",
    "doctor": "the evaluating physician's last name",
    "letter_date": "the covering letter's date (mm/dd/yyyy)",
    "pages_received": "the number of pages it says are enclosed",
}

_BLANK = {"first_name": "", "last_name": "", "dob": "", "lawfirm": ""}

_DATE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")


def _doctor(value: str) -> str:
    """The `DOCTORS` name ``value`` names, or '' - the dropdown holds only those, so anything else
    (a treating doctor, two names, a first name) is not a pre-fill."""
    named = [
        doctor for doctor in DOCTORS if re.search(rf"\b{re.escape(doctor)}\b", value, re.IGNORECASE)
    ]
    return named[0] if len(named) == 1 else ""


def _letter_date(value: str) -> str:
    """``value`` as mm/dd/yyyy with a real month and day, or ''."""
    match = _DATE.match(value)
    if not match:
        return ""
    month, day, year = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{month:02d}/{day:02d}/{year}"


def _page_count(value: str) -> str:
    """``value`` when it is a whole number above 0 (as digits), or ''."""
    text = value.strip()
    return str(int(text)) if text.isdigit() and int(text) > 0 else ""


_CLEANERS = {
    "attorney_name": lambda value: " ".join(value.split()),
    "doctor": _doctor,
    "letter_date": _letter_date,
    "pages_received": _page_count,
}


def _request(letter_fields):
    """The system message, instruction and schema for the base four plus ``letter_fields``."""
    instruction = (
        "Extract the patient's first name and last name (separately), the patient's date of "
        "birth (mm/dd/yyyy), and the attorney/law firm that sent the record"
    )
    if not letter_fields:
        return _HEADER_SYSTEM, instruction + " from this text:", _HEADER_SCHEMA
    asks = ", ".join(_LETTER_ASKS[field] for field in letter_fields)
    schema = {
        "type": "object",
        "properties": {
            **_HEADER_SCHEMA["properties"],
            **{field: _LETTER_PROPERTIES[field] for field in letter_fields},
        },
        "required": [*_HEADER_SCHEMA["required"], *letter_fields],
    }
    return (
        _HEADER_SYSTEM + _LETTER_SYSTEM,
        f"{instruction}; from the covering letter also {asks}, from this text:",
        schema,
    )


def extract_header(pdf_path, pages, letter_fields=None) -> dict:
    """OCR ``pages`` and extract {first_name, last_name, dob, lawfirm} via Vertex; blanks when
    nothing is found.

    ``letter_fields`` adds those `HEADER_LETTER_FIELDS` to the same call and the result; None reads
    `HEADER_PREFILL_FIELDS` (empty by default, which is exactly the four-field call). A letter field
    is '' unless its value is usable as typed: a doctor on the `DOCTORS` list, a date as mm/dd/yyyy,
    a page count as a whole number.

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
    if letter_fields is None:
        letter_fields = get_settings().header_letter_fields()
    letter_fields = tuple(field for field in HEADER_LETTER_FIELDS if field in letter_fields)
    blank = dict(_BLANK, **dict.fromkeys(letter_fields, ""))
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
        return blank

    system, instruction, schema = _request(letter_fields)
    # BOTH halves resolve through `extract`. A bare `get_provider()` would resolve the TRANSPORT
    # through backend_for("summarize") while the model below resolved through backend_for("extract")
    # - so a run with only `extract` moved would send the pod's model name over the Gemini
    # transport. Shipped that way in #318 and caught in review; the pairing is the fix.
    response = provider_for_stage("extract").generate_structured(
        # Resolved for the backend that will answer this stage, not read from genai_model directly.
        # That setting is shared by four stages (segment, extract, doi, deposition), so reading it
        # here would pin this call to whatever Gemini name they share even when `extract` has been
        # routed elsewhere.
        model=get_settings().model_for_stage("extract"),
        system=system,
        parts=[TextPart(instruction + "\n\n" + text)],
        schema=schema,
        temperature=0.0,
        # No max_output_tokens: this call has never had one, and the seam sends the field only when
        # it is set, so omitting it keeps the call uncapped exactly as before.
        stage="extract",
    )
    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        return blank
    result = {key: (data.get(key) or "") for key in _BLANK}
    for field in letter_fields:
        result[field] = _CLEANERS[field](str(data.get(field) or ""))
    return result


def prefill_letter_fields(document, header) -> list[str]:
    """Write each letter field ``header`` found onto ``document`` where it is still EMPTY.

    Never over a value a reviewer typed, so a re-identify or a second "Detect header" leaves their
    fields alone. pages_received is kept only when it is at most the record's page count - the
    count a covering letter states cannot exceed the pages on file. Returns the names it filled
    (never the values: they come from the patient's record).
    """
    filled = []
    for field in HEADER_LETTER_FIELDS:
        found = header.get(field) or ""
        current = getattr(document, field)
        if not found or (current is not None and str(current).strip()):
            continue
        if field == "pages_received":
            if int(found) > (document.page_count or 0):
                continue
            setattr(document, field, int(found))
        else:
            setattr(document, field, found)
        filled.append(field)
    return filled
