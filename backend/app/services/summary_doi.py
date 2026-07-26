"""Per-sub-document date-of-injury extraction (isolated vision) + a summary DOI-prefix helper.

The segmentation model propagates the claim's DOI onto documents that never state one, because it
reads a whole window (many documents) at once. This module reads ONE sub-document in ISOLATION -
only its own pages - so there are no neighbours to copy a DOI from, and it reads the date from the
PDF image via Gemini vision, not lossy OCR. summarize_row uses it to decide a summary's **DOI**
prefix; the backfill reuses it to correct already-stored summaries.
"""

import io
import logging
import re

from google.genai import types
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry

logger = logging.getLogger(__name__)

# A stated DOI sits on a document's first pages (a form field or report header), so the isolated
# call sends at most this many pages - bounding the payload on long QME/deposition sub-documents.
_MAX_PAGES = 5

_ISOLATION_PROMPT = (
    "The attached pages are ONE medical document. Does THIS document itself state the patient's "
    "DATE OF INJURY? Report it as MM/DD/YYYY ONLY if it is explicitly written in THIS document "
    "(e.g. a 'Date of Injury' or 'DOI' field). Do NOT infer it, and never use a date of exam, "
    "visit, service, report, birth, or signature. If the document states more than one injury "
    "date, list them comma-separated. If this document does not state an injury date, answer "
    "exactly '-'. Answer with ONLY the date(s) or '-'."
)

_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
# A leading "**DOI**:<date-list>, " prefix as summarize_row builds it (single or comma-joined).
_DOI_PREFIX = re.compile(r"^\s*\*\*DOI\*\*:\s*\d[\d/.\-]*(?:\s*,\s*\d[\d/.\-]*)*\s*,\s*")


def _clean(reply: str) -> str:
    """The MM/DD/YYYY dates found in a model reply (zero-padded, de-duped, in order); '-' if none."""
    seen: list[str] = []
    for match in _DATE.finditer(reply or ""):
        token = f"{int(match.group(1)):02d}/{int(match.group(2)):02d}/{match.group(3)}"
        if token not in seen:
            seen.append(token)
    return ", ".join(seen) if seen else "-"


def extract_injury_date(pdf_path, start, end, model=None) -> str:
    """The injury date THIS sub-document (pages ``start``..``end``) states, or ``"-"``.

    Isolated + vision: sends only this document's first pages as a PDF Part, so the model cannot
    copy a DOI from neighbouring documents and reads it from the image, not OCR. Fail-safe: returns
    ``"-"`` on any error or empty reply - a wrong/propagated DOI is worse than none.
    """
    settings = get_settings()
    model = model or settings.genai_model
    try:
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        last = min(int(end), int(start) + _MAX_PAGES - 1)
        for page in range(int(start) - 1, last):
            writer.add_page(reader.pages[page])
        buffer = io.BytesIO()
        writer.write(buffer)
        part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="application/pdf")
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=[part, _ISOLATION_PROMPT],
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=200),
        )
        return _clean(response.text or "")
    except Exception:
        logger.warning("isolated DOI extraction failed for pages %s-%s", start, end, exc_info=True)
        return "-"


def apply_doi_prefix(body, injury):
    """Rewrite ``body`` so its leading ``**DOI**:`` prefix reflects ``injury`` (none when ``"-"``).

    Strips any existing leading ``**DOI**:<dates>,`` prefix and, when ``injury`` is a real date,
    prepends ``**DOI**:<injury>, ``. Non-DOI content is unchanged; a None/empty body is returned
    as-is. Used by the backfill to correct already-stored summaries.
    """
    if not body:
        return body
    stripped = _DOI_PREFIX.sub("", body, count=1)
    if injury and injury != "-":
        return f"**DOI**:{injury}, {stripped}"
    return stripped
