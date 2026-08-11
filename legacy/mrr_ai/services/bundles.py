"""Category-filtered document bundles (Diagnostic & Operative, Depositions, ...).

Pull the review rows whose category is in a requested set, then either concatenate their
source pages into one PDF (no LLM) or summarize just those records into a filtered report.
Both are per-document and stay in memory - no ~/MRRs artifacts (HIPAA); ids-only logging
lives in the caller. Category sets are the only difference between the two product pages,
so the logic is parameterized here rather than duplicated.
"""

import io

from pypdf import PdfReader, PdfWriter

from mrr_ai.services import summarize_engine


def matched_rows(rows, categories):
    """Rows whose category is in ``categories`` (int/str mix ok), original order kept."""
    wanted = {str(c) for c in categories}
    return [row for row in rows if str(row["category"]) in wanted]


def pages_for_rows(rows):
    """The 1-indexed pages covered by ``rows``, in row order (ranges are inclusive)."""
    pages = []
    for row in rows:
        pages.extend(range(int(row["start"]), int(row["end"]) + 1))
    return pages


def build_bundle_pdf(pdf_path, rows):
    """Concatenate the pages of ``rows`` from ``pdf_path`` into an in-memory PDF buffer.

    Out-of-range pages are skipped rather than raising: one bad row must not sink the
    whole bundle (rows are validated on save, but the source file may be shorter).
    """
    reader = PdfReader(pdf_path)
    last = len(reader.pages)
    writer = PdfWriter()
    for page in pages_for_rows(rows):
        if 1 <= page <= last:
            writer.add_page(reader.pages[page - 1])
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def bundle_summary_entries(pdf_path, rows, model=None, prompt_for=None):
    """Summarize each row with its category prompt -> Word-export entry dicts.

    Fresh on demand (works even if the document was never summarized in full); reuses the
    per-row engine, so each record gets its category-specific prompt exactly as the main
    flow would. ``prompt_for`` is an optional ``row -> prompt text`` resolver injected by the
    blueprint (DB-first via ``catalog.get_prompt``); when omitted the engine uses its
    hardcoded default, keeping this service Flask/DB-free.
    """
    entries = []
    for row in rows:
        prompt = prompt_for(row) if prompt_for is not None else None
        output = summarize_engine.summarize_row(pdf_path, row, model, prompt=prompt)
        entries.append(
            {
                "summaryDate": output.get("summaryDate") or "-",
                "summaryTitle": output["summaryTitle"],
                "summaryText": output["summaryText"],
            }
        )
    return entries
