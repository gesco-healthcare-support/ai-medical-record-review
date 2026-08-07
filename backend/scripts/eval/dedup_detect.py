"""Content-similarity duplicate detection (finds true re-scans that exact date+title misses).

Loads each summary's stored source_text (the OCR text), computes pairwise word-set Jaccard, and
clusters near-identical sub-documents (true scanned copies). Prints clusters with size >= 2 so we
can pick real duplicate groups for the A/B test - and it's the detection logic Phase 2 will use.
No DB writes.

Run: python /tmp/dedup_detect.py --doc <id> --threshold 0.65
"""

import argparse
import re

from app.db import get_sessionmaker
from app.models import Summary


def sig(text):
    return set(re.findall(r"[a-z0-9]{2,}", (text or "").lower()))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--threshold", type=float, default=0.65)
    args = ap.parse_args()

    with get_sessionmaker()() as s:
        rows = s.query(Summary).filter(Summary.document_id == args.doc).order_by(Summary.idx).all()
        items = []
        for r in rows:
            items.append({
                "idx": r.idx, "pages": f"{r.row_start}-{r.row_end}",
                "title": (r.effective_title() or "")[:45], "date": r.effective_date(),
                "sig": sig(r.source_text), "chars": len(r.source_text or "")
            })
    have_text = sum(1 for it in items if it["sig"])
    print(f"summaries={len(items)} with_source_text={have_text} threshold={args.threshold}")
    if not have_text:
        print("NO source_text stored - would need to re-OCR to detect by content.")
        return

    # union-find over pairs above the threshold
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
            if sim >= args.threshold:
                pairs.append((i, j, round(sim, 2)))
                parent[find(i)] = find(j)

    clusters = {}
    for k in range(len(items)):
        clusters.setdefault(find(k), []).append(k)
    dupe_clusters = [c for c in clusters.values() if len(c) > 1]
    dupe_clusters.sort(key=len, reverse=True)

    total_dupe_docs = sum(len(c) for c in dupe_clusters)
    print(f"\nDUPLICATE CLUSTERS (content-similarity): {len(dupe_clusters)} clusters covering "
          f"{total_dupe_docs} sub-documents (vs 5 exact date+title groups)\n")
    for n, c in enumerate(dupe_clusters, 1):
        print(f"--- cluster {n} ({len(c)} copies) ---")
        for k in c:
            it = items[k]
            print(f"    idx={it['idx']} pages={it['pages']} date={it['date']} "
                  f"chars={it['chars']} | {it['title']}")
        # show intra-cluster min similarity
        sims = [p[2] for p in pairs if find(p[0]) == find(c[0])]
        if sims:
            print(f"    similarity range: {min(sims)}-{max(sims)}")


if __name__ == "__main__":
    main()
