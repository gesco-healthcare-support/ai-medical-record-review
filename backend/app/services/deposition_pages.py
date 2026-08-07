"""Discover a deposition transcript's OWN printed page numbers, once per sub-document.

There are two page numberings in play and they do not match. A deposition transcript prints its own
page numbers (with numbered lines down the margin) and that is what a lawyer cites and what the human
summarizers write - "On page 5, lines 13 to 14". Our OCR markers count pages of the whole scanned
record, so a deposition sitting at record pages 418-460 is transcript pages 1-43. Printing the marker
number would look like a transcript page and be wrong, which is why the category-9 prompt used to
forbid page numbers outright.

Rather than ask the model to read a printed corner number off every page - unreliable on OCR, and
impossible past ``summary_image_max_pages`` where it cannot see the page at all - discover the OFFSET
once and relabel the markers. The prompt then just cites the numbers it is given: no arithmetic, no
per-page reading, and the contradiction disappears.

Mirrors ``services/summary_doi``: one isolated vision read over a sub-document's first pages, bounded
payload, fail-safe on every error. Fail-safe matters more here than usual - a WRONG citation is worse
than none, because a reviewer trusts it and then cannot find the testimony.
"""

import io
import json
import logging

from google.genai import types
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry

logger = logging.getLogger(__name__)

# A transcript's printed number sits on its first pages, and the same bound as the DOI read keeps the
# payload small. Also stays inside summary_image_max_pages (15), so every page asked about is one the
# model can actually see rather than one it would have to guess from OCR.
_MAX_PAGES = 6

# At least this many pages must agree on one offset. Two is cheap evidence that the transcript is
# sequentially paginated from where we think it starts - a cover page, an index, or exhibits inserted
# mid-transcript all break a constant offset, and one page alone cannot tell us that happened.
_MIN_AGREEING = 2

_PROMPT = (
    "The attached pages are consecutive pages from ONE deposition or hearing transcript. Each page "
    "usually carries its OWN printed page number, near a corner or in a header or footer, separate "
    "from any line numbers running down the margin.\n\n"
    "For EACH attached page, in order, report the printed page number you can SEE on that page.\n\n"
    "Rules:\n"
    "- Report the number printed ON the page. Do NOT count the attached pages yourself, and do not "
    "infer or continue a sequence.\n"
    "- A transcript page number is a plain number, usually 1 to 4 digits. Ignore line numbers (the "
    "column of numbers down the left margin), exhibit numbers, dates, case numbers, and any "
    '"Page X of Y" where X is a fax or cover-sheet count.\n'
    "- If a page shows no printed page number - a cover sheet, an appearance page, an exhibit - "
    "report 0 for it.\n\n"
    'Answer as JSON: {"pages": [{"i": <1-based position among the attached pages>, '
    '"printed": <the printed number, or 0>}]}'
)

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "pages": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "i": {
                        "type": "INTEGER",
                        "description": "1-based position among attached pages",
                    },
                    "printed": {"type": "INTEGER", "description": "Printed page number, or 0"},
                },
                "required": ["i", "printed"],
            },
        }
    },
    "required": ["pages"],
}


def transcript_page_offset(pdf_path, start, end, model=None) -> int | None:
    """The offset to ADD to a record page number to get this transcript's printed page number.

    ``transcript_page = record_page + offset``. Returns None when it cannot be established, which the
    caller must treat as "produce no page citations" rather than falling back to record numbers - a
    citation that looks like a transcript page but is not one is the failure this exists to prevent.

    Requires ``_MIN_AGREEING`` pages to agree on the same offset. Never raises.
    """
    settings = get_settings()
    model = model or settings.genai_model
    try:
        start, end = int(start), int(end)
        reader = PdfReader(pdf_path)
        last = min(end, start + _MAX_PAGES - 1)
        writer = PdfWriter()
        for page in range(start - 1, last):
            writer.add_page(reader.pages[page])
        buffer = io.BytesIO()
        writer.write(buffer)
        part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="application/pdf")
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=[part, _PROMPT],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=400,
                response_mime_type="application/json",
                response_schema=_SCHEMA,
                # Explicit, as summary_doi does: the retry seam defaults thinking_budget to 0, which a
                # thinking model rejects with a 400 - and because this function is fail-safe, that
                # rejection would be SILENT and every transcript would lose its citations.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=settings.summary_thinking_budget
                ),
            ),
        )
        return _offset_from(json.loads((response.text or "").strip()), start, last)
    except Exception:
        logger.warning(
            "transcript page-number read failed for pages %s-%s; no page citations", start, end
        )
        return None


def _offset_from(data, start, last) -> int | None:
    """The single offset that ``_MIN_AGREEING`` or more pages agree on, or None.

    Split out from the model call so the agreement rule is testable without a network round trip.
    """
    counts: dict[int, int] = {}
    for entry in (data or {}).get("pages") or []:
        try:
            position = int(entry["i"])
            printed = int(entry["printed"])
        except (KeyError, TypeError, ValueError):
            continue
        record_page = start + position - 1
        # printed 0 means "this page shows no number" (a cover or appearance page), which is
        # information but not an offset. A negative offset would mean the printed number is lower than
        # the record page, which is the normal case; only a nonsensical one is dropped.
        if printed <= 0 or not (start <= record_page <= last):
            continue
        counts[printed - record_page] = counts.get(printed - record_page, 0) + 1
    if not counts:
        return None
    offset, agreeing = max(counts.items(), key=lambda kv: kv[1])
    if agreeing < _MIN_AGREEING:
        logger.info(
            "transcript page numbers did not agree on one offset (best %s seen %d time(s)); "
            "no page citations",
            offset,
            agreeing,
        )
        return None
    return offset
