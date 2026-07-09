"""The four candidate segmenters for the Phase 1 bake-off.

Each takes (pdf_path, n_pages, Cost) and returns a list of (start, end) document spans, with
all Gemini usage recorded on the Cost accountant (cost objective = total tokens). None of
these run on the current free-tier Gemini key; the bake-off runs once a paid path exists.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genai_client
import oracles
from config import CHUNK_SIZE
from pipeline import starts_to_spans
from pypdf import PdfReader, PdfWriter


def _spans(starts, n):
    return starts_to_spans(sorted(set(starts)), n)


# Corroboration tolerance for the sol1 overlap-zone vote: the second window must have reported a
# start within this many pages. Matches the measured +-2pp boundary-localization scatter, so the
# vote kills single-view variance noise without punishing mere localization disagreement.
VOTE_TOL = 2


def _page_raw_sizes(pdf_path, n):
    """Per-page single-page-PDF byte size. A multi-page window's real size is slightly LESS than the
    sum of these (per-page structural overhead is not shared), so greedy packing to a budget stays
    conservatively under the inline cap."""
    reader = PdfReader(pdf_path)
    sizes = []
    for p in range(n):
        w = PdfWriter()
        w.add_page(reader.pages[p])
        buf = io.BytesIO()
        w.write(buf)
        sizes.append(len(buf.getvalue()))
    return sizes


def _fixed_page_windows(n, window, overlap):
    """Overlapping windows of a FIXED page count, stepping (window - overlap) pages each time."""
    windows, s = [], 1
    while True:
        e = min(s + window - 1, n)
        windows.append((s, e))
        if e == n:
            break
        s += window - overlap
    return windows


def _next_window_start(s, e, overlap):
    """Start of the window after (s, e): the overlap is CAPPED at a third of the window so the
    step never collapses. With a fixed overlap, a dense region (~260 KB/page) packs windows of
    ~30-45 pages and step = window - overlap degenerates to a 2-4 page crawl: the same pages get
    re-judged by many windows and temperature-0 variance accumulates false splits (measured live
    on Case 2, 2026-07-04). Capping at window//3 keeps the step >= ~2/3 of the window."""
    eff = min(overlap, max(1, (e - s + 1) // 3))
    return max(s + 1, e - eff + 1)


def _byte_budgeted_windows(pdf_path, n, overlap, budget_bytes):
    """Overlapping windows packed to a raw-byte budget, so each fits Vertex's 20 MB inline cap even
    when page byte-density varies (60-260 KB/page) -- a fixed page count cannot bound request size.
    Each window after the first starts before the previous window's last page (see
    _next_window_start for the overlap cap), giving the next window real left-context across the
    seam. Assumes no single page exceeds the budget (pages are ~60-260 KB; a ~12.5 MB budget holds
    dozens); fails fast if one does."""
    sizes = _page_raw_sizes(pdf_path, n)
    windows, s = [], 1
    while True:
        if sizes[s - 1] > budget_bytes:
            raise RuntimeError(
                f"page {s} is {sizes[s - 1] / 1048576:.1f} MB raw, larger than the "
                f"{budget_bytes / 1048576:.1f} MB window budget; raise the budget or route via GCS")
        e, acc = s, sizes[s - 1]
        while e < n and acc + sizes[e] <= budget_bytes:
            acc += sizes[e]
            e += 1
        windows.append((s, e))
        if e == n:
            break
        s = _next_window_start(s, e, overlap)
    return windows


def sol1_overlapping_windows(pdf_path, n, cost, window=80, overlap=30, byte_budget_mb=12.5,
                             vote=False):
    """Window oracle with OWNERSHIP-based seam handling.

    Splits the PDF into overlapping windows and asks the segmentation oracle for document starts in
    each, but trusts each window only for the pages it has real left-context for, so a document
    straddling a seam is never severed. window_segment always reports its window's FIRST page as a
    start -- an ARTIFACT, since that page has no predecessor inside the window to judge it against.
    Accepting it would force a hard cut at every window start, defeating the overlap (the original
    bug). Instead each window OWNS the decisions in (window_start, next_window_start]: it drops its
    own first-page artifact and decides the next window's start page here, where it has left-context.
    With overlap >= 1 every owned page is interior to its window, so every page in 2..n is judged
    exactly once, by a window that saw the page before it.

    Windows are BYTE-BUDGETED by default (packed to ~byte_budget_mb raw, ~33% larger as base64, under
    the 20 MB inline cap) so dense scans don't trip the size guard. Falls back to FIXED page-count
    windows (`window`/`overlap`) when byte_budget_mb is None or pdf_path is None (e.g. the synthetic
    selftest, which has no PDF to measure).

    OVERLAP-ZONE VOTE (opt-in, default OFF): pages in (window_start, prev_window_end] were judged
    by TWO windows but are owned by the later one. With vote=True, a start the owner reports there
    that the previous window did not corroborate (no start within +-VOTE_TOL) is dropped. Live
    ablation on Case 2 (2026-07-04): identical bF1 and cost, doc-level F1 up (0.57 -> 0.62), but
    outright MISSED boundaries doubled (2 -> 4) - the veto turns near-boundary disagreements into
    merges, the worst error class for MRR (a merged doc silently loses its summary). Off by
    default; a future variant should flag uncorroborated starts for review instead of dropping.
    """
    if not 1 <= overlap < window:
        raise ValueError(f"overlap must satisfy 1 <= overlap < window (got overlap={overlap}, "
                         f"window={window})")
    if pdf_path is None or byte_budget_mb is None:
        windows = _fixed_page_windows(n, window, overlap)
    else:
        windows = _byte_budgeted_windows(pdf_path, n, overlap, int(byte_budget_mb * 1024 * 1024))
    reports = [oracles.window_segment(pdf_path, ws, we, cost) for ws, we in windows]
    starts = {1}
    for k, (ws, we) in enumerate(windows):
        owned_cap = n if k == len(windows) - 1 else windows[k + 1][0]
        prev_we = windows[k - 1][1] if k else 0
        prev_starts = {a for a, _b in reports[k - 1]} if k else set()
        for a, _b in reports[k]:
            if not (ws < a <= owned_cap):  # ws< drops the first-page artifact; <=owned_cap = our turf
                continue
            if vote and a <= prev_we and not any(abs(a - p) <= VOTE_TOL for p in prev_starts):
                continue  # doubly-seen page, single-view start -> variance noise, veto
            starts.add(a)
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
