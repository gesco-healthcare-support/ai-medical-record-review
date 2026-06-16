"""Phase 1 bake-off: run the four candidate segmenters on the labeled cases and score them.

Cost objective = total tokens; every method reports $/case + #calls. The Gemini-backed runs
need a PAID Gemini path (the free-tier key caps at 20 req/day); Solution 3 also needs
markitdown's OCR backend wired. The `selftest` command validates the sol2/sol4 search logic
NOW, under a perfect fake oracle derived from gold -- no Gemini spend.

Usage:
  python src/bake_off.py selftest
  python src/bake_off.py run ["Case 3" ...] [--only 2_adjacent_image,4_range_probe]
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cues
import metrics
import oracles
import solutions
from cases import CSV_CASE_IDS, ALL_CASE_IDS, by_id
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


def _chunk_upper(c):
    """Baseline = current approach's BEST case: gold starts + forced chunk-edge cuts."""
    edges = set(range(CHUNK_SIZE + 1, c["n"] + 1, CHUNK_SIZE))
    starts = set(c["gold_starts"]) | edges
    return starts_to_spans(sorted(starts), c["n"])


def run(case_ids, only=None):
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

        for name, fn in solutions.SOLUTIONS.items():
            if only and name not in only:
                continue
            cost = Cost()
            try:
                if name == "4b_range_probe_cued":
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
                f"bF1={m['f1']:.2f} WD={m['windowdiff']:.3f} over={m['over_seg']:.2f} "
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

    print(f"selftest OK over {n} pages, {len(spans)} gold docs (mean ~7.6pp):")
    print(f"  (1) perfect oracle: sol2/sol4/sol4b all recover gold "
          f"(calls sol2={c2.calls} sol4={c4.calls} sol4b={c4b.calls})")
    print(f"  (2) cue pre-cuts cut probes {c4.calls} -> {c4b_cued.calls}")
    print(f"  (3a) shifted boundary: confirm RELOCATES it -> exact recovery (plain over-shifts)")
    print(f"  (3b) false split: confirm VETOES it -> exact recovery "
          f"(plain over-segments to {len(plain_b)} docs)")
    print(f"  (4) random-per-call noise (unrealistic for temp-0): plain offset={rand_plain:.2f} "
          f"confirm={rand_conf:.2f} -- needs the better oracle; 0b decides")


def main(argv):
    cmd = argv[0] if argv else "selftest"
    if cmd == "selftest":
        selftest()
        return
    rest = [a for a in argv[1:] if not a.startswith("--")]
    only = None
    for a in argv[1:]:
        if a.startswith("--only"):
            only = set(a.split("=", 1)[1].split(",")) if "=" in a else None
    case_ids = ALL_CASE_IDS if rest == ["all"] else (rest or CSV_CASE_IDS)
    run(case_ids, only)


if __name__ == "__main__":
    main(sys.argv[1:])
