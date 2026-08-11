"""Read-only: min-pairwise difflib ratio at 1500 / 4000 / full chars on REAL candidate clusters.

Candidates come from the app's own cluster_rows (Jaccard union-find). Prints sizes, scores, timings
only - never document text.
"""

import difflib
import itertools
import time
from collections import defaultdict

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ReviewRow
from app.services.dedup import cluster_rows


def min_ratio(texts, limit):
    cut = [(t or "")[:limit] if limit else (t or "") for t in texts]
    started = time.perf_counter()
    ratios = [difflib.SequenceMatcher(None, a, b).ratio() for a, b in itertools.combinations(cut, 2)]
    return (round(min(ratios), 3) if ratios else 1.0), int((time.perf_counter() - started) * 1000)


with get_sessionmaker()() as session:
    rows = session.scalars(
        select(ReviewRow).where(ReviewRow.source_text.isnot(None)).order_by(ReviewRow.document_id)
    ).all()
    by_doc = defaultdict(list)
    for row in rows:
        if (row.source_text or "").strip():
            by_doc[row.document_id].append(row)
    print(f"docs with OCR text: {len(by_doc)}; rows: {sum(len(v) for v in by_doc.values())}")
    print(f"{'members':>7} {'avg chars':>9} | {'1500':>12} {'4000':>12} {'full':>12}")
    found = 0
    for doc_id, doc_rows in by_doc.items():
        items = [{"id": r.id, "text": r.source_text or ""} for r in doc_rows]
        for cluster in cluster_rows(items):
            members = cluster["members"]
            texts = [m["text"] for m in members]
            avg = sum(len(t) for t in texts) // len(texts)
            cells = []
            for limit in (1500, 4000, None):
                score, ms = min_ratio(texts, limit)
                cells.append(f"{score:>6} {ms:>4}ms")
            print(f"{len(members):>7} {avg:>9} | {' '.join(cells)}")
            found += 1
    print(f"candidate clusters measured: {found}")
