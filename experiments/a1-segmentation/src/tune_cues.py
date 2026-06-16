"""Measure + tune the free text cues offline (no Gemini, fast - reads the OCR cache only).

Two jobs:
  1. Report the page-number cue (after the footer-band + broader-grammar rework) per case, so
     we can compare against the locked 0a baseline and confirm the recall lift holds its
     precision (page-number cue is tuned for PRECISION -- it becomes a fixed pre-cut).
  2. Sweep the header/footer similarity threshold and pick the best value on the 3 clean
     cases (the reliable tier), REPORTING -- not fitting -- on the 8 ROR cases, to avoid
     overfitting a threshold to 11 small cases. Header cue is tuned for RECALL (candidate set).

Skips the blank cue entirely (it renders thousands of pages for ~0 recall and is already
dropped). Text cues read the cached OCR, so this runs in seconds.

  python src/tune_cues.py            # all 11 cases
  python src/tune_cues.py "Case 1"   # a subset
"""

import os
import sys

import cues
from cases import ALL_CASE_IDS, by_id
from config import OUTPUTS
from run_phase0 import _case, _score_starts

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SIM_GRID = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def _mean(rows, key):
    vals = [r[key] for r in rows if r[key] == r[key]]  # drop NaN
    return sum(vals) / len(vals) if vals else float("nan")


def _tier(cid):
    return by_id(cid)["source"]


def run(case_ids):
    cases_data = [_case(cid) for cid in case_ids]
    lines = ["# Cue tuning (text cues, no Gemini)\n"]

    # --- page-number cue: strict (pre-cut, precision) vs broad (candidate, recall) ---
    lines.append("## Page-number cue: strict (pre-cut) vs broad (candidate union)\n")
    head = (f"{'case':38}{'tier':>5}{'strictR':>9}{'strictP':>9}"
            f"{'broadR':>9}{'broadP':>9}")
    print(head)
    lines.append("```\n" + head)
    pn_rows = []
    for c in cases_data:
        ms = _score_starts(cues.starts_from_page_numbers(c["pages_text"], broad=False), c)
        mb = _score_starts(cues.starts_from_page_numbers(c["pages_text"], broad=True), c)
        pn_rows.append(dict(id=c["id"], tier=_tier(c["id"]), strict=ms, broad=mb))
        line = (f"{c['id'][:38]:38}{_tier(c['id']):>5}{ms['recall']:>9.2f}{ms['precision']:>9.2f}"
                f"{mb['recall']:>9.2f}{mb['precision']:>9.2f}")
        print(line)
        lines.append(line)
    for tier in ("csv", "ror"):
        sub = [r for r in pn_rows if r["tier"] == tier]
        if sub:
            msg = (f"  {tier} mean: strict R={_mean([r['strict'] for r in sub], 'recall'):.2f}"
                   f"/P={_mean([r['strict'] for r in sub], 'precision'):.2f}  "
                   f"broad R={_mean([r['broad'] for r in sub], 'recall'):.2f}"
                   f"/P={_mean([r['broad'] for r in sub], 'precision'):.2f}")
            print(msg)
            lines.append(msg)
    lines.append("```")

    # --- header cue threshold sweep ---
    lines.append("\n## Header/footer similarity sweep (pick on clean, report on ROR)\n")
    head = f"{'threshold':>10}{'clean_recall':>14}{'clean_prec':>12}{'clean_F1':>10}{'ror_recall':>12}"
    print("\n" + head)
    lines.append("```\n" + head)
    best_t, best_f1 = SIM_GRID[0], -1.0
    for t in SIM_GRID:
        clean, ror = [], []
        for c in cases_data:
            starts = cues.starts_from_header_change(c["pages_text"], threshold=t)
            m = _score_starts(starts, c)
            (clean if _tier(c["id"]) == "csv" else ror).append(m)
        cf1 = _mean(clean, "f1")
        line = (f"{t:>10.2f}{_mean(clean, 'recall'):>14.2f}{_mean(clean, 'precision'):>12.2f}"
                f"{cf1:>10.2f}{_mean(ror, 'recall'):>12.2f}")
        print(line)
        lines.append(line)
        if cf1 == cf1 and cf1 > best_f1:
            best_t, best_f1 = t, cf1
    pick = f"  -> best clean F1 at threshold={best_t:.2f} (F1={best_f1:.2f})"
    print(pick)
    lines.append(pick + "\n```")

    os.makedirs(OUTPUTS, exist_ok=True)
    out = os.path.join(OUTPUTS, "cue_tuning.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written: {out}")
    return best_t


if __name__ == "__main__":
    args = sys.argv[1:]
    run(args or ALL_CASE_IDS)
