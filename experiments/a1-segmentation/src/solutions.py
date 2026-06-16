"""The four candidate segmenters for the Phase 1 bake-off.

Each takes (pdf_path, n_pages, Cost) and returns a list of (start, end) document spans, with
all Gemini usage recorded on the Cost accountant (cost objective = total tokens). None of
these run on the current free-tier Gemini key; the bake-off runs once a paid path exists.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracles
from pipeline import starts_to_spans


def _spans(starts, n):
    return starts_to_spans(sorted(set(starts)), n)


def sol1_overlapping_windows(pdf_path, n, cost, window=80, overlap=30):
    """Window oracle + seam reconciliation: overlapping windows, trust each window's interior
    and let the next window own the overlap zone (so a doc straddling a seam is never severed).
    """
    starts = {1}
    s = 1
    while s <= n:
        e = min(s + window - 1, n)
        last = e == n
        cutoff = e if last else e - overlap
        for a, _b in oracles.window_segment(pdf_path, s, e, cost):
            if 1 < a <= cutoff:
                starts.add(a)
        if last:
            break
        s = e - overlap + 1
    return _spans(starts, n)


def sol2_adjacent_image(pdf_path, n, cost, dpi=150):
    """Per-page NEW/SAME on page images (root-cause PSS; gaps/overlaps impossible)."""
    starts = [1]
    for p in range(2, n + 1):
        if oracles.adjacent(pdf_path, p, cost, dpi=dpi) == "NEW":
            starts.append(p)
    return _spans(starts, n)


def sol3_adjacent_markdown(pdf_path, n, cost):
    """Per-page NEW/SAME on markitdown markdown text (markitdown mandatory; needs OCR plugin)."""
    import markdown  # lazy: pulls in markitdown only for this solution

    mds = [markdown.page_markdown(pdf_path, p) for p in range(1, n + 1)]
    starts = [1]
    for p in range(2, n + 1):
        if oracles.adjacent_text(mds[p - 2], mds[p - 1], cost) == "NEW":
            starts.append(p)
    return _spans(starts, n)


def sol4_range_probe(pdf_path, n, cost, dpi=150):
    """Range-probe galloping + binary search: for each document, find its end with O(log len)
    probes, then start the next document there. Token-cheapest if the oracle is reliable
    (gated on the 0b error rate; near-boundary voting is the robustness lever to add there).
    """
    starts = [1]
    s = 1
    while s < n:
        # Galloping: probe s+1, s+2, s+4, ... until the first NEW_DOC.
        last_yes, first_no, delta, cand = s, None, 1, s + 1
        while cand <= n:
            if oracles.range_probe(pdf_path, s, cand, cost, dpi=dpi) == "NEW_DOC":
                first_no = cand
                break
            last_yes = cand
            delta *= 2
            cand = s + delta
        if first_no is None:
            break  # document runs to the end of the PDF
        # Binary search in (last_yes, first_no) for the exact first page of the next document.
        lo, hi = last_yes, first_no
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if oracles.range_probe(pdf_path, s, mid, cost, dpi=dpi) == "NEW_DOC":
                hi = mid
            else:
                lo = mid
        starts.append(hi)
        s = hi
    return _spans(starts, n)


SOLUTIONS = {
    "1_windows": sol1_overlapping_windows,
    "2_adjacent_image": sol2_adjacent_image,
    "3_adjacent_markdown": sol3_adjacent_markdown,
    "4_range_probe": sol4_range_probe,
}
