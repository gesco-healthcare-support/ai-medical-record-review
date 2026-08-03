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

# A stated DOI sits on a document's first pages (a form field or report header), so the isolated call
# sends at most this many pages - bounding the payload on long QME/deposition sub-documents.
#
# Raised 5 -> 10 on measurement (2026-07-31): the old bound was the single largest cause of a missed
# DOI. On rows whose DOI label is followed by a digit, capture was 83.5% (n=79) for spans of 1-5 pages
# against 59.5% (n=37) for 6 pages and over - past page 5 the field simply was not in the payload.
# 85.9% of summarized sub-documents are 1-5 pages, so only 14.1% send a larger payload at all, and 10
# pages covers 96.2% of sub-documents in full.
_MAX_PAGES = 10

_ISOLATION_PROMPT = (
    "The attached pages are ONE medical document. Does THIS document itself state the patient's "
    "DATE OF INJURY? Report it as MM/DD/YY ONLY if it is explicitly written in THIS document "
    "(e.g. a 'Date of Injury' or 'DOI' field). If the document states a CUMULATIVE TRAUMA period "
    "instead of a single date, report it as CT MM/DD/YY-MM/DD/YY using the exact dates written. "
    "Do NOT infer either, and never use a date of exam, visit, service, report, birth, or "
    "signature. If the document states more than one injury date or period, separate them with "
    "' & '. If this document does not state an injury date or period, answer exactly '-'. "
    "Answer with ONLY the date(s) or '-'."
)

# House grammar, measured across 813 human-written summary entries: two-digit years, a cumulative
# trauma period written "CT MM/DD/YY-MM/DD/YY" (90 instances), and several dates joined with " & "
# (6 instances; no comma-joined instance exists in the corpus).
_D = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
# A RANGE is only recognised between slash/dot-separated dates: allowing "-" inside the dates too
# would make "01-02-20" (one date) read as a range.
_DS = r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
# Ranges must be tried BEFORE single dates, or a range parses as two unrelated injury dates.
#
# The CT group admits an optional colon and internal dots because `CT\s*` alone silently dropped the
# marker: a source reading "Date of injury: CT: 11/30/2015 - 12/04/2025" degraded to a bare date
# range, losing the fact that it is a cumulative-trauma PERIOD rather than a single injury - a
# correctness bug in a medical-legal field. `\b` anchors it so a bare "C" or "T" cannot match, and so
# that the letters inside a word cannot either: "IMPACT 11/30/2015 - 12/04/2025" previously came out
# marked "CT", inventing the classification this module exists to avoid inventing.
_ITEM = re.compile(
    rf"(?P<ct>\bC\.?T\.?\s*:?\s*)?(?P<from>{_DS})\s*(?:-|--|to|through)\s*(?P<to>{_DS})|(?P<one>{_D})",
    re.IGNORECASE,
)

# The same grammar as a stored-prefix pattern: "**DOI**: <value>." where <value> is one item or
# several joined by " & ". Mirrored in the frontend's parseDisplay
# (frontend/components/review/summaries-view.tsx).
_ITEM_G = rf"(?:CT\s*)?{_DS}\s*-\s*{_DS}|{_D}"
_VALUE_G = rf"(?:{_ITEM_G})(?:\s*&\s*(?:{_ITEM_G}))*"
_DOI_PREFIX_NEW = re.compile(rf"^\s*\*\*DOI\*\*:\s*({_VALUE_G})\s*\.\s*", re.IGNORECASE)
# The pre-2026-07-29 grammar: a comma-joined date list terminated by a comma. 709 stored summaries
# carry it, so it stays READABLE (parsed here, and by the frontend) even though nothing emits it any
# more - rewriting them on read would be a silent data change, and refusing to parse them would drop
# their DOI out of the chip and out of export restoration.
_DOI_PREFIX_LEGACY = re.compile(r"^\s*(\*\*DOI\*\*:\s*\d[\d/.\-]*(?:\s*,\s*\d[\d/.\-]*)*)\s*,\s*")


def _yy(token: str) -> str:
    """One date token -> zero-padded MM/DD/YY (the house format)."""
    month, day, year = re.split(r"[/.\-]", token)
    return f"{int(month):02d}/{int(day):02d}/{year[-2:]}"


def _clean(reply: str) -> str:
    """The injury date(s) a model reply states, in house format; '-' when it states none.

    Single dates become MM/DD/YY; a cumulative-trauma period stays ONE item
    ("CT MM/DD/YY-MM/DD/YY"); several items are joined with " & ". De-duped, order preserved. The
    CT marker is only emitted when the reply carried it - a bare range is kept as a range rather
    than being relabelled, since inventing the classification is exactly what this module exists to
    avoid.
    """
    items: list[str] = []
    for match in _ITEM.finditer(reply or ""):
        if match.group("from"):
            item = f"{_yy(match.group('from'))}-{_yy(match.group('to'))}"
            if match.group("ct"):
                item = f"CT {item}"
        else:
            item = _yy(match.group("one"))
        if item not in items:
            items.append(item)
    return " & ".join(items) if items else "-"


def extract_injury_date(pdf_path, start, end, model=None, strict=False) -> str:
    """The injury date THIS sub-document (pages ``start``..``end``) states, or ``"-"``.

    Isolated + vision: sends only this document's first pages as a PDF Part, so the model cannot
    copy a DOI from neighbouring documents and reads it from the image, not OCR. Fail-safe: returns
    ``"-"`` on any error or empty reply - at write time a wrong/propagated DOI is worse than none.

    ``strict=True`` re-raises instead, for callers that REWRITE stored summaries: there, "-" means
    "delete this summary's DOI", so a read failure (expired credentials, a moved file, a quota
    error) must not be mistaken for "this document states no injury date".
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
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=200,
                # summarize_row passes ITS model here, which is summary_model - a thinking model
                # (2.5-pro) that rejects the retry seam's default thinking_budget of 0 with a 400.
                # This function is fail-safe, so that rejection was silent and EVERY document
                # reported "no stated injury date". Set the budget explicitly, as the summary call
                # does.
                thinking_config=types.ThinkingConfig(
                    thinking_budget=get_settings().summary_thinking_budget
                ),
            ),
        )
        return _clean(response.text or "")
    except Exception:
        logger.warning("isolated DOI extraction failed for pages %s-%s", start, end, exc_info=True)
        if strict:
            raise
        return "-"


def doi_prefix(body) -> str:
    """The leading ``**DOI**:`` prefix of a stored summary body, or ``""`` when it has none.

    Returns the prefix ready to prepend, in WHATEVER grammar the body was stored with: the house
    form ``**DOI**: <value>.`` or the legacy ``**DOI**:<dates>,``. Export re-applies exactly this
    string to a body whose prefix an edit stripped, so handing back a rewritten form would change a
    stored summary's text as a side effect of exporting it.

    This is the single place that knows the grammar, so callers cannot re-derive it with a pattern
    that stops at the first separator and silently drops a second stated date.
    """
    match = _DOI_PREFIX_NEW.match(body or "")
    if match:
        return f"**DOI**: {match.group(1)}."
    match = _DOI_PREFIX_LEGACY.match(body or "")
    return f"{match.group(1)}," if match else ""


def apply_doi_prefix(body, injury):
    """Rewrite ``body`` so its leading ``**DOI**:`` prefix reflects ``injury`` (none when ``"-"``).

    Strips an existing prefix in EITHER grammar and, when ``injury`` is a real date or period,
    prepends the house form ``**DOI**: <injury>. `` - so a rewrite (the backfill) also upgrades a
    legacy prefix. Non-DOI content is unchanged; a None/empty body is returned as-is.
    """
    if not body:
        return body
    stripped = _DOI_PREFIX_NEW.sub("", body, count=1)
    if stripped == body:
        stripped = _DOI_PREFIX_LEGACY.sub("", body, count=1)
    if injury and injury != "-":
        return f"**DOI**: {injury}. {stripped}"
    return stripped
