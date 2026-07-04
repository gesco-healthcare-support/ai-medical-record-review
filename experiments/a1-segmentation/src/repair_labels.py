"""Audit and repair the sample answer keys (gold label CSVs).

Two defect classes are machine-detectable; the rest (bundle granularity, unlabeled gaps)
need human adjudication, for which this script generates review sheets.

1. CONSTANT PAGE OFFSET (ROR-conversion artifact: the ROR's link targets index a different
   PDF version than the input we found). Detection uses PHYSICAL evidence only - printed
   "Page 1 of N" resets extracted from the Tesseract OCR cache (cues.starts_from_page_numbers,
   strict mode: high precision) - so no Gemini call and no circularity with the model being
   evaluated. If gold starts align with the printed-page evidence markedly better at shift k
   than at 0, the key is off by k.

2. ADJUDICATION SHEETS: per case, the unlabeled page ranges (gold is not a partition) and
   oversized gold entries (>= 20pp, candidate reviewer 'bundles') - page numbers only, no PHI
   content - so a human can decide the granularity policy and label the gaps.

Usage:
  python -u src/repair_labels.py audit             # all cases, report evidence, change nothing
  python -u src/repair_labels.py apply R4=-5 ...   # shift named cases' ROR label CSVs (backup kept)
  python -u src/repair_labels.py sheets            # write adjudication sheets to outputs/key-repair
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cues
from cases import ALL_CASE_IDS, by_id
from config import OUTPUTS
from diagnose_naive import alias_map, resolve
from pipeline import load_labels, load_pages

OUT_DIR = os.path.join(OUTPUTS, "key-repair")
SHIFT_SPAN = 10  # test offsets -10..+10
BUNDLE_MIN_PAGES = 20


def _case_data(cid):
    pages_text, n = load_pages(cid)
    if pages_text is None:
        return None
    _, gold_spans = load_labels(by_id(cid)["label_csv"], n)
    gold_starts = sorted({s for s, _e in gold_spans})
    return dict(n=n, pages_text=pages_text, gold_spans=gold_spans, gold_starts=gold_starts)


def _shift_evidence(gold_starts, evidence_starts):
    """hits(k) = how many gold starts land exactly on printed-page evidence when shifted by k."""
    ev = set(evidence_starts)
    return {k: sum(1 for g in gold_starts if g + k in ev) for k in range(-SHIFT_SPAN, SHIFT_SPAN + 1)}


def audit():
    amap = alias_map()
    print(f"{'case':14}{'pages':>6}{'docs':>6}{'#evid':>7}{'hits@0':>8}{'best':>12}  verdict")
    for cid in ALL_CASE_IDS:
        d = _case_data(cid)
        if d is None:
            print(f"{amap[cid]:14}  (no OCR cache; skipped)")
            continue
        evidence = cues.starts_from_page_numbers(d["pages_text"], broad=False)
        hits = _shift_evidence(d["gold_starts"], evidence)
        k = max(hits, key=lambda k: (hits[k], -abs(k)))
        h0, hk = hits[0], hits[k]
        # Verdict: an offset needs to beat k=0 clearly AND explain a decent share of the
        # evidence-covered starts; sparse printed-page coverage keeps this conservative.
        if k != 0 and hk >= max(h0 + 3, int(1.5 * max(h0, 1))):
            verdict = f"OFFSET k={k:+d} likely"
        elif hk == 0:
            verdict = "no printed-page evidence overlaps gold (inconclusive)"
        else:
            verdict = "aligned (no offset evidence)"
        print(f"{amap[cid]:14}{d['n']:>6}{len(d['gold_starts']):>6}{len(evidence):>7}"
              f"{h0:>8}{f'{hits[k]} @ {k:+d}':>12}  {verdict}")


def apply(assignments):
    """Shift a ROR case's label CSV by k pages (start,end both shifted; re-clamped to n).
    Original kept as <name>.csv.orig-<date-free> backup; refuses hand-typed CSV cases."""
    for spec in assignments:
        alias, _, k_str = spec.partition("=")
        k = int(k_str)
        cid, alias = resolve(alias)
        info = by_id(cid)
        if info["source"] != "ror":
            raise SystemExit(f"{alias}: hand-typed CSV case - repair those by hand, not by script")
        d = _case_data(cid)
        if d is None:
            raise SystemExit(f"{alias}: no OCR cache; run ocr_prep first (n is needed to clamp)")
        path = info["label_csv"]
        backup = path + ".orig"
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        rows = []
        with open(backup, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[0].strip().isdigit():
                    s = min(max(int(parts[0]) + k, 1), d["n"])
                    e = min(max(int(parts[1]) + k, 1), d["n"])
                    rows.append([str(s), str(e)] + parts[2:])
        # Re-tile ends to the next start - 1 (ROR keys are start-derived; shifting can
        # otherwise leave a clamped short tail like R4's old 66-69 artifact).
        for i in range(len(rows) - 1):
            rows[i][1] = str(int(rows[i + 1][0]) - 1)
        if rows:
            rows[-1][1] = str(d["n"])
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(",".join(r) + "\n" for r in rows)
        # Print the alias only: label paths contain patient-named case ids (PHI).
        print(f"{alias}: shifted by {k:+d}, {len(rows)} rows written, backup kept as <csv>.orig")


def sheets():
    os.makedirs(OUT_DIR, exist_ok=True)
    amap = alias_map()
    for cid in ALL_CASE_IDS:
        d = _case_data(cid)
        if d is None:
            continue
        alias = amap[cid]
        gaps, cursor = [], 1
        for s, e in d["gold_spans"]:
            if s > cursor:
                gaps.append((cursor, s - 1))
            cursor = max(cursor, e + 1)
        if cursor <= d["n"]:
            gaps.append((cursor, d["n"]))
        bundles = [(s, e) for s, e in d["gold_spans"] if e - s + 1 >= BUNDLE_MIN_PAGES]
        lines = [
            f"# Answer-key adjudication: {alias}",
            "",
            f"Pages: {d['n']}, labeled docs: {len(d['gold_spans'])}.",
            "",
            f"## Unlabeled page ranges ({sum(e - s + 1 for s, e in gaps)} pages)",
            "Decide per range: front/administrative matter (label as one doc or 'skip'),",
            "or real documents the index missed (add rows).",
            "",
            *(f"- pages {s}-{e} ({e - s + 1}pp)" for s, e in gaps),
            "",
            f"## Oversized entries >= {BUNDLE_MIN_PAGES}pp (possible reviewer bundles)",
            "Decide per entry: genuinely ONE document (e.g. a QME report), or a provider",
            "bundle that should be split per encounter (the model splits these per visit).",
            "",
            *(f"- pages {s}-{e} ({e - s + 1}pp)" for s, e in bundles),
            "",
        ]
        out = os.path.join(OUT_DIR, f"{alias}-adjudication.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"{alias}: {len(gaps)} gaps, {len(bundles)} oversized entries -> {out}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        audit()
    elif cmd == "apply":
        apply(sys.argv[2:])
    elif cmd == "sheets":
        sheets()
    else:
        raise SystemExit("usage: repair_labels.py audit | apply ALIAS=K ... | sheets")
