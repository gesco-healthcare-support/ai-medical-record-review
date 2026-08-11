"""Byte-budgeted overlapping windows for sliding-window segmentation.

Windows are packed to a raw-byte budget because Vertex inline requests cap at ~20 MB
after base64 and page byte-density varies ~60-260 KB/page - a fixed page count cannot
bound request size (measured live: dense 100-page chunks reached 24 MB). Windows
overlap so the next window has real left-context across each seam; the segmentation
engine's ownership rule then guarantees no document is severed at a window edge.

Ported from experiments/a1-segmentation/src/solutions.py, validated live across the
2026-07-04 bake-off and diagnosis runs.
"""

import io

from pypdf import PdfReader, PdfWriter


def page_raw_sizes(pdf_path, n):
    """Per-page byte size of each page written as its own single-page PDF.

    A multi-page window's real size is slightly LESS than the sum of these (per-page
    structural overhead is not shared), so greedy packing against a budget stays
    conservatively under the inline request cap.
    """
    reader = PdfReader(pdf_path)
    sizes = []
    for p in range(n):
        writer = PdfWriter()
        writer.add_page(reader.pages[p])
        buffer = io.BytesIO()
        writer.write(buffer)
        sizes.append(buffer.getbuffer().nbytes)
    return sizes


def next_window_start(s, e, overlap):
    """Start of the window after (s, e): the overlap is CAPPED at a third of the window
    so the step never collapses. With a fixed overlap, a dense region packs windows of
    ~30-45 pages and step = window - overlap degenerates to a 2-4 page crawl: the same
    pages get re-judged by many windows and temperature-0 variance accumulates false
    splits (measured live on Case 2, 2026-07-04)."""
    effective = min(overlap, max(1, (e - s + 1) // 3))
    return max(s + 1, e - effective + 1)


def byte_budgeted_windows(pdf_path, n, overlap, budget_bytes):
    """Overlapping windows packed to `budget_bytes` raw. Returns [(start, end)] 1-based.

    Fails fast if a single page exceeds the budget (that file would need a bigger
    budget or GCS routing - never a silent truncation).
    """
    if overlap < 1:
        raise ValueError(f"overlap must be >= 1 (got {overlap})")
    sizes = page_raw_sizes(pdf_path, n)
    windows, s = [], 1
    while True:
        if sizes[s - 1] > budget_bytes:
            raise RuntimeError(
                f"page {s} is {sizes[s - 1] / 1048576:.1f} MB raw, larger than the "
                f"{budget_bytes / 1048576:.1f} MB window budget; raise WINDOW_BUDGET_MB"
            )
        e, acc = s, sizes[s - 1]
        while e < n and acc + sizes[e] <= budget_bytes:
            acc += sizes[e]
            e += 1
        windows.append((s, e))
        if e == n:
            return windows
        s = next_window_start(s, e, overlap)
