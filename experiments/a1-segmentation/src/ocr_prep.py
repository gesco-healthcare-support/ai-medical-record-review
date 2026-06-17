"""Step 1: OCR each case's input PDF into per-page text, cached as JSONL.

Reads the case registry (src/cases.py). Scanned PDFs have no text layer, so each
page is rendered with pdf2image (Poppler) and OCR'd with Tesseract. Rendering is
done in small page BATCHES (memory-bounded, shows progress, resumable). Output:
cache/ocr/<case_id>.jsonl with {"page": int, "text": str} per line.

Run:  python src/ocr_prep.py                 # all cases (skips already-cached)
      python src/ocr_prep.py "Manual Case 2" # specific case ids
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF, fast page count
import pytesseract
from pdf2image import convert_from_path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cases import CASES, by_id
from config import OCR_CACHE, OCR_THREADS, TESSERACT_CMD

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

DPI = 150
BATCH = 10


def done_pages(out_path):
    """Page numbers already OCR'd (for resume)."""
    if not os.path.exists(out_path):
        return set()
    done = set()
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["page"])
            except Exception:
                pass
    return done


def ocr_case(case, dpi=DPI, workers=OCR_THREADS, batch=BATCH):
    os.makedirs(OCR_CACHE, exist_ok=True)
    cid, pdf = case["id"], case["pdf"]
    out = os.path.join(OCR_CACHE, f"{cid}.jsonl")
    n = fitz.open(pdf).page_count
    already = done_pages(out)
    if len(already) >= n:
        print(f"[skip] {cid}: {n} pages already cached")
        return out

    todo = [p for p in range(1, n + 1) if p not in already]
    print(f"[start] {cid}: {n} pages, {len(todo)} to do @ {dpi} DPI", flush=True)
    t0 = time.time()
    processed = 0
    with open(out, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), batch):
            lo, hi = todo[i], todo[min(i + batch - 1, len(todo) - 1)]
            images = convert_from_path(pdf, dpi=dpi, first_page=lo, last_page=hi)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                texts = list(ex.map(pytesseract.image_to_string, images))
            for offset, text in enumerate(texts):
                f.write(json.dumps({"page": lo + offset, "text": text}) + "\n")
            f.flush()
            processed += len(texts)
            rate = processed / max(time.time() - t0, 1e-6)
            eta = (len(todo) - processed) / max(rate, 1e-6)
            print(f"  {cid}: {processed}/{len(todo)} ({rate:.1f} pg/s, ETA {eta:.0f}s)", flush=True)
    print(f"[done] {cid}: {n} pages in {time.time()-t0:.0f}s -> {out}", flush=True)
    return out


if __name__ == "__main__":
    ids = sys.argv[1:]
    targets = [by_id(i) for i in ids] if ids else CASES
    for c in targets:
        ocr_case(c)
    print("OCR_PREP_COMPLETE")
