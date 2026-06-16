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
import metrics
import oracles
import solutions
from cases import CSV_CASE_IDS
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
        head = f"\n=== {cid}: {c['n']} pages, {len(c['gold_starts'])} gold docs ==="
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


def selftest():
    """Validate sol2/sol4 logic under a PERFECT fake oracle (no Gemini). They must recover a
    known gold segmentation exactly, and sol4 must do it in far fewer calls than sol2."""
    # Synthetic gold: documents of varying lengths over 60 pages.
    lengths = [1, 5, 2, 20, 1, 3, 10, 8, 1, 9]
    spans, p = [], 1
    for length in lengths:
        spans.append((p, p + length - 1))
        p += length
    n = p - 1
    gold_starts = {s for s, _ in spans}
    end_of = {}
    for s, e in spans:
        for page in range(s, e + 1):
            end_of[page] = e  # the gold end of whichever doc 'page' belongs to

    def fake_adjacent(pdf, page, cost, dpi=150):
        cost.add(None)
        return "NEW" if page in gold_starts else "SAME"

    def fake_range_probe(pdf, start, cand, cost, dpi=150):
        cost.add(None)
        return "SAME_DOC" if cand <= end_of[start] else "NEW_DOC"

    orig_a, orig_r = oracles.adjacent, oracles.range_probe
    oracles.adjacent, oracles.range_probe = fake_adjacent, fake_range_probe
    try:
        c2 = Cost()
        s2 = solutions.sol2_adjacent_image(None, n, c2)
        c4 = Cost()
        s4 = solutions.sol4_range_probe(None, n, c4)
    finally:
        oracles.adjacent, oracles.range_probe = orig_a, orig_r

    assert s2 == spans, f"sol2 did not recover gold: {s2}"
    assert s4 == spans, f"sol4 did not recover gold: {s4}"
    print(f"selftest OK: sol2 and sol4 both recover {len(spans)} gold docs over {n} pages.")
    print(f"  calls: sol2={c2.calls} (per-page)  sol4={c4.calls} (galloping+binary search)")


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
    run(rest or CSV_CASE_IDS, only)


if __name__ == "__main__":
    main(sys.argv[1:])
