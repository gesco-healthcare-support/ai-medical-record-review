"""Boundary verification merge pass: computed suspicion + a two-page check per suspect (ported).

Suspicion is COMPUTED from the rows (the model's self-reported confidence was measured useless);
each suspect boundary gets one cheap two-image "does a new document start here?" check. By default
a refuted boundary becomes a MERGE SUGGESTION (a wrong auto-merge hides a document, the worst error
class). An unverifiable suspect KEEPS its boundary.
"""

import io
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import types
from pdf2image import convert_from_path

from app.config import get_settings
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_image

_VERIFY_SYSTEM = (
    "You review the segmentation of a scanned workers' compensation medical record. "
    "Given two adjacent segments with their metadata and page images, decide whether "
    "the second segment is part of the first document or a separate document."
)

# Rows this short next to another boundary are the measured scatter signature; these
# (plus same-category+date pairs) keep priority when the suspect net hits its cap.
SHORT_ROW_PAGES = 2
# How many of the suspect fragment's own pages to show (fragments are 1-2 pages by selection).
FRAGMENT_PAGE_CAP = 2
# Boundary-page OCR excerpts are clipped so the prompt stays small.
BOUNDARY_TEXT_CHARS = 1200


def _page_image(pdf_path, page, dpi=120):
    """Rasterize one page ONCE; the caller reuses it for both PNG evidence and OCR."""
    return convert_from_path(pdf_path, first_page=page, last_page=page, dpi=dpi)[0]


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _boundary_text(a_last_image, b_first_image):
    """OCR the two boundary pages into a prompt block, or "" when OCR yields nothing.

    Text is enrichment, not a gate: any OCR failure degrades to the image-only oracle.
    """
    try:
        a_tail = extract_text_from_image(a_last_image).strip()[-BOUNDARY_TEXT_CHARS:]
        b_head = extract_text_from_image(b_first_image).strip()[:BOUNDARY_TEXT_CHARS]
    except Exception as exc:
        print(f"verify boundary OCR failed, continuing image-only: {exc}")
        return ""
    if not a_tail and not b_head:
        return ""
    return (
        "\n\nOCR text from the boundary pages (may contain recognition errors).\n"
        "END of document A's last page:\n"
        f"{a_tail or '(no text recognized)'}\n"
        "START of segment B's first page:\n"
        f"{b_head or '(no text recognized)'}\n"
        "A sentence, list, or table cut mid-flow at the end of A and resuming in B, or "
        "pagination such as 'Page 3 of 5' followed by 'Page 4 of 5', is strong "
        "continuation evidence."
    )


def _row_line(name, row):
    return (
        f"{name}: pages {row['start']}-{row['end']}, category {row['category']}, "
        f"date {row['date']}, title: {row.get('title') or '-'}"
    )


def _same_document(pdf_path, prev_row, row):
    """Rich yes/no check: is `row` a continuation of `prev_row`'s document? Any failure resolves
    to False (keep the boundary): merging on missing evidence would hide a document."""
    settings = get_settings()
    fragment_end = min(row["end"], row["start"] + FRAGMENT_PAGE_CAP - 1)
    try:
        a_last = _page_image(pdf_path, prev_row["end"])
        fragment_images = [
            _page_image(pdf_path, page) for page in range(row["start"], fragment_end + 1)
        ]
        prompt = (
            _row_line("Document A", prev_row)
            + "\n"
            + _row_line("Segment B", row)
            + "\n\n"
            + "The first image is the LAST page of document A. The following image(s) are "
            f"segment B (pages {row['start']}-{fragment_end}). Segment B may be the "
            "continuation of document A - remaining report pages, attachments, lab tables, "
            "signature/stamp/certification pages, terms or branding pages, or blank "
            "separator pages - OR it may begin a separate document.\n"
            "Answer YES only when the evidence clearly shows continuation: continued "
            "pagination, a sentence or table that flows across the boundary, the same "
            "author and visit continuing, or an attachment the report explicitly "
            "references. Sharing a document type, date, or letterhead is NOT enough - "
            "these records routinely contain same-day batches of separate short "
            "documents. If the evidence is unclear, answer NO."
        )
        if settings.verify_use_text:
            prompt += _boundary_text(a_last, fragment_images[0])
        contents = [prompt, types.Part.from_bytes(data=_png_bytes(a_last), mime_type="image/png")]
        for image in fragment_images:
            contents.append(types.Part.from_bytes(data=_png_bytes(image), mime_type="image/png"))
        config = types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="text/x.enum",
            response_schema={"type": "STRING", "enum": ["YES", "NO"]},
            system_instruction=_VERIFY_SYSTEM,
        )
        response = generate_with_retry(
            get_genai_client(), model=settings.verify_model, contents=contents, config=config
        )
    except Exception as exc:
        print(f"verify oracle failed at page {row['start']}: {exc}")
        return False
    return (response.text or "").strip() == "YES"


def suspect_indices(rows, cap=None):
    """Row indices worth one verification call, ascending, at most ``cap`` of them (wide net:
    every adjacent boundary is a candidate; measured triggers keep priority at the cap)."""
    if cap is None:
        cap = get_settings().verify_suspect_cap
    triggered, rest = [], []
    for i in range(1, len(rows)):
        row, prev = rows[i], rows[i - 1]
        same_cat_date = (
            row["category"] == prev["category"]
            and row["date"] == prev["date"]
            and row["date"] not in ("", "-")
        )
        short = (row["end"] - row["start"] + 1) <= SHORT_ROW_PAGES
        (triggered if same_cat_date or short else rest).append(i)
    return sorted((triggered + rest)[: max(cap, 0)])


def verify_and_merge(pdf_path, rows, progress=None, workers=None, auto=False):
    """Verify suspect boundaries; refuted ones become MERGE SUGGESTIONS by default (auto=False).
    Returns (rows, stats). Merging preserves tiling."""
    if workers is None:
        workers = get_settings().classify_workers

    def report(current, total):
        if progress is not None:
            progress("verifying", current, total)

    suspects = suspect_indices(rows)
    report(0, len(suspects))
    same_doc = {}
    if suspects:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_same_document, pdf_path, rows[i - 1], rows[i]): i for i in suspects
            }
            for done, future in enumerate(as_completed(futures), start=1):
                same_doc[rows[futures[future]]["start"]] = future.result()
                report(done, len(suspects))

    out, affected = [], 0
    for row in rows:
        refuted = bool(out) and same_doc.get(row["start"]) is True
        if refuted and auto:
            previous = out[-1]
            previous["end"] = row["end"]
            if str(row["flag"]).strip().lower() == "x":
                previous["flag"] = "x"
            affected += 1
            continue
        row = dict(row)
        if refuted:
            row["suggest_merge"] = True
            affected += 1
        out.append(row)
    key = "merged_away" if auto else "suggested"
    return out, {"suspects": len(suspects), key: affected}
