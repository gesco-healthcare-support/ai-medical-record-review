"""Measure the CURRENT production pipeline against the gold labels (evidence, not guesses).

Faithfully reproduces app.py /getPages:
  - split the input PDF into 100-page chunks,
  - upload each chunk to Gemini (gemini-flash-latest, the SAME prompt + config),
  - parse the JSON, re-base page numbers per chunk,
  - categorise each title with the SAME difflib/0.65 matcher (groups.py).
Then scores predicted (start, end, category) against the gold CSVs:
  - SEGMENTATION: boundary precision/recall/F1 and exact-span Document-F1.
  - CATEGORISATION: of documents the pipeline segmented exactly right, how many
    got the right category (measurable only for the 3 hand-CSV cases).

Run:  python src/measure_current.py "Case 1" "Case 2" "Case 3"
"""
import os
import sys
import re
import csv
import ast
import json
import time
import tempfile

import numpy as np
import fitz
from difflib import SequenceMatcher
from PyPDF2 import PdfReader, PdfWriter
import google.generativeai as genai

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"P:\MRR_AI_Source\mrr-line_source")  # groups.py lives with the app
from groups import groups
from cases import by_id, CSV_CASE_IDS
from pipeline import prf, doc_f1

APP = r"P:\MRR_AI_Source\mrr-line_source\app.py"


def _read_from_app(pattern, after):
    with open(APP, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if pattern in s and s.startswith(after):
                return ast.literal_eval(s[len(after):])
    raise SystemExit(f"could not extract {after!r} from app.py")


def read_key():
    with open(APP, encoding="utf-8") as f:
        for line in f:
            m = re.search(r'genai\.configure\(api_key="([^"]+)"\)', line)
            if m:
                return m.group(1)
    raise SystemExit("no Gemini key in app.py")


# --- the exact difflib categoriser from app.py ---
def _norm(t):
    return re.sub(r"[^a-zA-Z0-9\s]", "", t).strip().lower()


def categorize(title, threshold=0.65):
    if not isinstance(title, str):
        title = "Unknown"
    nt = _norm(title)
    best, best_group = 0.0, None
    for group, docs in groups.items():
        for d in docs:
            s = SequenceMatcher(None, nt, _norm(d)).ratio()
            if s > best and s >= threshold:
                best, best_group = s, group
    return best_group if best_group else "100"


genai.configure(api_key=read_key())
PROMPT = _read_from_app("Title: Extract", "prompt=")
GEN_CFG = {"temperature": 1.5, "top_p": 0.95, "top_k": 40, "response_mime_type": "text/plain"}
MODEL = genai.GenerativeModel(
    "gemini-flash-latest", generation_config=GEN_CFG,
    system_instruction="You are an assistant that segments a large document into subdocuments and provide their metadata.")


def upload_wait(path):
    f = genai.upload_file(path, mime_type="application/pdf")
    while f.state.name == "PROCESSING":
        time.sleep(2)
        f = genai.get_file(f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"upload failed: {f.state.name}")
    return f


def chunk_pdf(pdf, size=100):
    reader = PdfReader(pdf)
    n = len(reader.pages)
    tmp = tempfile.mkdtemp()
    chunks = []
    for i in range(0, n, size):
        w = PdfWriter()
        for p in range(i, min(i + size, n)):
            w.add_page(reader.pages[p])
        out = os.path.join(tmp, f"chunk_{i // size:02d}.pdf")
        with open(out, "wb") as fh:
            w.write(fh)
        chunks.append(out)
    return chunks


def segment_case(pdf):
    """Reproduce /getPages: returns list of (start, end, category) predictions."""
    preds, offset, parse_fails = [], 0, 0
    for cf in chunk_pdf(pdf, 100):
        fobj = upload_wait(cf)
        chat = MODEL.start_chat(history=[{"role": "user", "parts": [fobj]}])
        txt = chat.send_message(PROMPT).text.replace("```json", "").replace("```", "").strip()
        try:
            items = json.loads(txt)
        except Exception:
            parse_fails += 1
            offset += 100
            continue
        for it in items:
            try:
                s, e = int(it["s"]), int(it["e"])
            except (KeyError, ValueError, TypeError):
                continue
            title = str(it.get("t", it.get("title", ""))).strip()
            preds.append((s + offset, e + offset, categorize(title)))
        offset += 100
    return preds, parse_fails


def gold(case_id):
    c = by_id(case_id)
    n = fitz.open(c["pdf"]).page_count
    spans, cat = [], {}
    with open(c["label_csv"], encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip().isdigit() and row[1].strip().isdigit():
                s, e = int(row[0]), int(row[1])
                spans.append((s, e))
                if len(row) >= 3 and row[2].strip().isdigit():
                    cat[(s, e)] = row[2].strip()
    return n, spans, cat


def main(case_ids):
    seg_rows, cat_hit, cat_tot = [], 0, 0
    for cid in case_ids:
        n, gspans, gcat = gold(cid)
        print(f"\n=== {cid}: {n} pages, {len(gspans)} gold docs -> running Gemini ...", flush=True)
        preds, pf = segment_case(by_id(cid)["pdf"])
        gstarts = set(s for s, _ in gspans)
        pstarts = set(s for s, _, _ in preds)
        yt = np.array([1 if p in gstarts else 0 for p in range(1, n + 1)])
        yp = np.array([1 if p in pstarts else 0 for p in range(1, n + 1)])
        bp, br, bf = prf(yt, yp)
        df = doc_f1([(s, e) for s, e, _ in preds], gspans)
        seg_rows.append((cid, len(gspans), len(preds), bp, br, bf, df, pf))
        # categorisation on exactly-segmented docs
        pcat = {(s, e): c for s, e, c in preds}
        for span, gc in gcat.items():
            if span in pcat:
                cat_tot += 1
                cat_hit += (str(pcat[span]) == str(gc))
        print(f"    boundary P/R/F1 = {bp:.2f}/{br:.2f}/{bf:.2f} | Doc-F1 = {df:.2f} "
              f"| pred docs = {len(preds)} | parse-fails = {pf}", flush=True)

    print("\n================ CURRENT PIPELINE vs GOLD ================")
    print(f"{'case':14}{'gold':>5}{'pred':>5}{'bP':>6}{'bR':>6}{'bF1':>6}{'DocF1':>7}{'pf':>4}")
    for cid, g, p, bp, br, bf, df, pf in seg_rows:
        print(f"{cid[:14]:14}{g:>5}{p:>5}{bp:>6.2f}{br:>6.2f}{bf:>6.2f}{df:>7.2f}{pf:>4}")
    if cat_tot:
        print(f"\nCATEGORISATION (on exactly-segmented docs): {cat_hit}/{cat_tot} "
              f"= {100*cat_hit/cat_tot:.0f}% correct category")
    else:
        print("\nCATEGORISATION: no exactly-segmented docs to score "
              "(segmentation too far off to isolate the categoriser).")


if __name__ == "__main__":
    main(sys.argv[1:] or CSV_CASE_IDS)
