"""Content-similarity duplicate detection that RE-OCRs sub-docs (source_text is empty in DB).

ARROYO's summaries have no stored source_text, so dedup_detect.py (which reads it) can't run.
This script re-OCRs each summary row's page range with the CURRENT Tesseract (the exact app path,
extract_text_from_selected_pages), caching each page once so no page is OCR'd twice. It then:
  - clusters rows by word-set Jaccard (candidate re-scan groups), and
  - for each cluster reports the pairwise char-level difflib ratio range, so true re-scans
    (difflib high) are distinguishable from same-template-different-content (difflib lower).

The per-page OCR cache is written to disk so the A/B test can reuse the "normal tesseract" text
without re-OCRing. No DB writes. Serial, no Gemini -> no 429/ADC concern.

Run: python /tmp/dedup_reocr.py --doc <id> --pdf /tmp/arroyo_full.pdf --out /tmp/out/dedup
"""

import argparse
import difflib
import gc
import json
import os
import re
import time

from app.db import get_sessionmaker
from app.models import Summary
from app.services.ocr import extract_text_from_selected_pages


def sig(text):
    return set(re.findall(r"[a-z0-9]{2,}", (text or "").lower()))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", default="/tmp/out/dedup")
    ap.add_argument("--thresholds", default="0.6,0.7,0.8")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    with get_sessionmaker()() as s:
        rows = (
            s.query(Summary)
            .filter(Summary.document_id == args.doc)
            .order_by(Summary.idx)
            .all()
        )
        items = [
            {
                "idx": r.idx,
                "start": r.row_start,
                "end": r.row_end,
                "title": (r.effective_title() or "")[:45],
                "date": r.effective_date(),
            }
            for r in rows
        ]
    print(f"rows={len(items)} pages spanned={min(i['start'] for i in items)}-{max(i['end'] for i in items)}", flush=True)

    # OCR each unique page once (current Tesseract, app path), lean per-page.
    all_pages = sorted({p for it in items for p in range(it["start"], it["end"] + 1)})
    print(f"unique pages to OCR: {len(all_pages)}", flush=True)
    cache = {}
    t0 = time.time()
    for n, p in enumerate(all_pages, 1):
        cache[p] = extract_text_from_selected_pages(args.pdf, [p])
        if n % 20 == 0 or n == len(all_pages):
            rate = (time.time() - t0) / n
            print(f"  OCR {n}/{len(all_pages)} pages ({rate:.1f}s/pg, ~{rate*(len(all_pages)-n)/60:.1f}min left)", flush=True)
        gc.collect()

    # Assemble per-row text and signatures.
    for it in items:
        it["text"] = "".join(cache[p] for p in range(it["start"], it["end"] + 1))
        it["chars"] = len(it["text"])
        it["sig"] = sig(it["text"])

    # Persist the page cache (reusable for the A/B "normal tesseract" arm).
    with open(f"{args.out}_pages.json", "w", encoding="utf-8") as f:
        json.dump({str(p): cache[p] for p in all_pages}, f)
    with open(f"{args.out}_rows.json", "w", encoding="utf-8") as f:
        json.dump([{k: it[k] for k in ("idx", "start", "end", "title", "date", "chars", "text")} for it in items], f)

    thresholds = [float(t) for t in args.thresholds.split(",")]
    for thr in thresholds:
        parent = list(range(len(items)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                sim = jaccard(items[i]["sig"], items[j]["sig"])
                if sim >= thr:
                    pairs.append((i, j, sim))
                    parent[find(i)] = find(j)

        clusters = {}
        for k in range(len(items)):
            clusters.setdefault(find(k), []).append(k)
        dupe = sorted((c for c in clusters.values() if len(c) > 1), key=len, reverse=True)
        total = sum(len(c) for c in dupe)
        print(f"\n===== threshold {thr}: {len(dupe)} clusters covering {total} sub-docs =====", flush=True)
        for cn, c in enumerate(dupe, 1):
            # char-level difflib range within the cluster: true re-scans stay high.
            dr = []
            for a in range(len(c)):
                for b in range(a + 1, len(c)):
                    dr.append(difflib.SequenceMatcher(None, items[c[a]]["text"], items[c[b]]["text"]).ratio())
            jr = [p[2] for p in pairs if find(p[0]) == find(c[0])]
            print(f"--- cluster {cn} ({len(c)} copies) jaccard={min(jr):.2f}-{max(jr):.2f} "
                  f"difflib={min(dr):.2f}-{max(dr):.2f} ---", flush=True)
            for k in c:
                it = items[k]
                print(f"    idx={it['idx']} pages={it['start']}-{it['end']} date={it['date']} "
                      f"chars={it['chars']} | {it['title']}", flush=True)

    print(f"\nwrote {args.out}_pages.json + {args.out}_rows.json", flush=True)


if __name__ == "__main__":
    main()
