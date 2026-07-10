"""Zero-spend segmentation error triage against strict doc-F1.

Reuses the harness gold loader + metrics (no Gemini, no client build). For each
clean case it (a) reconciles recomputed doc-F1/recall/precision/over-seg with the
saved naive-diagnosis numbers, (b) decomposes every gold document's fate under the
saved predictions into exact-hit / shifted / merged / over-split, and (c) runs
what-if "recovery" passes to estimate how many strict-doc-F1 points each fix class
would buy back.

Predictions triaged:
  - naive_chunk (saved outputs/naive-diagnosis/<case>/pred.csv), all 3 clean cases.
    naive_chunk sits within ~0.01 doc-F1 of the current 1_windows method and shares
    its error family (recall ~1.0, over-seg ~1.5), so it proxies the live method
    where per-method 1_windows predictions were not persisted.
  - sol2 adjacent per-page (reconstructed from the oracle cache), Case 3 only -- the
    'detect the boundary once per page' method, for direct comparison.

Output is page numbers + counts only (no titles/dates/content) -> no PHI.

Usage:  uv run python src/triage_errors.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import images  # noqa: E402
from cases import by_id  # noqa: E402
from config import OUTPUTS  # noqa: E402
from pipeline import load_labels, starts_to_spans  # noqa: E402
from metrics import exact_doc_f1, over_seg_ratio  # noqa: E402

NAIVE_DIR = os.path.join(OUTPUTS, "naive-diagnosis")
CACHE_DIR = os.path.join(OUTPUTS, "oracle-cache")
CASES = ["Case 1", "Case 2", "Case 3"]
TOL = 2  # pages: how close a predicted start must be to count as "near" a gold start


def starts_of(spans):
    return sorted({s for s, _ in spans})


def nearest(sorted_list, x):
    """Signed distance from x to the nearest element (None if list empty)."""
    if not sorted_list:
        return None
    return min((v - x for v in sorted_list), key=abs)


def triage(pred_spans, gold_spans, tol=TOL):
    """Boundary-level + doc-level decomposition of pred vs gold (strict)."""
    gstarts, pstarts = starts_of(gold_spans), starts_of(pred_spans)
    pset = set(pstarts)
    gint = [g for g in gstarts if g != 1]   # internal gold boundaries (page 1 is free)
    pint = [p for p in pstarts if p != 1]

    exact_hit, shifted, missed = [], [], []
    for g in gint:
        if g in pset:
            exact_hit.append(g)
        else:
            d = nearest(pstarts, g)
            if d is not None and 0 < abs(d) <= tol:
                shifted.append((g, d))     # detected but mislocalized -> refine-recoverable
            else:
                missed.append(g)           # no pred boundary near -> MERGE (unrecoverable)

    extra = [p for p in pint
             if nearest(gstarts, p) is None or abs(nearest(gstarts, p)) > tol]
    exact_docs = set(map(tuple, gold_spans)) & set(map(tuple, pred_spans))
    return dict(n_gold=len(gold_spans), n_pred=len(pred_spans), n_gint=len(gint),
                exact_docs=len(exact_docs), b_exact=len(exact_hit),
                b_shifted=shifted, b_missed=missed, b_extra=extra)


def recover(pred_starts, gold_starts, n, tol=TOL):
    """What-if doc-F1 on the START set: base -> snap +-tol shifts -> drop far FPs -> both.
    Both sides are tiled from starts, so gold's own gaps are ignored here; these numbers
    are directional (the exact-span doc-F1 is reported separately and reconciles)."""
    gset = set(gold_starts)
    ps = sorted(set(pred_starts))
    gsorted = sorted(gset)

    def f1(starts):
        return exact_doc_f1(starts_to_spans(sorted(set(starts)), n),
                            starts_to_spans(gsorted, n))

    def near(p):
        d = nearest(gsorted, p)
        return d is not None and abs(d) <= tol

    snapped = [p + nearest(gsorted, p) if (p not in gset and near(p)) else p for p in ps]
    kept = [p for p in ps if near(p) or p == 1]
    both = [s for p, s in zip(ps, snapped) if near(p) or p == 1]
    return dict(base=f1(ps), snap=f1(snapped), drop=f1(kept), both=f1(both))


def load_naive(case):
    n = images.page_count(by_id(case)["pdf"])
    _, gold = load_labels(by_id(case)["label_csv"], n)
    _, pred = load_labels(os.path.join(NAIVE_DIR, case, "pred.csv"), n)
    return n, gold, pred


def sol2_case3_from_cache():
    """Reconstruct sol2 adjacent per-page starts for Case 3 from cached NEW/SAME verdicts."""
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".jsonl")]
    verdict = {}
    for fn in files:
        for line in open(os.path.join(CACHE_DIR, fn), encoding="utf-8"):
            d = json.loads(line)
            if d["k"].startswith("adjacent|"):
                verdict[int(d["k"].split("|")[1].split("=")[1])] = d["v"]
    starts = [1] + [p for p, v in verdict.items() if v == "NEW"]
    return sorted(set(starts)), len(verdict)


def fmt_case(case, n, gold, pred):
    t = triage(pred, gold)
    df1 = exact_doc_f1(pred, gold)
    gset, pset = set(starts_of(gold)), set(starts_of(pred))
    rec = len(gset & pset) / len(gset)
    prec = len(gset & pset) / len(pset)
    rc = recover(starts_of(pred), starts_of(gold), n)
    return [
        f"\n## {case}: {n} pages, {t['n_gold']} gold docs",
        "```",
        f"recomputed:  DocF1(strict)={df1:.3f}  start-recall={rec:.3f}  "
        f"start-prec={prec:.3f}  over-seg={over_seg_ratio(pred, gold):.2f}  pred_docs={t['n_pred']}",
        f"exact-TP docs: {t['exact_docs']}/{t['n_gold']} "
        f"({t['exact_docs'] / t['n_gold'] * 100:.0f}% land BOTH edges exactly)",
        "",
        f"internal gold boundaries: {t['n_gint']}",
        f"  exact-hit      : {t['b_exact']}",
        f"  shifted (+-{TOL})  : {len(t['b_shifted'])}   <- detected, mislocalized (refine-recoverable)",
        f"  MISSED (merge) : {len(t['b_missed'])}   <- unrecoverable  {t['b_missed']}",
        f"  extra pred starts (over-split/FP): {len(t['b_extra'])}   <- merge-recoverable",
        "",
        "what-if strict doc-F1 (start-set surgery, directional):",
        f"  base                        : {rc['base']:.3f}",
        f"  + snap +-{TOL} shifts onto gold : {rc['snap']:.3f}",
        f"  + drop far over-split FPs    : {rc['drop']:.3f}",
        f"  + both (refine & merge)      : {rc['both']:.3f}",
        "```",
    ]


def main():
    lines = ["# Segmentation error triage (strict doc-F1, zero Vertex spend)\n",
             "Predictions: naive_chunk saved pred.csv (chunk-family baseline, within ~0.01 "
             "doc-F1 of the current 1_windows method).",
             f"Buckets use tol=+-{TOL} pages for 'near a gold boundary'. Page numbers only (no PHI).\n"]
    for case in CASES:
        lines += fmt_case(case, *load_naive(case))

    n, gold, _ = load_naive("Case 3")
    s2, ncalls = sol2_case3_from_cache()
    s2_spans = starts_to_spans(s2, n)
    t2 = triage(s2_spans, gold)
    df1_2 = exact_doc_f1(s2_spans, gold)
    gset = set(starts_of(gold))
    rc2 = recover(s2, starts_of(gold), n)
    lines += [
        f"\n## Case 3 -- sol2 adjacent per-page (reconstructed from {ncalls} cached verdicts)",
        "The 'detect the boundary once per page' method.",
        "```",
        f"recomputed:  DocF1(strict)={df1_2:.3f}  start-recall={len(gset & set(s2)) / len(gset):.3f}  "
        f"start-prec={len(gset & set(s2)) / len(set(s2)):.3f}  pred_docs={t2['n_pred']}",
        f"exact-TP docs: {t2['exact_docs']}/{t2['n_gold']}  "
        f"shifted={len(t2['b_shifted'])}  MISSED={len(t2['b_missed'])} {t2['b_missed']}  "
        f"extra(over-split)={len(t2['b_extra'])}",
        f"what-if: base={rc2['base']:.3f}  snap={rc2['snap']:.3f}  "
        f"drop={rc2['drop']:.3f}  both={rc2['both']:.3f}",
        "```",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
