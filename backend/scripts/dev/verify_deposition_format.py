"""Verify the deposition format on ONE real sub-document, printing structure only.

Checks what PR #82 claimed and never proved on real input: three-page grouping, a range opener per
paragraph, and transcript page numbers rather than record page numbers.

PHI: this summarizes a real deposition, so the output is PHI. NOTHING from the model's text is
printed - only counts, the page numbers cited, and boolean shape checks. The one exception is a
single redacted opener per paragraph (the "On pages N to M," prefix with the following words cut),
because the whole question is whether that prefix is present and correctly numbered.

Usage:
    docker exec mrr-api-1 python /app/scripts/dev/verify_deposition_format.py <document_id> <row_start>
"""

import re
import sys

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import ReviewRow
from app.services import catalog
from app.services.deposition_pages import transcript_page_offset
from app.services.summarize_engine import summarize_row

_OPENER = re.compile(r"^\s*(On pages?\s+\d+\s*(?:to|and|-)\s*\d+)", re.IGNORECASE)
_ANY_PAGE = re.compile(r"\bpages?\s+(\d+)", re.IGNORECASE)


def main() -> int:
    # Addressed by the LIVE row's page range, not by a summary index. Two traps otherwise:
    # Summary.idx is not ReviewRow.idx, and a stored summary's row_start/row_end is a SNAPSHOT that
    # goes stale the moment a reviewer edits the boundary - this document's deposition reads 462-498
    # live but 462-463 in its summary, so matching on the snapshot summarizes two pages of a
    # thirty-seven page transcript and quietly proves nothing.
    document_id, start = sys.argv[1], int(sys.argv[2])
    with get_sessionmaker()() as session:
        row_obj = session.scalar(
            select(ReviewRow).where(
                ReviewRow.document_id == document_id, ReviewRow.start == start
            )
        )
        row = row_obj.as_row()
        prompt = catalog.get_prompt(session, "summary", str(row["category"]))
        pdf_path = row_obj.document.stored_path

        print(f"row idx={row_obj.idx} category={row['category']} pages {row['start']}-{row['end']}")
        print(f"pages in sub-document: {int(row['end']) - int(row['start']) + 1}")

        # The offset read, on its own, so a failure here is distinguishable from a format failure.
        offset = transcript_page_offset(pdf_path, row["start"], row["end"])
        print(f"transcript_page_offset -> {offset}")
        if offset is not None:
            print(f"  record page {row['start']} should print as {int(row['start']) + offset}")

        out = summarize_row(pdf_path, row, prompt=prompt)
        body = out["verifiedText"] or out["summaryText"]

        paragraphs = [p for p in body.split("\n") if p.strip()]
        openers = [_OPENER.match(p) for p in paragraphs]
        with_opener = [m for m in openers if m]

        print(f"\nparagraphs: {len(paragraphs)}")
        print(f"paragraphs opening with a page range: {len(with_opener)}/{len(paragraphs)}")

        cited = [int(n) for p in paragraphs for n in _ANY_PAGE.findall(p)]
        if cited:
            print(f"page numbers cited: min {min(cited)}, max {max(cited)}")
            record_lo, record_hi = int(row["start"]), int(row["end"])
            in_record_range = [n for n in cited if record_lo <= n <= record_hi]
            print(
                f"  cited numbers inside the RECORD range {record_lo}-{record_hi}: "
                f"{len(in_record_range)} (want 0 - those would be our page numbers, not the "
                f"transcript's)"
            )

        print("\nopeners only (rest of each paragraph withheld - PHI):")
        for m in with_opener[:14]:
            print(f"  {m.group(1)},")
        if len(with_opener) > 14:
            print(f"  ... {len(with_opener) - 14} more")

        # Grouping: consecutive openers should advance by three.
        starts = [int(_ANY_PAGE.search(m.group(1)).group(1)) for m in with_opener]
        steps = [b - a for a, b in zip(starts, starts[1:])]
        print(f"\nsteps between consecutive paragraph starts: {sorted(set(steps))}")
        print(f"  all steps == 3: {all(s == 3 for s in steps) if steps else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
