"""Convert "Record of Records" (ROR) linked PDFs into our label CSV format.

8 of our 11 cases have no hand-typed CSV, but they DO have a human-made
".ror-linked.pdf": the medical-record-review summary, where each document entry
is a hyperlink pointing to that document's START page in the original records
PDF. Extracting those link targets and sorting them gives the document
boundaries -- the exact A1 ground truth -- for free.

Output: one cache/ror_labels/<case>.csv with rows  start,end,category,-,-,-
(category left blank: ROR links give boundaries reliably; category mapping is a
separate, lower-confidence step we do not need for A1).

Run:  python src/ror_to_csv.py
"""
import os
import sys
import csv
import glob

import fitz  # PyMuPDF

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import EXP_ROOT

ROR_LABELS = os.path.join(EXP_ROOT, "cache", "ror_labels")

# (case_label, case_directory). Case labels stay stable across the project.
ROR_CASES = []
_FOLDER1 = r"P:\MRR_AI_Source\medical record and review samples"
_MANUAL = r"P:\MRR_AI_Source\MR Samples\Manual Summarization Samples"
for d in sorted(glob.glob(os.path.join(_FOLDER1, "*"))):
    if os.path.isdir(d):
        ROR_CASES.append((os.path.basename(d), d))
for d in sorted(glob.glob(os.path.join(_MANUAL, "*"))):
    if os.path.isdir(d):
        ROR_CASES.append(("Manual " + os.path.basename(d), d))


def find_input_pdf(case_dir):
    """The input records PDF = the longest PDF that is not an output artifact."""
    skip = ("ror-linked", "ror-unlinked", "diagnostic", "op reports",
            "operative", "deposition", "memo")
    best, best_n = None, -1
    for p in glob.glob(os.path.join(case_dir, "*.pdf")):
        low = os.path.basename(p).lower()
        if any(s in low for s in skip):
            continue
        try:
            n = fitz.open(p).page_count
        except Exception:
            continue
        if n > best_n:
            best, best_n = p, n
    return best, best_n


def find_ror_pdf(case_dir):
    hits = glob.glob(os.path.join(case_dir, "*ror-linked*.pdf"))
    return hits[0] if hits else None


def extract_start_pages(ror_pdf):
    """Collect the target page of every GOTO/GOTOR link in the ROR (1-based)."""
    starts = set()
    doc = fitz.open(ror_pdf)
    for page in doc:
        for lk in page.get_links():
            tgt = lk.get("page", -1)
            # fitz returns int (0-based) for some PDFs, a numeric string
            # (1-based page label) for others. Normalise both to a 1-based page.
            if isinstance(tgt, int) and tgt >= 0:
                starts.add(tgt + 1)
            elif isinstance(tgt, str) and tgt.strip().isdigit():
                starts.add(int(tgt.strip()))
    return sorted(starts)


def starts_to_rows(starts, n_pages):
    """Turn sorted start pages into (start, end) rows; end = next start - 1."""
    rows = []
    for i, s in enumerate(starts):
        e = (starts[i + 1] - 1) if i + 1 < len(starts) else n_pages
        rows.append((s, e))
    return rows


def convert_case(label, case_dir):
    os.makedirs(ROR_LABELS, exist_ok=True)
    inp, n = find_input_pdf(case_dir)
    ror = find_ror_pdf(case_dir)
    if not inp or not ror:
        return dict(case=label, status="MISSING input or ROR", n_pages=n,
                    n_docs=0, max_start=0, in_range=False)
    starts_all = extract_start_pages(ror)
    starts = [s for s in starts_all if 1 <= s <= n]  # drop out-of-range tail links
    dropped = len(starts_all) - len(starts)
    rows = starts_to_rows(starts, n)
    out = os.path.join(ROR_LABELS, f"{label}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for s, e in rows:
            w.writerow([s, e, "", "-", "-", "-"])
    return dict(case=label, status="ok", n_pages=n, n_docs=len(rows),
                dropped=dropped,
                min_start=(min(starts) if starts else 0),
                max_start=(max(starts) if starts else 0),
                in_range=(dropped == 0), out=out)


if __name__ == "__main__":
    print(f"{'case':40} {'pages':>6} {'docs':>5} {'dropped':>8}")
    print("-" * 66)
    total_docs = 0
    for label, d in ROR_CASES:
        r = convert_case(label, d)
        total_docs += r.get("n_docs", 0)
        print(f"{label[:40]:40} {r['n_pages']:>6} {r['n_docs']:>5} "
              f"{r.get('dropped', 0):>8}  {r['status']}")
    print(f"\n{len(ROR_CASES)} ROR cases converted, {total_docs} boundaries total "
          f"(out-of-range tail links dropped).")
