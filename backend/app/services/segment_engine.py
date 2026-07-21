"""Sliding-window segmentation engine: scanned PDF -> categorized sub-document rows (ported).

Windows are byte-budgeted and OVERLAP; each seam page is decided by the window that saw the page
before it (ownership), so no document is cut at a window edge. Output rows always TILE the PDF.
This is the segment worker's core; it runs on the P4 `segment` (torch/classifier) worker tier.
"""

import io
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import types
from pypdf import PdfReader, PdfWriter

from app.config import get_settings
from app.services.classification import classify
from app.services.gemini import (
    SEGMENT_RESPONSE_SCHEMA,
    SEGMENTATION_PROMPT,
    SEGMENTATION_SYSTEM,
    parse_segment_item,
)
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.verify_pass import verify_and_merge
from app.services.windows import byte_budgeted_windows


def _generation_config():
    # Segmentation keeps thinking (segment_thinking_budget, default dynamic): an A/B on labeled
    # cases showed thinking-off regresses strict doc-F1 here, unlike the other structured calls.
    return types.GenerateContentConfig(
        temperature=0.0,
        top_p=0.95,
        top_k=40,
        response_mime_type="application/json",
        response_schema=SEGMENT_RESPONSE_SCHEMA,
        system_instruction=SEGMENTATION_SYSTEM,
        thinking_config=types.ThinkingConfig(
            thinking_budget=get_settings().segment_thinking_budget
        ),
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
        model=get_settings().genai_model,
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
    """Ownership merge: window k owns starts in (ws_k, ws_{k+1}]; the last window owns through the
    end. Metadata comes from the owning window's row. Ends are re-derived so rows tile."""
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
    """B5 cascade on the title, escalating to first-page OCR when inconclusive; any low-confidence
    result routes the row to human review via the flag."""
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

    progress(stage, current, total) is called around every model interaction; it must never raise.
    """
    settings = get_settings()
    client = get_genai_client()

    def report(stage, current, total):
        if progress is not None:
            progress(stage, current, total)

    windows = byte_budgeted_windows(
        pdf_path,
        total_pages,
        settings.window_overlap,
        int(settings.window_budget_mb * 1024 * 1024),
    )
    # Windows are independent (each builds its own sub-PDF and calls the model), so run them on a
    # small pool - the seam's rate limiter caps the aggregate request rate. Results are placed by
    # window index so the downstream ownership merge still sees them in order.
    report("segmenting", 0, len(windows))
    reports = [None] * len(windows)
    with ThreadPoolExecutor(max_workers=settings.segment_window_workers) as pool:
        futures = {
            pool.submit(_window_rows, pdf_path, ws, we, client): k
            for k, (ws, we) in enumerate(windows)
        }
        for done, future in enumerate(as_completed(futures), start=1):
            reports[futures[future]] = future.result()  # fail loudly; never drop a window silently
            report("segmenting", done, len(windows))

    rows = merge_window_rows(reports, windows, total_pages)

    # Rows are independent, so categorize on a small pool. Each worker owns its row (no shared
    # mutation) and classify() opens its own short session for catalog reads (thread-safe).
    report("categorizing", 0, len(rows))
    with ThreadPoolExecutor(max_workers=settings.classify_workers) as pool:
        futures = [pool.submit(_categorize, pdf_path, row) for row in rows]
        for i, future in enumerate(as_completed(futures), start=1):
            future.result()  # a worker failure must fail the job loudly, not vanish
            report("categorizing", i, len(rows))

    if settings.verify_merge:
        rows, stats = verify_and_merge(pdf_path, rows, progress=progress)
        print(f"verify pass: {stats}")
    return rows
