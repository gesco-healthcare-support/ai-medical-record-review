"""Merge multiple record PDFs into one + compute each source's page range (individual-records).

Replaces the classic reports.py:compute_page_ranges + the individual-MRR folder upload. The
uploaded files are pre-split records; merging them into one PDF lets the individual-record flow
reuse the whole Document/ReviewRow pipeline (one ReviewRow per record, keyed by its page range).
Flask-free (pypdf); the caller owns storage + the DB rows.
"""

import io

from pypdf import PdfReader, PdfWriter


def merge_pdfs(sources: list[tuple[str, bytes]]) -> tuple[bytes, list[dict]]:
    """Merge ``[(filename, pdf_bytes)]`` into one PDF, in order.

    Returns (merged_bytes, records) where records is ``[{filename, start, end, pages}]`` (1-based,
    inclusive) for the files that were readable. Unreadable files are skipped rather than raising,
    so one bad upload does not sink the whole case.
    """
    writer = PdfWriter()
    records: list[dict] = []
    current = 1
    for filename, data in sources:
        try:
            reader = PdfReader(io.BytesIO(data))
            num_pages = len(reader.pages)
        except Exception:
            num_pages = 0
        if num_pages <= 0:
            continue
        for page in reader.pages:
            writer.add_page(page)
        records.append(
            {
                "filename": filename,
                "start": current,
                "end": current + num_pages - 1,
                "pages": num_pages,
            }
        )
        current += num_pages

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), records
