"""Offline verify/merge upgrade experiment - aggressive net + local text signals.

Tests how much of the over-segmentation an aggressive, enriched merge pass can recover
WITHOUT harming recall. Input = saved segmentation predictions (naive-diagnosis pred.csv,
the same rows the current verify pass was measured on). For EVERY adjacent row pair it asks
an enriched continuation oracle "is B a continuation of A?" - images (A's last page + B's
first pages) PLUS the OCR text of the boundary pages (continuation-sentence + "page N of M"
signals, which help LOCALLY even though text failed for global segmentation) PLUS metadata.
Refuted boundaries (YES = continuation) are auto-merged (measures the ceiling).

Recall-safety invariant: unclear -> NO -> KEEP the boundary. Hard gate: TRUE gold boundaries
harmed = 0. Leads with recall damage.

App code unchanged - this reuses the app's verify PHILOSOPHY in the harness, not its module.

Usage:
  uv run python src/verify_upgrade_experiment.py --dry "Case 3"    # call count only, no Gemini
  uv run python src/verify_upgrade_experiment.py "Case 3"          # run
"""
import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import images  # noqa: E402
import verdict_cache  # noqa: E402  (disk-backed, resumable across restarts / stalls)
from cases import by_id  # noqa: E402
from config import OUTPUTS  # noqa: E402
from pipeline import load_labels, load_pages, starts_to_spans  # noqa: E402
from metrics import exact_doc_f1, over_seg_ratio  # noqa: E402
from google.genai import types  # noqa: E402

NAIVE_DIR = os.path.join(OUTPUTS, "naive-diagnosis")
DPI = 120
FRAGMENT_PAGE_CAP = 2  # how many of B's own pages to show (fragments are short by nature)

_VERIFY_SYS = (
    "You review the segmentation of a scanned workers' compensation medical record. Given two "
    "adjacent segments with their metadata, page images, and page text, decide whether the "
    "second segment CONTINUES the first document or begins a separate document."
)


def _load_rows(case):
    """Predicted rows from naive-diagnosis pred.csv: start,end,title,date,injury,manual."""
    rows = []
    with open(os.path.join(NAIVE_DIR, case, "pred.csv"), encoding="utf-8") as f:
        for r in csv.reader(f):
            if len(r) < 2:
                continue
            try:
                s, e = int(r[0]), int(r[1])
            except ValueError:
                continue
            rows.append(dict(start=s, end=e, title=(r[2] if len(r) > 2 else "-"),
                             date=(r[3] if len(r) > 3 else "-")))
    return rows


def _png(pdf, page):
    return types.Part.from_bytes(data=images.render_png(pdf, page, DPI), mime_type="image/png")


def _tail_text(t, n=600):
    return (t or "").strip()[-n:]


def _head_text(t, n=600):
    return (t or "").strip()[:n]


_PAGE_OF = re.compile(r"page\s+(\d+)\s+of\s+(\d+)", re.I)


def _continues_pairwise(pdf, pages, prev, row, cost):
    """Enriched continuation oracle: YES = B continues A (merge). Recall-safe: unclear -> NO.
    Disk-cached by the (original) pair identity so a stalled run resumes with no re-spend."""
    from genai_client import classify_enum
    a_last, b_first = prev["end"], row["start"]
    b_show_end = min(row["end"], row["start"] + FRAGMENT_PAGE_CAP - 1)

    def _compute():
        a_txt, b_txt = _tail_text(pages[a_last - 1]), _head_text(pages[b_first - 1])
        prompt = (
            f"Document A: pages {prev['start']}-{prev['end']}, date {prev['date']}, title {prev['title']}\n"
            f"Segment B: pages {row['start']}-{row['end']}, date {row['date']}, title {row['title']}\n\n"
            f"A's LAST page text (end):\n...{a_txt}\n\n"
            f"B's FIRST page text (start):\n{b_txt}...\n\n"
            "The first image is A's last page; the following image(s) are B's first page(s). "
            "B may be a CONTINUATION of A - remaining report pages, an attachment, a lab table, a "
            "signature/stamp/certification page, terms/branding, or a blank separator - OR a "
            "separate document. Answer YES only when the evidence clearly shows continuation: a "
            "sentence or table flowing across the boundary, continued 'page N of M' pagination, the "
            "same author/visit continuing, or an attachment A explicitly references. Sharing a "
            "document type, date, or letterhead is NOT enough. If the evidence is unclear, answer NO."
        )
        contents = [prompt, _png(pdf, a_last)]
        for p in range(b_first, b_show_end + 1):
            contents.append(_png(pdf, p))
        return classify_enum(contents, ("YES", "NO"), _VERIFY_SYS, cost)

    args = f"a={prev['start']}-{prev['end']}|b={row['start']}-{row['end']}"
    verdict = verdict_cache.cached(pdf, "verify_merge", args, DPI, cost, _compute)
    return verdict == "YES"


def _apply_merges(rows, continues):
    """Cascade-merge: B folds into the current output doc when continues[B.start] is True."""
    out = []
    for row in rows:
        if out and continues.get(row["start"]):
            out[-1]["end"] = row["end"]  # extend; cascades (A+B then +C)
        else:
            out.append(dict(row))
    return out


def _spans(rows, n):
    return starts_to_spans(sorted({r["start"] for r in rows}), n)


def run_case(case, dry):
    pdf = by_id(case)["pdf"]
    n = images.page_count(pdf)
    _, gold = load_labels(by_id(case)["label_csv"], n)
    rows = _load_rows(case)
    pages, _ = load_pages(case)
    if pages is None:
        raise SystemExit(f"No OCR cache for {case}")
    if len(pages) < n:
        pages = pages + [""] * (n - len(pages))
    pairs = max(0, len(rows) - 1)
    print(f"\n### {case}: {n} pages, {len(gold)} gold docs | input rows={len(rows)} "
          f"| adjacent pairs (=calls) {pairs}")
    if dry:
        print("[dry] no Gemini calls.")
        return pairs

    from genai_client import Cost, MODEL
    print(f"model={MODEL}", flush=True)
    cost = Cost()
    continues = {}
    npairs = len(rows) - 1
    for i in range(1, len(rows)):
        v = _continues_pairwise(pdf, pages, rows[i - 1], rows[i], cost)
        continues[rows[i]["start"]] = v
        print(f"[{i}/{npairs}] boundary p{rows[i]['start']}: {'MERGE' if v else 'keep'}", flush=True)
    merged = _apply_merges(rows, continues)

    gs = sorted({s for s, _ in gold})
    in_starts = sorted({r["start"] for r in rows})
    out_starts = sorted({r["start"] for r in merged})
    # recall damage: gold starts present in the INPUT that the merge REMOVED
    harmed = sorted((set(gs) & set(in_starts)) - set(out_starts))

    in_spans, out_spans = _spans(rows, n), _spans(merged, n)
    print(f"{'stage':16}{'DocF1':>8}{'RECALL':>8}{'over':>7}{'rows':>6}")
    for name, sp, starts in (("input (raw)", in_spans, in_starts), ("upgraded verify", out_spans, out_starts)):
        tp = len(set(gs) & set(starts))
        print(f"{name:16}{exact_doc_f1(sp, gold):>8.3f}{tp / len(gs):>8.3f}"
              f"{over_seg_ratio(sp, gold):>7.2f}{len(starts):>6}")
    print(f"merged away: {len(in_starts) - len(out_starts)} boundaries | "
          f"TRUE boundaries HARMED (recall damage): {len(harmed)} {harmed}")
    if harmed:
        print(f"AUTO gate FAILED: {len(harmed)} true boundaries merged away (recall damage).")
    else:
        print("AUTO gate PASSED: 0 true boundaries harmed.")

    # --- suggest mode (recall-safe): same verdicts, but flags become suggestions, not merges ---
    gset = set(gs)
    flagged = [b for b in in_starts if continues.get(b)]          # boundaries suggested for merge
    correct = [b for b in flagged if b not in gset]              # true over-splits -> click saved
    wrong = [b for b in flagged if b in gset]                    # real docs -> declined (safe)
    over_splits = [b for b in in_starts if b != 1 and b not in gset]  # all false starts in input
    print("\n[suggest mode - recall-safe: flags are one-click suggestions, nothing auto-merged]")
    print(f"  suggestions: {len(flagged)} | correct (clicks saved): {len(correct)} | "
          f"wrong (declined, 0 data loss): {len(wrong)} {wrong}")
    prec = len(correct) / len(flagged) if flagged else 0.0
    cov = len(correct) / len(over_splits) if over_splits else 0.0
    print(f"  suggestion precision={prec:.2f} | over-split coverage={cov:.2f} "
          f"({len(correct)}/{len(over_splits)} input over-splits flagged)")
    print(f"cost: {cost.summary()}")
    return pairs


def main(argv):
    dry = "--dry" in argv
    cases = [a for a in argv if not a.startswith("--")] or ["Case 3"]
    total = sum(run_case(c, dry) for c in cases)
    print(f"\nTOTAL CALLS across {len(cases)} case(s): {total}")


if __name__ == "__main__":
    main(sys.argv[1:])
