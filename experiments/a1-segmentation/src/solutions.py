"""The four candidate segmenters for the Phase 1 bake-off.

Each takes (pdf_path, n_pages, Cost) and returns a list of (start, end) document spans, with
all Gemini usage recorded on the Cost accountant (cost objective = total tokens). None of
these run on the current free-tier Gemini key; the bake-off runs once a paid path exists.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genai_client
import oracles
from config import CHUNK_SIZE
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


def _gallop_binary(pdf_path, s, lo_floor, seg_end, cost, dpi):
    """Smallest page in (lo_floor, seg_end] that the range oracle calls NEW_DOC for doc(s), or
    None if doc(s) runs through seg_end. lo_floor is a page already known to belong to doc(s)
    (the doc start, or a page a vetoed false-split confirmed still belongs to doc(s))."""
    last_yes, first_no, delta, cand = lo_floor, None, 1, lo_floor + 1
    while cand <= seg_end:
        if oracles.range_probe(pdf_path, s, cand, cost, dpi=dpi) == "NEW_DOC":
            first_no = cand
            break
        last_yes = cand
        delta *= 2
        cand = lo_floor + delta
    # Galloping can overshoot seg_end without ever testing it; probe the segment's last page so a
    # boundary sitting just below the cap is not silently absorbed into doc(s).
    if first_no is None and last_yes < seg_end:
        if oracles.range_probe(pdf_path, s, seg_end, cost, dpi=dpi) == "NEW_DOC":
            first_no = seg_end
    if first_no is None:
        return None  # document runs through seg_end
    lo, hi = last_yes, first_no
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if oracles.range_probe(pdf_path, s, mid, cost, dpi=dpi) == "NEW_DOC":
            hi = mid
        else:
            lo = mid
    return hi


def _segment_search(pdf_path, seg_start, seg_end, cost, dpi, confirm):
    """Find every document start in [seg_start, seg_end]. seg_start begins a document; seg_end+1
    is a known boundary (a cue pre-cut or the PDF end), so the search never crosses it. With
    confirm, each range-probe boundary is cross-checked by the adjacent oracle before it is
    accepted. Returns the internal starts found (seg_start included)."""
    starts = [seg_start]
    s = seg_start
    while s < seg_end:
        lo_floor, boundary = s, None
        while True:
            hi = _gallop_binary(pdf_path, s, lo_floor, seg_end, cost, dpi)
            if hi is None:
                break  # doc(s) runs through seg_end; seg_end+1 boundary owned by the caller
            if not confirm:
                boundary = hi
                break
            decision = _confirm_boundary(pdf_path, s, hi, seg_start, seg_end, cost, dpi)
            if decision is None:  # veto: hi is a false split -> doc(s) extends past it, keep going
                lo_floor = hi
                continue
            boundary = decision
            break
        if boundary is None:
            break
        starts.append(boundary)
        s = boundary
    return starts


def _confirm_boundary(pdf_path, s, hi, seg_start, seg_end, cost, dpi):
    """Cross-check a range-probe boundary `hi` with the ADJACENT oracle -- a different prompt over
    a different page pair, so its error is largely independent (temperature-0 makes repeating the
    SAME range probe an echo, not a vote; an independent view is what decorrelates). Returns the
    accepted boundary page, or None to VETO it:
    - adjacent NEW at hi             -> keep hi (the two oracles agree);
    - adjacent NEW at hi-1 or hi+1   -> relocate (repairs an off-by-one localization);
    - adjacent SAME all around       -> None (veto: hi is most likely a false split, e.g. a long
                                        report's interior page that merely looks like new
                                        letterhead). Caller resumes the search past hi.
    This only helps when the adjacent oracle is the more reliable of the two -- which Phase 0b
    measures; a corrector as noisy as the primary can do net harm."""
    if oracles.adjacent(pdf_path, hi, cost, dpi=dpi) == "NEW":
        return hi
    for cand in (hi - 1, hi + 1):
        if seg_start < cand <= seg_end and oracles.adjacent(pdf_path, cand, cost, dpi=dpi) == "NEW":
            return cand
    return None


def sol4_range_probe(pdf_path, n, cost, dpi=150):
    """Range-probe galloping + binary search over the whole PDF (no cues, no confirmation).
    Token-cheapest if the oracle is reliable; most sensitive to oracle noise."""
    starts = _segment_search(pdf_path, 1, n, cost, dpi, confirm=False)
    return _spans(starts, n)


def sol4b_range_probe_cued(pdf_path, n, cost, precuts=None, confirm=True, dpi=150):
    """Cue-seeded, robustness-hardened Solution 4.

    `precuts` (high-confidence page-number resets) are treated as fixed boundaries: they split
    the PDF into segments that the search never crosses, shrinking each search range (fewer
    probes) and guaranteeing cuts on known-good seams. `confirm` adds the near-boundary
    adjacent-oracle cross-check. With precuts=None and confirm=False this is exactly sol4.
    """
    cuts = sorted({p for p in (precuts or set()) if 1 < p <= n})
    bounds = [1, *cuts, n + 1]
    starts = set()
    for i in range(len(bounds) - 1):
        seg_start, seg_end = bounds[i], bounds[i + 1] - 1
        starts.update(_segment_search(pdf_path, seg_start, seg_end, cost, dpi, confirm))
    return _spans(starts, n)


def naive_chunk_production(pdf_path, n, cost, chunk=CHUNK_SIZE):
    """The ACTUAL current approach (production /getPages): segment the PDF in non-overlapping
    `chunk`-page windows, each handled INDEPENDENTLY by the production prompt, with page numbers
    offset to absolute. Because chunks never see each other, a document straddling a chunk edge is
    severed -- so every chunk boundary is a HARD cut. This is the real baseline the candidate
    solutions must beat; it differs from chunk_upper (the gold-informed ceiling of this same
    chunking scheme) only in using the model's within-chunk starts instead of gold's.
    """
    starts = {1}
    for chunk_start in range(1, n + 1, chunk):
        chunk_end = min(chunk_start + chunk - 1, n)
        if chunk_start > 1:
            starts.add(chunk_start)  # independent chunks guarantee a boundary at every edge
        for s, _e in oracles.window_segment(pdf_path, chunk_start, chunk_end, cost):
            starts.add(s)
    return _spans(starts, n)


async def sol2_adjacent_image_async(pdf_path, n, cost, dpi=150,
                                    concurrency=genai_client.DEFAULT_CONCURRENCY):
    """Async sol2: the per-page NEW/SAME probes are independent, so run them concurrently (same
    result and token cost as sol2, far less wall-clock). The async_client_scope binds a fresh client
    to this loop so a multi-case bake-off does not hit "Event loop is closed" on the second case."""
    async with genai_client.async_client_scope():
        pages = list(range(2, n + 1))
        factories = [(lambda p=p: oracles.adjacent_async(pdf_path, p, cost, dpi=dpi)) for p in pages]
        results = await genai_client.gather_bounded(factories, limit=concurrency)
    starts = [1] + [p for p, r in zip(pages, results, strict=True) if r == "NEW"]
    return _spans(starts, n)


async def sol3_adjacent_markdown_async(pdf_path, n, cost,
                                       concurrency=genai_client.DEFAULT_CONCURRENCY):
    """Async sol3: markdown conversion stays local/sequential; the boundary calls run concurrently.
    Wrapped in async_client_scope for the same per-loop-client reason as sol2."""
    import markdown  # lazy: pulls in markitdown only for this solution

    mds = [markdown.page_markdown(pdf_path, p) for p in range(1, n + 1)]
    async with genai_client.async_client_scope():
        pages = list(range(2, n + 1))
        factories = [(lambda p=p: oracles.adjacent_text_async(mds[p - 2], mds[p - 1], cost))
                     for p in pages]
        results = await genai_client.gather_bounded(factories, limit=concurrency)
    starts = [1] + [p for p, r in zip(pages, results, strict=True) if r == "NEW"]
    return _spans(starts, n)


SOLUTIONS = {
    "1_windows": sol1_overlapping_windows,
    "2_adjacent_image": sol2_adjacent_image,
    "3_adjacent_markdown": sol3_adjacent_markdown,
    "4_range_probe": sol4_range_probe,
    "4b_range_probe_cued": sol4b_range_probe_cued,
}

# Solutions with an async (concurrent) implementation; sol4/4b are inherently sequential.
ASYNC_SOLUTIONS = {
    "2_adjacent_image": sol2_adjacent_image_async,
    "3_adjacent_markdown": sol3_adjacent_markdown_async,
}
