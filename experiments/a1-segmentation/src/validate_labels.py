"""Validate every labeled case's gold before we trust it in the bake-off.

The 3 AI-System cases are hand-typed at physical-document granularity; the 8 ROR cases are
derived from review-summary hyperlink targets and can be finer-grained or start mid-PDF
(front matter the reviewer did not link). This script quantifies each case's gold so we know
which to treat as the primary bar and which as directional breadth:

  pages, docs, mean/median doc length, #1-page docs, front gap (first start > 1),
  tail coverage, and partition validity (gaps/overlaps in the gold tiling).

It emits only COUNTS (never page text), so the printed/Committed summary carries no PHI; the
full per-case markdown goes to outputs/ (gitignored).

  python src/validate_labels.py            # all 11 cases
  python src/validate_labels.py "Case 1"   # a subset
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import images
import metrics
from cases import ALL_CASE_IDS, CSV_CASE_IDS, by_id
from config import OUTPUTS
from pipeline import load_labels

# A case is flagged when its gold looks unlike clean physical-document segmentation.
MIN_MEAN_DOC_LEN = 1.5      # below this the gold is probably over-granular (sub-doc links)
MAX_SINGLE_PAGE_FRAC = 0.6  # mostly 1-page "docs" -> likely a link-per-entry index, not docs


def _stats(case_id):
    c = by_id(case_id)
    n = images.page_count(c["pdf"])
    _, spans = load_labels(c["label_csv"], n)
    spans = sorted(spans)
    if not spans:
        return dict(id=case_id, tier=c["source"], n=n, docs=0, flags=["NO GOLD"])

    lengths = [e - s + 1 for s, e in spans]
    starts = [s for s, _ in spans]
    single = sum(1 for ln in lengths if ln == 1)
    front_gap = starts[0] - 1               # pages before the first labeled document
    tail_gap = n - max(e for _, e in spans)  # pages after the last labeled document
    valid = metrics.partition_validity(spans, n)

    flags = []
    mean_len = float(np.mean(lengths))
    if mean_len < MIN_MEAN_DOC_LEN:
        flags.append("OVER-GRANULAR(mean<1.5)")
    if single / len(spans) > MAX_SINGLE_PAGE_FRAC:
        flags.append(f"MOSTLY-1PAGE({single}/{len(spans)})")
    if front_gap > 0:
        flags.append(f"FRONT-GAP({front_gap}pp)")
    if not valid["valid"]:
        flags.append(f"NOT-TILED(gap={valid['gap_pages']},ovl={valid['overlap_pages']})")

    return dict(
        id=case_id, tier=c["source"], n=n, docs=len(spans),
        mean_len=mean_len, median_len=float(np.median(lengths)), max_len=max(lengths),
        single_page=single, front_gap=front_gap, tail_gap=tail_gap,
        valid=valid["valid"], flags=flags,
    )


_HEADER = (f"{'case':40}{'tier':>5}{'pages':>6}{'docs':>5}{'mean':>6}{'med':>5}"
           f"{'max':>5}{'1pg':>5}{'front':>6}{'tail':>5}  flags")


def _row(s):
    if s.get("docs", 0) == 0:
        return f"{s['id'][:40]:40}{s['tier']:>5}{s['n']:>6}{0:>5}  {' '.join(s['flags'])}"
    return (f"{s['id'][:40]:40}{s['tier']:>5}{s['n']:>6}{s['docs']:>5}"
            f"{s['mean_len']:>6.1f}{s['median_len']:>5.0f}{s['max_len']:>5}"
            f"{s['single_page']:>5}{s['front_gap']:>6}{s['tail_gap']:>5}  {' '.join(s['flags'])}")


def main(case_ids):
    rows = [_stats(cid) for cid in case_ids]
    lines = ["# Gold label validation\n", "```", _HEADER]
    print(_HEADER)
    for s in rows:
        line = _row(s)
        print(line)
        lines.append(line)
    lines.append("```")

    clean = [s for s in rows if s["tier"] == "csv"]
    ror = [s for s in rows if s["tier"] == "ror"]
    flagged = [s["id"] for s in rows if s.get("flags")]
    summary = (f"\n{len(clean)} clean (primary) + {len(ror)} ROR (secondary). "
               f"Flagged: {', '.join(flagged) if flagged else 'none'}.")
    print(summary)
    lines.append(summary)

    os.makedirs(OUTPUTS, exist_ok=True)
    out = os.path.join(OUTPUTS, "label_validation.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReport written: {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args or ALL_CASE_IDS)
    _ = CSV_CASE_IDS  # referenced for clarity that the 3 clean cases are the primary tier
