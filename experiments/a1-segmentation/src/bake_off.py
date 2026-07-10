"""Phase 1 bake-off: run the four candidate segmenters on the labeled cases and score them.

Cost objective = total tokens; every method reports $/case + #calls. The Gemini-backed runs
need a PAID Gemini path (the free-tier key caps at 20 req/day); Solution 3 also needs
markitdown's OCR backend wired. The `selftest` command validates the sol2/sol4 search logic
NOW, under a perfect fake oracle derived from gold -- no Gemini spend.

Usage:
  python src/bake_off.py selftest
  python src/bake_off.py run ["Case 3" ...] [--only 2_adjacent_image,4_range_probe]
"""

import asyncio
import io
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cues
import metrics
import oracles
import solutions
from cases import ALL_CASE_IDS, CSV_CASE_IDS, by_id
from config import CHUNK_SIZE, OUTPUTS
from genai_client import Cost
from pipeline import starts_to_spans
from run_phase0 import _case


def _mask(starts, n):
    s = set(starts)
    return np.array([1 if p in s else 0 for p in range(1, n + 1)])


def _score_spans(spans, c):
    n, gold_starts = c["n"], c["gold_starts"]
    pred_starts = sorted({s for s, _ in spans})
    m = metrics.boundary_metrics(_mask(gold_starts, n), _mask(pred_starts, n))
    k = metrics.default_k(n, len(gold_starts))
    ref_b = metrics.starts_to_boundary_mask(gold_starts, n)
    hyp_b = metrics.starts_to_boundary_mask(pred_starts, n)
    m.update(
        doc_f1=metrics.exact_doc_f1(spans, c["gold_spans"]),
        wdoc_f1=metrics.weighted_doc_f1(spans, c["gold_spans"]),
        windowdiff=metrics.windowdiff(ref_b, hyp_b, k),
        pk=metrics.pk(ref_b, hyp_b, k),
        over_seg=metrics.over_seg_ratio(spans, c["gold_spans"]),
        partition=metrics.partition_validity(spans, n),
        offset=metrics.mean_boundary_offset(pred_starts, gold_starts),
        n_pred=len(spans),
    )
    return m


# Default in-flight cap for the async (image/markdown) solutions when the caller does not pass
# --concurrency. gemini-2.5-flash runs on dynamic shared quota, so 2 is a deliberate floor that
# survives DSQ where 4 did not. Pass --concurrency=1 to force the fully serial sync path.
ASYNC_DEFAULT_CONCURRENCY = 2


def _chunk_upper(c):
    """Baseline = current approach's BEST case: gold starts + forced chunk-edge cuts."""
    edges = set(range(CHUNK_SIZE + 1, c["n"] + 1, CHUNK_SIZE))
    starts = set(c["gold_starts"]) | edges
    return starts_to_spans(sorted(starts), c["n"])


def run(case_ids, only=None, concurrency=0):
    lines = ["# Phase 1 bake-off\n"]
    for cid in case_ids:
        c = _case(cid)
        tier = by_id(cid)["source"]
        head = f"\n=== {cid} [{tier}]: {c['n']} pages, {len(c['gold_starts'])} gold docs ==="
        print(head)
        lines.append(head)
        lines.append("```")

        base = _score_spans(_chunk_upper(c), c)
        baseline_row = f"  {'chunk_upper (bar)':22} DocF1={base['doc_f1']:.2f} bF1={base['f1']:.2f}"
        print(baseline_row)
        lines.append(baseline_row)

        # naive_chunk = the ACTUAL current approach (Gemini segments each non-overlapping chunk).
        # Gemini-backed, so it carries a Cost and respects --only; a failure (e.g. an oversized inline
        # chunk) is reported, not fatal -- the rest of the table still runs.
        if not only or "naive_chunk" in only:
            nc_cost = Cost()
            try:
                nc_spans = solutions.naive_chunk_production(c["pdf"], c["n"], nc_cost)
                nm = _score_spans(nc_spans, c)
                nc_row = (
                    f"  {'naive_chunk (actual)':22} DocF1={nm['doc_f1']:.2f} wDocF1={nm['wdoc_f1']:.2f} "
                    f"bF1={nm['f1']:.2f} R={nm['recall']:.2f} P={nm['precision']:.2f} "
                    f"WD={nm['windowdiff']:.3f} over={nm['over_seg']:.2f} "
                    f"valid={nm['partition']['valid']} | {nc_cost.summary()}"
                )
            except Exception as exc:  # baseline failure must not abort the bake-off
                nc_row = f"  {'naive_chunk (actual)':22} FAILED: {exc}"
            print(nc_row)
            lines.append(nc_row)

        for name, fn in solutions.SOLUTIONS.items():
            if only and name not in only:
                continue
            cost = Cost()
            try:
                # Async path for the embarrassingly-parallel solutions (same tokens, less wall-clock):
                # used unless the caller forces serial with --concurrency=1. Unset -> the DSQ-safe
                # default of 2; >1 -> the caller's value.
                if name in solutions.ASYNC_SOLUTIONS and concurrency != 1:
                    eff = concurrency if concurrency > 1 else ASYNC_DEFAULT_CONCURRENCY
                    afn = solutions.ASYNC_SOLUTIONS[name]
                    spans = asyncio.run(afn(c["pdf"], c["n"], cost, concurrency=eff))
                elif name == "4b_range_probe_cued":
                    # Seed the search with high-confidence (strict) page-number pre-cuts.
                    precuts = cues.starts_from_page_numbers(c["pages_text"], broad=False)
                    spans = fn(c["pdf"], c["n"], cost, precuts=precuts)
                else:
                    spans = fn(c["pdf"], c["n"], cost)
            except Exception as exc:  # a solution failing must not abort the bake-off
                row = f"  {name:22} FAILED: {exc}"
                print(row)
                lines.append(row)
                continue
            m = _score_spans(spans, c)
            row = (
                f"  {name:22} DocF1={m['doc_f1']:.2f} wDocF1={m['wdoc_f1']:.2f} "
                f"bF1={m['f1']:.2f} R={m['recall']:.2f} P={m['precision']:.2f} "
                f"WD={m['windowdiff']:.3f} over={m['over_seg']:.2f} "
                f"valid={m['partition']['valid']} | {cost.summary()}"
            )
            print(row)
            lines.append(row)
        lines.append("```")

    os.makedirs(OUTPUTS, exist_ok=True)
    out = os.path.join(OUTPUTS, "bake_off.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written: {out}")


def _synthetic_gold():
    """A record sized like production (~600-800pp PDFs, 7-10pp docs on average): here 91 pages,
    12 docs, mean ~7.6pp, with one 1-page doc and one long doc -- the realistic mix."""
    lengths = [8, 3, 12, 7, 1, 15, 9, 6, 10, 7, 11, 2]
    spans, p = [], 1
    for length in lengths:
        spans.append((p, p + length - 1))
        p += length
    n = p - 1
    gold_starts = sorted({s for s, _ in spans})
    end_of = {page: e for s, e in spans for page in range(s, e + 1)}
    return spans, n, gold_starts, end_of


def _patched(adjacent, range_probe, fn):
    """Run fn() with the oracles monkeypatched, always restoring them."""
    orig_a, orig_r = oracles.adjacent, oracles.range_probe
    oracles.adjacent, oracles.range_probe = adjacent, range_probe
    try:
        return fn()
    finally:
        oracles.adjacent, oracles.range_probe = orig_a, orig_r


def selftest():
    """Validate the search logic + the Task-3 robustness levers under fake oracles (no Gemini):
    (1) perfect oracle -> sol2/sol4/sol4b all recover gold exactly;
    (2) cue pre-cuts -> sol4b uses fewer probes than sol4;
    (3) an off-by-one range oracle -> near-boundary confirmation recovers gold that plain sol4
        gets wrong; (4) random independent noise -> confirmation lowers mean boundary error."""
    import random

    spans, n, gold_starts, end_of = _synthetic_gold()
    gold_start_set = set(gold_starts)

    def perfect_adjacent(pdf, page, cost, dpi=150):
        cost.add(None)
        return "NEW" if page in gold_start_set else "SAME"

    def perfect_range(pdf, start, cand, cost, dpi=150):
        cost.add(None)
        return "SAME_DOC" if cand <= end_of[start] else "NEW_DOC"

    # (1) perfect oracle: every solution recovers gold exactly.
    c2 = Cost()
    s2 = _patched(perfect_adjacent, perfect_range,
                  lambda: solutions.sol2_adjacent_image(None, n, c2))
    c4 = Cost()
    s4 = _patched(perfect_adjacent, perfect_range,
                  lambda: solutions.sol4_range_probe(None, n, c4))
    c4b = Cost()
    s4b = _patched(perfect_adjacent, perfect_range,
                   lambda: solutions.sol4b_range_probe_cued(None, n, c4b, confirm=True))
    assert s2 == spans, f"sol2 did not recover gold: {s2}"
    assert s4 == spans, f"sol4 did not recover gold: {s4}"
    assert s4b == spans, f"sol4b did not recover gold: {s4b}"

    # (1b) sol5 greedy accumulate: a perfect "belongs to the document assembled so far" oracle
    # (SAME_DOC iff the candidate is within the doc that began at doc_first) must recover gold.
    def perfect_belongs(pdf, doc_first, candidate, cost, dpi=150):
        cost.add(None)
        return "SAME_DOC" if candidate <= end_of[doc_first] else "NEW_DOC"

    orig_b = oracles.belongs_to_doc
    oracles.belongs_to_doc = perfect_belongs
    try:
        c5 = Cost()
        s5 = solutions.sol5_accumulate(None, n, c5)
    finally:
        oracles.belongs_to_doc = orig_b
    assert s5 == spans, f"sol5 did not recover gold: {s5}"
    assert c5.calls == n - 1, f"sol5 must call the oracle once per page 2..n: {c5.calls} != {n - 1}"

    # (2) cue pre-cuts shrink the search: feed sol4b a subset of true boundaries as pre-cuts.
    precuts = set(gold_starts[2::3])  # every third gold start, as if found for free
    c4b_cued = Cost()
    s4b_cued = _patched(perfect_adjacent, perfect_range,
                        lambda: solutions.sol4b_range_probe_cued(None, n, c4b_cued,
                                                                 precuts=precuts, confirm=False))
    assert s4b_cued == spans, f"sol4b(cued) did not recover gold: {s4b_cued}"
    assert c4b_cued.calls < c4.calls, f"cues did not cut probes: {c4b_cued.calls} vs {c4.calls}"

    # Temperature-0 Gemini is deterministic per input: the same page pair yields the same answer
    # every call, so realistic errors are systematic-per-page (not random-per-call), and an
    # independent VIEW (the adjacent oracle) is what decorrelates. Tests (3a)/(3b) model the two
    # systematic failures the plan names, with an accurate adjacent oracle.

    # (3a) RELOCATE: one boundary (between two multi-page docs) reported one page late.
    b0 = 24  # gold start of (24,30), preceded by the multi-page doc (12,23)

    def shift_range(pdf, start, cand, cost, dpi=150):
        cost.add(None)
        if end_of[start] + 1 == b0 and cand == b0:  # b0 still looks SAME for doc (12,23)
            return "SAME_DOC"
        return "SAME_DOC" if cand <= end_of[start] else "NEW_DOC"

    plain_a = _patched(perfect_adjacent, shift_range,
                       lambda: solutions.sol4_range_probe(None, n, Cost()))
    fixed_a = _patched(perfect_adjacent, shift_range,
                       lambda: solutions.sol4b_range_probe_cued(None, n, Cost(), confirm=True))
    assert fixed_a == spans, f"relocate did not repair the shifted boundary: {fixed_a}"
    assert plain_a != spans, "shifted-boundary oracle should have broken plain sol4 (test too weak)"

    # (3b) VETO: a long doc's interior looks like new letterhead, so range_probe calls doc (12,23)
    # finished early (NEW_DOC from page 17 on) -> a false split. Adjacent (accurate) sees no new
    # document there, so confirmation vetoes it and the search resumes to the true boundary 24.
    def split_range(pdf, start, cand, cost, dpi=150):
        cost.add(None)
        if start == 12 and cand >= 17:  # interior of doc (12,23) misread as a new document
            return "NEW_DOC"
        return "SAME_DOC" if cand <= end_of[start] else "NEW_DOC"

    plain_b = _patched(perfect_adjacent, split_range,
                       lambda: solutions.sol4_range_probe(None, n, Cost()))
    fixed_b = _patched(perfect_adjacent, split_range,
                       lambda: solutions.sol4b_range_probe_cued(None, n, Cost(), confirm=True))
    assert fixed_b == spans, f"veto did not remove the false split: {fixed_b}"
    assert len(plain_b) > len(spans), "false-split oracle should have over-segmented plain sol4"

    # (4) random-per-call noise is the WRONG model for temp-0 Gemini (calls are deterministic),
    # so this is a stress report, not an assertion: a corrector as noisy as the primary does not
    # reliably help. Phase 0b measures the real per-page oracle agreement that decides this.
    def noisy(p_adj, p_rng, seed):
        rng = random.Random(seed)
        def adj(pdf, page, cost, dpi=150):
            cost.add(None)
            t = "NEW" if page in gold_start_set else "SAME"
            return ("SAME" if t == "NEW" else "NEW") if rng.random() < p_adj else t
        def rng_probe(pdf, start, cand, cost, dpi=150):
            cost.add(None)
            t = "SAME_DOC" if cand <= end_of[start] else "NEW_DOC"
            return ("NEW_DOC" if t == "SAME_DOC" else "SAME_DOC") if rng.random() < p_rng else t
        return adj, rng_probe

    trials = 60
    plain_errs, conf_errs = [], []
    for seed in range(trials):
        a, r = noisy(0.02, 0.10, seed)
        sp = _patched(a, r, lambda: solutions.sol4_range_probe(None, n, Cost()))
        a, r = noisy(0.02, 0.10, seed)
        sc = _patched(a, r, lambda: solutions.sol4b_range_probe_cued(None, n, Cost(), confirm=True))
        plain_errs.append(metrics.mean_boundary_offset(sorted({s for s, _ in sp}), gold_starts))
        conf_errs.append(metrics.mean_boundary_offset(sorted({s for s, _ in sc}), gold_starts))
    rand_plain, rand_conf = sum(plain_errs) / trials, sum(conf_errs) / trials

    # (5) async path: sol2_async recovers gold AND respects the concurrency cap (no Gemini). sol2_async
    # now opens async_client_scope(), which builds a client via genai_client._build_client; we stub
    # that with a fake so the selftest needs no Gemini creds and exercises only the search logic.
    import genai_client as gc

    builds, closes = {"n": 0}, {"n": 0}

    class _FakeAio:
        async def aclose(self):
            closes["n"] += 1

    class _FakeClient:
        def __init__(self):
            self.aio = _FakeAio()

    def _fake_build():
        builds["n"] += 1
        return _FakeClient()

    inflight = {"cur": 0, "max": 0}

    async def fake_adjacent_async(pdf, page, cost, dpi=150):
        inflight["cur"] += 1
        inflight["max"] = max(inflight["max"], inflight["cur"])
        await asyncio.sleep(0.001)  # force overlap so concurrency is actually exercised
        cost.add(None)
        inflight["cur"] -= 1
        return "NEW" if page in gold_start_set else "SAME"

    orig_async, orig_build = oracles.adjacent_async, gc._build_client
    oracles.adjacent_async, gc._build_client = fake_adjacent_async, _fake_build
    try:
        ca = Cost()
        s_async = asyncio.run(solutions.sol2_adjacent_image_async(None, n, ca, concurrency=4))
    finally:
        oracles.adjacent_async, gc._build_client = orig_async, orig_build
    assert s_async == spans, f"sol2_async did not recover gold: {s_async}"
    assert inflight["max"] <= 4, f"concurrency cap breached: {inflight['max']} > 4"
    assert inflight["max"] > 1, "async path never ran calls concurrently (cap not exercised)"
    assert builds["n"] == 1 and closes["n"] == 1, \
        f"scope must build+close one client per run (built {builds['n']}, closed {closes['n']})"
    built5, closed5 = builds["n"], closes["n"]

    # (6) cross-loop regression (python-genai #1518): an httpx AsyncClient reused across asyncio.run()
    # raises "Event loop is closed". async_client_scope must build a FRESH client per loop and close
    # it, so two back-to-back asyncio.run() calls each get their own and the scoped contextvar resets.
    builds["n"] = closes["n"] = 0
    leaked = {"after": "sentinel"}

    async def _use_scope():
        async with gc.async_client_scope() as scoped:
            assert gc._active_async_client() is scoped, "scoped client not active inside the scope"
        leaked["after"] = gc._scoped_async_client.get()  # must be reset to the default (None)
        return True

    gc._build_client = _fake_build
    try:
        assert asyncio.run(_use_scope()) is True
        assert asyncio.run(_use_scope()) is True
    finally:
        gc._build_client = orig_build
    assert builds["n"] == 2, f"expected a fresh client per event loop, got {builds['n']}"
    assert closes["n"] == 2, f"expected aclose per event loop, got {closes['n']}"
    assert leaked["after"] is None, "async_client_scope did not reset the scoped-client contextvar"

    # (7) sol1 window_segment inlines the sub-PDF as application/pdf (NOT files.upload, which Vertex
    # rejects) and offsets local page numbers to absolute. Build a tiny in-memory PDF and stub
    # generate_json so no Gemini call happens.
    from google.genai import types as _types
    from pypdf import PdfWriter as _PdfWriter

    _w = _PdfWriter()
    for _ in range(6):
        _w.add_blank_page(width=200, height=200)
    _pdf_buf = io.BytesIO()
    _w.write(_pdf_buf)
    _pdf_buf.seek(0)

    captured = {}

    def _fake_generate_json(contents, system_instruction, cost, response_schema=None):
        captured["contents"] = contents
        captured["schema"] = response_schema
        cost.add(None)
        return [{"s": 1, "e": 2, "t": "A", "d": "-", "i": "-", "m": "-"},
                {"s": 3, "e": 5, "t": "B", "d": "-", "i": "-", "m": "-"}]

    orig_gj = oracles.generate_json
    oracles.generate_json = _fake_generate_json
    try:
        win_spans = oracles.window_segment(_pdf_buf, 2, 6, Cost())  # window pages 2..6, offset +1
    finally:
        oracles.generate_json = orig_gj
    inline_pdf = [p for p in captured.get("contents", [])
                  if isinstance(p, _types.Part) and getattr(p, "inline_data", None)
                  and p.inline_data.mime_type == "application/pdf"]
    assert inline_pdf, "window_segment must inline an application/pdf Part (not files.upload)"
    assert win_spans == [(2, 3), (4, 6)], f"window offsets not absolute: {win_spans}"
    assert captured.get("schema") is not None, "window_segment must pass the response schema"

    # (8) naive_chunk (T3): the actual production approach -- independent chunks force a HARD cut at
    # every chunk edge AND keep each chunk's within-chunk starts. Fake window_segment (no Gemini).
    def _fake_window(pdf, cs, ce, cost):
        cost.add(None)
        out = [(cs, min(cs + 2, ce))]          # 1st doc starts at the chunk's first page
        if cs + 3 <= ce:
            out.append((cs + 3, ce))           # 2nd doc starts 3 pages into the chunk
        return out

    orig_ws = oracles.window_segment
    oracles.window_segment = _fake_window
    try:
        nc = solutions.naive_chunk_production(None, 25, Cost(), chunk=10)  # chunks 1-10, 11-20, 21-25
    finally:
        oracles.window_segment = orig_ws
    nc_starts = sorted({s for s, _ in nc})
    assert {1, 11, 21}.issubset(nc_starts), f"naive_chunk missing hard chunk-edge cuts: {nc_starts}"
    assert {4, 14, 24}.issubset(nc_starts), f"naive_chunk dropped within-chunk starts: {nc_starts}"
    assert nc[0][0] == 1 and nc[-1][1] == 25, f"naive_chunk must tile pages 1..25: {nc}"

    # (9) sol1 overlapping windows: window_segment ALWAYS reports its window's first page as a start
    # (no left-context there) -- an artifact that, if trusted, force-cuts every window seam and severs
    # a doc straddling it. The ownership fix drops that artifact and lets the window WITH left-context
    # decide the seam page. A perfect-but-for-the-artifact oracle must recover gold exactly: the
    # synthetic doc (47..55) straddles the window=80/overlap=30 seam at page 51, so a correct sol1
    # must NOT cut there. Fake window_segment ({cs} + the gold starts interior to the window), no Gemini.
    def _fake_window_perfect(pdf, cs, ce, cost):
        cost.add(None)
        within = sorted({cs} | {g for g in gold_starts if cs < g <= ce})
        return [(st, (within[i + 1] - 1) if i + 1 < len(within) else ce)
                for i, st in enumerate(within)]

    orig_ws1 = oracles.window_segment
    oracles.window_segment = _fake_window_perfect
    try:
        s1 = solutions.sol1_overlapping_windows(None, n, Cost(), window=80, overlap=30)
    finally:
        oracles.window_segment = orig_ws1
    assert s1 == spans, f"sol1 did not recover gold (window-start artifact not dropped?): {s1}"
    assert 51 not in {st for st, _ in s1}, f"sol1 force-cut the straddled window seam at 51: {s1}"
    try:  # the overlap validator must fail fast on a degenerate (overlap >= window) config
        solutions.sol1_overlapping_windows(None, n, Cost(), window=80, overlap=80)
        raise AssertionError("sol1 accepted overlap >= window (fail-fast validator missing)")
    except ValueError:
        pass

    # (9b) overlap-zone vote: a start that only ONE of two covering windows reported is variance
    # noise -> vetoed; singly-seen territory is never vetoed (recall there rests on one view).
    # Windows for n=91/w=80/ov=30 are (1,80),(51,91): 66 is doubly-seen (window 1 has no start
    # within +-VOTE_TOL of it) -> veto; 85 is beyond window 1 -> singly-seen -> keep.
    def _fake_window_noisy(pdf, cs, ce, cost):
        cost.add(None)
        within = sorted({cs} | {g for g in gold_starts if cs < g <= ce})
        out = [(st, (within[i + 1] - 1) if i + 1 < len(within) else ce)
               for i, st in enumerate(within)]
        if cs > 1:  # inject uncorroborated starts into the SECOND window's report only
            out += [(66, 67), (85, 86)]
        return out

    oracles.window_segment = _fake_window_noisy
    try:
        s1n = solutions.sol1_overlapping_windows(None, n, Cost(), window=80, overlap=30, vote=True)
    finally:
        oracles.window_segment = orig_ws1
    s1n_starts = {st for st, _ in s1n}
    assert 66 not in s1n_starts, f"vote failed to veto the doubly-seen variance start: {s1n}"
    assert 85 in s1n_starts, f"vote wrongly vetoed a singly-seen start: {s1n}"
    assert {g for g in gold_starts} <= s1n_starts, f"vote dropped corroborated gold starts: {s1n}"

    # (9c) overlap cap: dense windows step by >= ~2/3 window instead of crawling; big windows keep
    # the full overlap (Case 1's live windows (1,102) -> next start 73 must be preserved).
    assert solutions._next_window_start(292, 333, 30) == 320, "dense window should cap overlap"
    assert solutions._next_window_start(1, 102, 30) == 73, "large window should keep full overlap"

    print(f"selftest OK over {n} pages, {len(spans)} gold docs (mean ~7.6pp):")
    print(f"  (1) perfect oracle: sol2/sol4/sol4b all recover gold "
          f"(calls sol2={c2.calls} sol4={c4.calls} sol4b={c4b.calls})")
    print(f"  (1b) sol5 greedy accumulate recovers gold under a perfect belongs-oracle "
          f"({c5.calls} calls, one per page)")
    print(f"  (2) cue pre-cuts cut probes {c4.calls} -> {c4b_cued.calls}")
    print("  (3a) shifted boundary: confirm RELOCATES it -> exact recovery (plain over-shifts)")
    print(f"  (3b) false split: confirm VETOES it -> exact recovery "
          f"(plain over-segments to {len(plain_b)} docs)")
    print(f"  (4) random-per-call noise (unrealistic for temp-0): plain offset={rand_plain:.2f} "
          f"confirm={rand_conf:.2f} -- needs the better oracle; 0b decides")
    print(f"  (5) async sol2 recovers gold; peak concurrency {inflight['max']} (cap 4); "
          f"scope built+closed {built5}/{closed5} client")
    print("  (6) async_client_scope: fresh client per asyncio.run (built 2, closed 2, var reset) "
          "-- survives the #1518 cross-loop bug")
    print(f"  (7) sol1 window_segment inlines application/pdf (no files.upload) + offsets to "
          f"absolute: {win_spans}")
    print("  (8) naive_chunk forces hard cuts at every chunk edge + keeps within-chunk starts "
          "(actual current approach)")
    print("  (9) sol1 overlapping windows: ownership drops the window-start artifact -> recovers "
          "gold exactly + un-severs the doc straddling the seam (no false cut at 51)")
    print("  (9b) overlap-zone vote: uncorroborated doubly-seen start vetoed (66), singly-seen "
          "kept (85), gold intact")
    print("  (9c) overlap cap: dense windows step >= ~2/3 window (no crawl); large windows "
          "keep the full overlap")


def main(argv):
    cmd = argv[0] if argv else "selftest"
    if cmd == "selftest":
        selftest()
        return
    rest = [a for a in argv[1:] if not a.startswith("--")]
    only, concurrency = None, 0
    for a in argv[1:]:
        if a.startswith("--only") and "=" in a:
            only = set(a.split("=", 1)[1].split(","))
        elif a.startswith("--concurrency") and "=" in a:
            concurrency = int(a.split("=", 1)[1])
    case_ids = ALL_CASE_IDS if rest == ["all"] else (rest or CSV_CASE_IDS)
    run(case_ids, only, concurrency)


if __name__ == "__main__":
    main(sys.argv[1:])
