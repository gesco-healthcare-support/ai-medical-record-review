"""Diagnose the CURRENT production segmentation (fixed 100-page chunks) one case at a time.

Runs the exact /getPages method live (chunk the PDF at 100 pages, one inline Gemini call per
chunk with the production prompt, offset local pages to absolute), writes the app-format CSV,
and produces a per-case FAILURE TAXONOMY against gold:

  - seam-severed gold docs (structural: a doc straddling a 100-page chunk edge is cut),
  - forced seam cuts that are NOT real boundaries,
  - within-chunk over-splits (false starts INSIDE a gold doc span),
  - ambiguous false starts (inside an UNLABELED gold gap -- gold is not a clean partition,
    so these may be real documents the human index skipped),
  - missed gold starts / merges (with near-miss +-1/+-2 localization tolerance),
  - raw output tiling violations (the app writes Gemini's raw s,e rows to the CSV, so
    within-chunk gaps/overlaps ship to production even though span normalization hides them).

PHI rules: prints ALIASES, page numbers, and metrics only -- never patient-named case ids,
never page content, never titles. Full rows (incl. titles) go only to the gitignored
outputs/ tree. Oversized chunks (Vertex ~20 MB inline cap) are sub-split at a byte budget;
those extra seams are tracked separately so they are not blamed on the 100-page grid.

Usage:
  python -u src/diagnose_naive.py list           # aliased inventory, no Gemini
  python -u src/diagnose_naive.py "Case 3"       # run one case (alias or safe id)
"""

import csv
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import oracles
from bake_off import _score_spans
from cases import ALL_CASE_IDS, by_id
from config import CHUNK_SIZE, OUTPUTS
from genai_client import MAX_RETRIES, MODEL, RETRY_MAX_DELAY, USE_VERTEX, Cost
from google.genai import types
from pipeline import load_labels, starts_to_spans
from pypdf import PdfReader, PdfWriter
from run_phase0 import _case

PAGE_CAP = 500          # per Adrian: skip PDFs bigger than this for now
RAW_BUDGET_MB = 12.5    # sub-split budget when a 100-page chunk trips the inline cap
NEAR_MISS_TOL = 2       # +-pages for "found but mislocalized"

OUT_DIR = os.path.join(OUTPUTS, "naive-diagnosis")


# ----- aliasing (PHI: patient-named ROR ids must never reach stdout/transcript) --------------


def alias_map():
    """Stable alias per registered case. csv + 'Manual ...' ids are already PHI-safe;
    the remaining ROR ids are patient-named and get R1..Rk in registry order."""
    mapping, k = {}, 0
    for cid in ALL_CASE_IDS:
        if cid.startswith("Case") or cid.startswith("Manual"):
            mapping[cid] = cid
        else:
            k += 1
            mapping[cid] = f"R{k}"
    return mapping


def resolve(alias):
    for cid, a in alias_map().items():
        if a == alias or cid == alias:
            return cid, a
    raise SystemExit(f"unknown case alias: {alias}")


# ----- the production-faithful chunked run, keeping FULL rows --------------------------------


def _chunk_grid(n, chunk=CHUNK_SIZE):
    return [(s, min(s + chunk - 1, n)) for s in range(1, n + 1, chunk)]


def _page_sizes(pdf_path, n):
    reader = PdfReader(pdf_path)
    sizes = []
    for p in range(n):
        w = PdfWriter()
        w.add_page(reader.pages[p])
        buf = io.BytesIO()
        w.write(buf)
        sizes.append(len(buf.getvalue()))
    return sizes


def _fits_inline(sizes, s, e):
    """Conservative: per-page sizes overestimate a multi-page chunk, so this never under-guards."""
    raw = sum(sizes[s - 1 : e])
    return (raw + 2) // 3 * 4 <= oracles._INLINE_REQUEST_CAP_BYTES - oracles._INLINE_ENVELOPE_MARGIN_BYTES


def _sub_split(sizes, s, e, budget_bytes):
    """Non-overlapping byte-budgeted sub-chunks of [s, e] (only used when the cap trips)."""
    out, cur = [], s
    while cur <= e:
        end, acc = cur, 0
        while end <= e and acc + sizes[end - 1] <= budget_bytes:
            acc += sizes[end - 1]
            end += 1
        if end == cur:
            raise RuntimeError(f"page {cur} alone exceeds the {budget_bytes / 1048576:.1f} MB budget")
        out.append((cur, end - 1))
        cur = end
    return out


def _segment_window_full(pdf_path, cs, ce, cost):
    """window_segment, but keeping the full parsed rows (t/d/i/m) with ABSOLUTE pages."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for p in range(cs - 1, ce):
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    part = types.Part.from_bytes(data=buf.getvalue(), mime_type="application/pdf")
    data = oracles.generate_json([part, oracles.SEGMENTATION_PROMPT], oracles._SEG_SYS, cost) or []
    rows, malformed = [], 0
    for item in data:
        try:
            s, e, title, d, i, m = oracles.parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            malformed += 1
            continue
        rows.append(dict(s=s + cs - 1, e=e + cs - 1, t=title, d=d, i=i, m=m, chunk=(cs, ce)))
    return rows, malformed


def run_case(alias):
    cid, alias = resolve(alias)
    c = _case(cid)
    n = c["n"]
    if n > PAGE_CAP:
        raise SystemExit(f"{alias}: {n} pages > {PAGE_CAP}-page cap; skipped by design")

    print(f"=== {alias}: {n} pages, {len(c['gold_starts'])} gold docs | model={MODEL} "
          f"vertex={USE_VERTEX} chunk={CHUNK_SIZE} retries={MAX_RETRIES}/{RETRY_MAX_DELAY}s",
          flush=True)

    sizes = _page_sizes(c["pdf"], n)
    grid = _chunk_grid(n)
    windows, cap_seams = [], []
    for cs, ce in grid:
        if _fits_inline(sizes, cs, ce):
            windows.append((cs, ce))
        else:
            subs = _sub_split(sizes, cs, ce, int(RAW_BUDGET_MB * 1024 * 1024))
            windows.extend(subs)
            cap_seams.extend(s for s, _e in subs[1:])
            print(f"  [inline cap] chunk {cs}-{ce} ({sum(sizes[cs - 1:ce]) / 1048576:.1f} MB raw) "
                  f"sub-split into {len(subs)} windows -> extra seams at {[s for s, _ in subs[1:]]}",
                  flush=True)

    cost, rows, malformed_total = Cost(), [], 0
    t0 = time.time()
    for k, (cs, ce) in enumerate(windows, 1):
        print(f"  [chunk {k}/{len(windows)}: pages {cs}-{ce}] calling ...", end="", flush=True)
        t1 = time.time()
        wrows, malformed = _segment_window_full(c["pdf"], cs, ce, cost)
        malformed_total += malformed
        rows.extend(wrows)
        print(f" {len(wrows)} sub-docs in {time.time() - t1:.1f}s", flush=True)
    wall = time.time() - t0

    # The app-format CSV (category "-": segmentation only). Titles stay in this gitignored file.
    case_dir = os.path.join(OUT_DIR, alias)
    os.makedirs(case_dir, exist_ok=True)
    with open(os.path.join(case_dir, "pred.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([r["s"], r["e"], "-", r["d"], r["i"], r["m"]])
    with open(os.path.join(case_dir, "pred_rows.json"), "w", encoding="utf-8") as f:
        json.dump(dict(windows=windows, cap_seams=cap_seams, malformed=malformed_total,
                       rows=rows), f, indent=1)

    report = analyze(alias, c, rows, windows, cap_seams, malformed_total)
    report += [f"cost: {cost.summary()}", f"wall-clock: {wall:.1f}s"]
    with open(os.path.join(case_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(f"# naive-chunk diagnosis: {alias}\n\n```\n" + "\n".join(report) + "\n```\n")
    print("\n".join(report), flush=True)
    print(f"saved: {case_dir}", flush=True)


# ----- the failure taxonomy ------------------------------------------------------------------


def _best_gold_shift(gold_starts, pred_starts, span=10):
    """Detect a CONSTANT page offset between gold and predictions (ROR-conversion artifact:
    the ROR's link targets can be off by the page-count difference between the PDF the ROR
    indexes and the input PDF we found). Returns (k, hits_at_k, hits_at_0)."""
    pred = set(pred_starts)
    hits = {k: sum(1 for g in gold_starts if g + k in pred) for k in range(-span, span + 1)}
    k = max(hits, key=lambda k: (hits[k], -abs(k)))
    return k, hits[k], hits[0]


def _shift_case(c, k):
    """Case dict with gold shifted by k (starts re-tiled to n, matching ROR derivation)."""
    n = c["n"]
    starts = sorted({min(max(g + k, 1), n) for g in c["gold_starts"]})
    spans = starts_to_spans(starts, n)
    return dict(c, gold_starts=starts, gold_spans=spans)


def analyze(alias, c, rows, windows, cap_seams, malformed_total, _shifted=False):
    n, gold_spans, gold_starts = c["n"], c["gold_spans"], sorted(c["gold_starts"])
    gold_start_set = set(gold_starts)
    grid_seams = [s for s, _e in windows[1:] if s not in set(cap_seams)]

    # Page -> labeled-region map. Gold is NOT a clean partition: pages outside every gold span
    # are UNLABELED (front matter + mid gaps); predictions there are ambiguous, not wrong.
    in_doc = {}
    for s, e in gold_spans:
        for p in range(s, e + 1):
            in_doc.setdefault(p, (s, e))

    # --- headline metrics on normalized spans (what the scoreboard reports) ---
    pred_starts = sorted({r["s"] for r in rows} | {1} | {s for s, _e in windows[1:]})
    spans = starts_to_spans(pred_starts, n)
    m = _score_spans(spans, c)

    # --- taxonomy on starts ---
    tol = NEAR_MISS_TOL
    near = lambda p, targets: any(abs(p - t) <= tol for t in targets)  # noqa: E731
    seams = sorted(set(grid_seams) | set(cap_seams))

    false_starts = [p for p in pred_starts if p not in gold_start_set]
    fs_seam = [p for p in false_starts if p in seams]
    fs_near = [p for p in false_starts if p not in seams and near(p, gold_starts)]
    fs_oversplit = [p for p in false_starts
                    if p not in seams and not near(p, gold_starts) and p in in_doc]
    fs_unlabeled = [p for p in false_starts
                    if p not in seams and not near(p, gold_starts) and p not in in_doc]

    missed = [g for g in gold_starts if g not in set(pred_starts)]
    missed_hard = [g for g in missed if not near(g, pred_starts)]
    missed_near = [g for g in missed if near(g, pred_starts)]

    severed = [(s, e) for s, e in gold_spans if any(s < seam <= e for seam in seams)]
    severed_grid = [(s, e) for s, e in gold_spans if any(s < seam <= e for seam in grid_seams)]

    # Which gold docs got over-split, and how badly (pieces per gold doc)
    pieces = {}
    for p in fs_oversplit:
        pieces[in_doc[p]] = pieces.get(in_doc[p], 0) + 1
    worst = sorted(pieces.items(), key=lambda kv: -kv[1])[:5]

    # --- raw-output quality: the app ships RAW rows to its CSV, so measure them raw ---
    gaps = overlaps = 0
    by_chunk = {}
    for r in rows:
        by_chunk.setdefault(tuple(r["chunk"]), []).append((r["s"], r["e"]))
    for (cs, ce), sp in by_chunk.items():
        sp.sort()
        cur = cs
        for s, e in sp:
            if s > cur:
                gaps += s - cur
            elif s < cur:
                overlaps += cur - s
            cur = max(cur, e + 1)
        if cur <= ce:
            gaps += ce - cur + 1
    manual_flags = sum(1 for r in rows if r["m"].strip().lower() == "x")
    no_date = sum(1 for r in rows if r["d"].strip() in ("", "-"))

    # Gold-offset self-check (skip when already analyzing shifted gold)
    offset_block = []
    if not _shifted:
        k, hits_k, hits_0 = _best_gold_shift(gold_starts, pred_starts)
        if k != 0 and hits_k >= max(hits_0 + 3, int(0.6 * len(gold_starts))):
            offset_block = [
                "",
                f"[GOLD OFFSET DETECTED] gold+({k:+d}) matches {hits_k}/{len(gold_starts)} pred "
                f"starts exactly (vs {hits_0} at +0) -> this case's ROR gold looks shifted; "
                f"metrics below are re-scored against corrected gold:",
            ]
            offset_block += [
                "  " + line
                for line in analyze(alias, _shift_case(c, k), rows, windows, cap_seams,
                                    malformed_total, _shifted=True)
                if line
            ]

    seam_forced_false = [p for p in fs_seam]
    return offset_block + [
        f"pred rows={len(rows)} (+{malformed_total} malformed skipped) -> spans={len(spans)} "
        f"vs gold={len(gold_spans)}",
        f"bF1={m['f1']:.2f} R={m['recall']:.2f} P={m['precision']:.2f} DocF1={m['doc_f1']:.2f} "
        f"wDocF1={m['wdoc_f1']:.2f} WD={m['windowdiff']:.3f} over={m['over_seg']:.2f}",
        "",
        f"[seams] grid seams={grid_seams} cap-extra seams={cap_seams}",
        f"  gold docs SEVERED by a seam: {len(severed)} "
        f"(by the 100-page grid alone: {len(severed_grid)}) -> {severed}",
        f"  seam cuts that are FALSE boundaries: {len(seam_forced_false)} of {len(seams)} "
        f"({seam_forced_false})",
        "",
        f"[within-chunk] OVER-SPLIT false starts inside a gold doc: {len(fs_oversplit)}",
        f"  worst-split gold docs (span: extra pieces): "
        f"{', '.join(f'{s}-{e}: +{k}' for (s, e), k in worst) if worst else 'none'}",
        f"  false starts in UNLABELED gaps (ambiguous, gold not a partition): {len(fs_unlabeled)}",
        f"  near-miss false starts (within +-{tol} of a gold start): {len(fs_near)} {fs_near}",
        "",
        f"[merges/misses] gold starts MISSED outright (worst error class): {len(missed_hard)} "
        f"{missed_hard}",
        f"  gold starts found but mislocalized (+-{tol}): {len(missed_near)} {missed_near}",
        "",
        f"[raw CSV quality] within-chunk page GAPS={gaps} OVERLAPS={overlaps} "
        f"(these ship in the app CSV as-is)",
        f"  rows flagged manual='x': {manual_flags}/{len(rows)}; rows with no doc date: "
        f"{no_date}/{len(rows)}",
    ]


def list_cases():
    amap = alias_map()
    print(f"{'alias':10}{'src':5}{'pages':>7}{'docs':>6}{'MB':>8}  eligible(<= {PAGE_CAP}pp)")
    for cid in ALL_CASE_IDS:
        info = by_id(cid)
        try:
            import fitz

            n = fitz.open(info["pdf"]).page_count
            mb = os.path.getsize(info["pdf"]) / 1048576
        except Exception as exc:
            print(f"{amap[cid]:10}{info['source']:5}  ERROR: {exc}")
            continue
        _, gold = load_labels(info["label_csv"], n)
        print(f"{amap[cid]:10}{info['source']:5}{n:>7}{len(gold):>6}{mb:>8.1f}  "
              f"{'YES' if n <= PAGE_CAP else 'no'}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "list"
    if arg == "list":
        list_cases()
    else:
        run_case(arg)
