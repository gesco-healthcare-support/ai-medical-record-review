"""Sliding-window segmentation engine: scanned PDF -> categorized sub-document rows.

Replaces the fixed 100-page chunking (which severed every document straddling a chunk
edge and could not ship dense chunks inline on Vertex). Windows are byte-budgeted and
OVERLAP; each seam page is decided by the window that saw the page before it
(ownership), so no document is cut at a window edge. Validated live 2026-07-04/05:
severed docs 16 -> 0 across 8 labeled cases, tied Google's paid splitter on accuracy.

Output rows always TILE the PDF (each end = next start - 1, last end = page count):
the row list drives page slicing downstream, and tiling by construction removes the
silent lost-pages defect found in diagnosis.
"""

import io
import json

from google.genai import types
from pypdf import PdfReader, PdfWriter

from mrr_ai.config import GENAI_MODEL, WINDOW_BUDGET_MB, WINDOW_OVERLAP
from mrr_ai.extensions import genai_client
from mrr_ai.services.classification import classify
from mrr_ai.services.gemini import (
    SEGMENT_RESPONSE_SCHEMA,
    SEGMENTATION_PROMPT,
    SEGMENTATION_SYSTEM,
    parse_segment_item,
)
from mrr_ai.services.genai_retry import generate_with_retry
from mrr_ai.services.ocr import extract_text_from_selected_pages
from mrr_ai.services.windows import byte_budgeted_windows


def _generation_config():
    return types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        top_k=40,
        response_mime_type="application/json",
        response_schema=SEGMENT_RESPONSE_SCHEMA,
        system_instruction=SEGMENTATION_SYSTEM,
    )


def _window_rows(pdf_path, window_start, window_end, client):
    """Segment pages [window_start, window_end] in one inline call; absolute-page rows."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for p in range(window_start - 1, window_end):
        writer.add_page(reader.pages[p])
    buffer = io.BytesIO()
    writer.write(buffer)
    part = types.Part.from_bytes(data=buffer.getvalue(), mime_type="application/pdf")

    response = generate_with_retry(
        client,
        model=GENAI_MODEL,
        contents=[part, SEGMENTATION_PROMPT],
        config=_generation_config(),
    )
    clean = (response.text or "").replace("```json", "").replace("```", "").strip()
    rows = []
    for item in json.loads(clean):
        try:
            s, e, title, date, injury, manual = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue  # one malformed element must not abort the window
        rows.append(
            dict(
                start=s + window_start - 1,
                end=e + window_start - 1,
                title=title,
                date=date,
                injury_date=injury,
                flag=manual,
            )
        )
    return sorted(rows, key=lambda r: (r["start"], r["end"]))


def merge_window_rows(window_reports, windows, total_pages):
    """Ownership merge: window k owns starts in (ws_k, ws_{k+1}]; the last window owns
    through the end. A window always reports its own first page as a start - an artifact
    (it has no left-context there), so rows starting AT a later window's first page are
    dropped and that page is decided by the previous window, which saw the page before it.
    Metadata comes from the owning window's row. Ends are re-derived so rows tile."""
    surviving = []
    for k, rows in enumerate(window_reports):
        ws = windows[k][0]
        owned_cap = total_pages if k == len(windows) - 1 else windows[k + 1][0]
        floor = 0 if k == 0 else ws  # window 0 keeps its first-page row (absolute page 1)
        surviving.extend(r for r in rows if floor < r["start"] <= owned_cap)

    surviving.sort(key=lambda r: r["start"])
    deduped = []
    for row in surviving:
        if deduped and row["start"] == deduped[-1]["start"]:
            continue  # same start seen by two windows: the earlier (owning) row wins
        deduped.append(dict(row))

    if not deduped or deduped[0]["start"] > 1:
        # Never leave front pages uncovered; an explicit low-confidence row is honest.
        deduped.insert(0, dict(start=1, end=1, title="-", date="-", injury_date="-", flag="x"))
    for i, row in enumerate(deduped):
        row["end"] = (deduped[i + 1]["start"] - 1) if i + 1 < len(deduped) else total_pages
    return deduped


def _categorize(pdf_path, row):
    """B5 cascade on the title, escalating to first-page OCR when inconclusive; any
    low-confidence result routes the row to human review via the flag."""
    result = classify(row["title"])
    if result.needs_review:
        try:
            page_text = extract_text_from_selected_pages(pdf_path, [row["start"]])
            if page_text.strip():
                result = classify(row["title"], page_text=page_text)
        except Exception as exc:
            print(f"Classification escalation OCR failed: {exc}")
    row["category"] = result.category
    if result.needs_review or row["flag"].strip().lower() == "x":
        row["flag"] = "x"
    return row


def run_segmentation(pdf_path, total_pages, progress=None):
    """PDF -> tiled, categorized sub-document rows, reporting progress per stage.

    progress(stage, current, total) is called around every model interaction so a UI
    can show real movement during the minutes-long run; it must never raise.
    """

    def report(stage, current, total):
        if progress is not None:
            progress(stage, current, total)

    windows = byte_budgeted_windows(
        pdf_path, total_pages, WINDOW_OVERLAP, int(WINDOW_BUDGET_MB * 1024 * 1024)
    )
    reports = []
    for k, (ws, we) in enumerate(windows, start=1):
        report("segmenting", k - 1, len(windows))
        reports.append(_window_rows(pdf_path, ws, we, genai_client))
        report("segmenting", k, len(windows))

    rows = merge_window_rows(reports, windows, total_pages)

    for i, row in enumerate(rows, start=1):
        _categorize(pdf_path, row)
        report("categorizing", i, len(rows))
    return rows
