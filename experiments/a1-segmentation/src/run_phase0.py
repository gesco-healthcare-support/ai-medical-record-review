"""Phase 0 runner: free-cue survey (0a) + oracle reliability (0b) + synthesis report.

Usage:
  python src/run_phase0.py cues  ["Case 1" ...]      # 0a, free, defaults to all 3 cases
  python src/run_phase0.py oracle ["Case 3" ...]     # 0b, costs Gemini, defaults to Case 3
  python src/run_phase0.py all                        # cues (3) + oracle (Case 3 smoke)

0a tells us how many true boundaries free cues catch (recall) and how many false starts they
invent (precision). 0b measures whether Gemini's boundary answers are reliable enough for the
binary-search solution (idea 4) and per-page solutions (ideas 2/3), and the real token cost.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cues
import images
import metrics
import oracles
from cases import by_id, CSV_CASE_IDS
from config import OUTPUTS
from genai_client import Cost, MODEL
from pipeline import load_labels, load_pages, starts_to_spans

# Cost guards for 0b (Gemini spend). Adjacent runs every page; range-probe is sampled.
RANGE_INSIDE_PER_DOC = 2
RANGE_AFTER_PER_DOC = 1
RANGE_PROBE_CAP = 120
DPI = 150


def _case(case_id):
    pdf = by_id(case_id)["pdf"]
    n = images.page_count(pdf)
    _, gold_spans = load_labels(by_id(case_id)["label_csv"], n)
    gold_starts = sorted({s for s, _ in gold_spans})
    pages_text, _ = load_pages(case_id)
    if pages_text is None:
        pages_text = [""] * n
    if len(pages_text) < n:
        pages_text += [""] * (n - len(pages_text))
    return dict(id=case_id, pdf=pdf, n=n, gold_spans=gold_spans, gold_starts=gold_starts,
                pages_text=pages_text)


def _mask(starts, n):
    s = set(starts)
    return np.array([1 if p in s else 0 for p in range(1, n + 1)])


def _score_starts(pred_starts, c):
    """Boundary + segmentation metrics for a predicted START set against gold."""
    n, gold_starts = c["n"], c["gold_starts"]
    bm = metrics.boundary_metrics(_mask(gold_starts, n), _mask(pred_starts, n))
    pred_spans = starts_to_spans(list(pred_starts), n)
    k = metrics.default_k(n, len(gold_starts))
    ref_b = metrics.starts_to_boundary_mask(gold_starts, n)
    hyp_b = metrics.starts_to_boundary_mask(pred_starts, n)
    bm.update(
        doc_f1=metrics.exact_doc_f1(pred_spans, c["gold_spans"]),
        wdoc_f1=metrics.weighted_doc_f1(pred_spans, c["gold_spans"]),
        windowdiff=metrics.windowdiff(ref_b, hyp_b, k),
        pk=metrics.pk(ref_b, hyp_b, k),
        over_seg=metrics.over_seg_ratio(pred_spans, c["gold_spans"]),
        offset=metrics.mean_boundary_offset(pred_starts, gold_starts),
        n_pred=len(pred_spans),
    )
    return bm


# ----- 0a: free-cue survey ------------------------------------------------------------------


def run_cues(case_ids):
    lines = ["## 0a - Free-cue survey (no Gemini)\n"]
    for cid in case_ids:
        c = _case(cid)
        detectors = {
            "blank->next": cues.starts_after_blank(c["pdf"], c["n"]),
            "page-number": cues.starts_from_page_numbers(c["pages_text"]),
            "header-change": cues.starts_from_header_change(c["pages_text"]),
        }
        union = set().union(*detectors.values()) if detectors else set()
        detectors["UNION(all cues)"] = union

        header = f"\n### {cid}: {c['n']} pages, {len(c['gold_starts'])} gold docs"
        print(header)
        lines.append(header)
        row = f"{'cue':18}{'recall':>8}{'prec':>8}{'F1':>8}{'WindowDiff':>12}{'#pred':>7}"
        print(row)
        lines.append("```\n" + row)
        for name, starts in detectors.items():
            m = _score_starts(starts, c)
            r = (f"{name:18}{m['recall']:>8.2f}{m['precision']:>8.2f}{m['f1']:>8.2f}"
                 f"{m['windowdiff']:>12.3f}{m['n_pred']:>7}")
            print(r)
            lines.append(r)
        lines.append("```")
    return "\n".join(lines)


# ----- 0b: oracle reliability ---------------------------------------------------------------


def run_oracle(case_ids):
    lines = ["## 0b - Oracle reliability (Gemini, temp 0, " + MODEL + ")\n"]
    for cid in case_ids:
        c = _case(cid)
        gold_starts = set(c["gold_starts"])

        # --- adjacent oracle: every page 2..n -> NEW/SAME ---
        adj_cost = Cost()
        yt, yp, none_count = [], [], 0
        for p in range(2, c["n"] + 1):
            ans = oracles.adjacent(c["pdf"], p, adj_cost, dpi=DPI)
            yt.append(1 if p in gold_starts else 0)
            if ans is None:
                none_count += 1
            yp.append(1 if ans == "NEW" else 0)
        adj = metrics.boundary_metrics(np.array(yt), np.array(yp))

        # --- range-probe oracle: sampled inside/after each doc ---
        rp_cost = Cost()
        rp_true, rp_pred, probes = [], [], 0
        for i, (s, e) in enumerate(c["gold_spans"]):
            if probes >= RANGE_PROBE_CAP:
                break
            inside = np.linspace(s + 1, e, num=min(RANGE_INSIDE_PER_DOC, max(0, e - s)),
                                 dtype=int) if e > s else []
            nxt_end = c["gold_spans"][i + 1][1] if i + 1 < len(c["gold_spans"]) else c["n"]
            after = np.linspace(e + 1, nxt_end,
                                num=min(RANGE_AFTER_PER_DOC, max(0, nxt_end - e)), dtype=int) \
                if nxt_end > e else []
            for cand in list(inside) + list(after):
                if probes >= RANGE_PROBE_CAP:
                    break
                ans = oracles.range_probe(c["pdf"], s, int(cand), rp_cost, dpi=DPI)
                rp_true.append(0 if cand <= e else 1)         # 1 = NEW_DOC (left the doc)
                rp_pred.append(1 if ans == "NEW_DOC" else 0)
                probes += 1
        rp = metrics.boundary_metrics(np.array(rp_true), np.array(rp_pred)) if rp_true else {}

        block = [
            f"\n### {cid}: {c['n']} pages, {len(c['gold_starts'])} gold docs",
            "```",
            "ADJACENT (NEW vs SAME), one call per page:",
            f"  precision={adj['precision']:.2f} recall={adj['recall']:.2f} f1={adj['f1']:.2f} "
            f"MCC={adj['mcc']:.2f} kappa={adj['kappa']:.2f} bal_acc={adj['balanced_acc']:.2f} "
            f"(null answers={none_count})",
            f"  cost: {adj_cost.summary()}",
            "RANGE-PROBE (SAME_DOC vs NEW_DOC), sampled:",
            (f"  precision={rp['precision']:.2f} recall={rp['recall']:.2f} f1={rp['f1']:.2f} "
             f"MCC={rp['mcc']:.2f} bal_acc={rp['balanced_acc']:.2f} (probes={probes})"
             if rp else "  (no probes)"),
            f"  cost: {rp_cost.summary()}",
            f"TOTAL Gemini cost this case: ${adj_cost.usd + rp_cost.usd:.4f}",
            "```",
        ]
        print("\n".join(block))
        lines += block
    return "\n".join(lines)


def main(argv):
    cmd = argv[0] if argv else "all"
    rest = argv[1:]
    sections = ["# Phase 0 report\n"]
    if cmd in ("cues", "all"):
        sections.append(run_cues(rest or CSV_CASE_IDS))
    if cmd in ("oracle", "all"):
        sections.append(run_oracle(rest or ["Case 3"]))
    os.makedirs(OUTPUTS, exist_ok=True)
    out = os.path.join(OUTPUTS, "phase0.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(sections) + "\n")
    print(f"\nReport written: {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
