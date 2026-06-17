"""Offline (no-Gemini) diagnostics that explain the Phase-1 bake-off sol4-vs-sol4b split per case.

Why this exists: sol4b (cue-seeded range probe) beats plain sol4 on Cases 1-2 but slightly LOSES on
Case 3. This script quantifies the cause WITHOUT spending Gemini -- it reads only the Tesseract OCR
cache + the gold CSVs. Per case it reports:

  - the STRICT "Page X of Y" page-number pre-cut set (exactly what sol4b seeds its search with) vs
    gold: recall / precision / #precuts. Those pre-cuts ARE sol4b's advantage; where their recall
    collapses, sol4b loses its seeding and degenerates toward plain sol4.
  - the gold document-length distribution (short docs are the hardest case for the range-probe
    galloping search, which doubles its stride from each document start).

It writes a short findings block to outputs/case_diagnostics.md. Run:
  python src/diagnose_cases.py            # the 3 CSV cases
  python src/diagnose_cases.py all        # every registered case
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cues
import metrics
from cases import ALL_CASE_IDS, CSV_CASE_IDS
from config import OUTPUTS
from run_phase0 import _case

# Phase-1 boundary-F1 per case, copied from outputs/bake_off.md (run 2026-06-17), so the diagnostic
# can restate the 4-vs-4b delta beside its cause. Static on purpose: this script makes no Gemini call.
BAKEOFF_BF1 = {
    "Case 1": {"sol4": 0.62, "sol4b": 0.70},
    "Case 2": {"sol4": 0.68, "sol4b": 0.80},
    "Case 3": {"sol4": 0.59, "sol4b": 0.55},
}


def _mask(starts, n):
    s = set(starts)
    return np.array([1 if p in s else 0 for p in range(1, n + 1)])


def _precut_stats(c):
    """Strict page-number pre-cuts vs gold -- the seed sol4b relies on (recall is the key number)."""
    precuts = cues.starts_from_page_numbers(c["pages_text"], broad=False)
    bm = metrics.boundary_metrics(_mask(c["gold_starts"], c["n"]), _mask(precuts, c["n"]))
    return precuts, bm


def _doclen_stats(gold_spans):
    """Gold document-length distribution; short docs stress the galloping range-probe search."""
    arr = np.array([e - s + 1 for s, e in gold_spans])
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "min": int(arr.min()),
        "max": int(arr.max()),
        "n_1page": int((arr == 1).sum()),
        "n_le3": int((arr <= 3).sum()),
        "frac_le3": float((arr <= 3).mean()),
    }


def diagnose(case_ids):
    lines = [
        "# Case diagnostics (offline, no Gemini)\n",
        "Explains the Phase-1 sol4 vs sol4b split. sol4b seeds its range-probe search with strict",
        "page-number pre-cuts; where that seed is sparse, sol4b loses its edge over plain sol4.\n",
    ]
    for cid in case_ids:
        c = _case(cid)
        precuts, bm = _precut_stats(c)
        dl = _doclen_stats(c["gold_spans"])
        bf1 = BAKEOFF_BF1.get(cid, {})
        s4, s4b = bf1.get("sol4"), bf1.get("sol4b")
        delta = f"{s4b - s4:+.2f}" if (s4 is not None and s4b is not None) else "?"
        block = [
            f"\n## {cid}: {c['n']} pages, {len(c['gold_starts'])} gold docs",
            "```",
            f"strict page-number pre-cuts (sol4b seed): recall={bm['recall']:.2f} "
            f"precision={bm['precision']:.2f} f1={bm['f1']:.2f} "
            f"(#precuts={len(precuts)} of {len(c['gold_starts'])} gold starts)",
            f"gold doc length (pp): mean={dl['mean']:.1f} median={dl['median']:.0f} "
            f"min={dl['min']} max={dl['max']}; short <=3pp: {dl['n_le3']} "
            f"({dl['frac_le3'] * 100:.0f}%), 1-page: {dl['n_1page']}",
            f"bake-off boundary-F1: sol4={s4} sol4b={s4b} (4b - 4 = {delta})",
            "```",
        ]
        print("\n".join(block))
        lines += block

    os.makedirs(OUTPUTS, exist_ok=True)
    out = os.path.join(OUTPUTS, "case_diagnostics.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written: {out}")


def main(argv):
    case_ids = ALL_CASE_IDS if argv == ["all"] else (argv or CSV_CASE_IDS)
    diagnose(case_ids)


if __name__ == "__main__":
    main(sys.argv[1:])
