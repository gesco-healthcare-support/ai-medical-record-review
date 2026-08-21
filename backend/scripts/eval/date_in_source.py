"""Does the date we put on a row actually appear in that row's pages?

The cheapest date check available, and the one that needs no view on the hard question. Deciding WHICH
of several dates on a page is the encounter date is a judgement call (see the three rows sent on #130:
one where our answer was the labelled Date of Exam and the human's was the print stamp). But a date
that appears NOWHERE in the pages it describes needs no judgement - the field spec says `"d"` is read
from the document and `"-"` when the document states none, and segmentation reads OCR text only, so
there was nothing else to read it from.

Read-only. No model calls, no writes, no human reference needed - it compares the pipeline against its
own input. Prints COUNTS only, never page text.

WHY THE NARROWING MATTERS, and why it is built in rather than left to the reader. Run naively this
reports a third of all dates as missing, and that number is wrong four times over. Measured
2026-08-19 across 1,458 rows on three accounts:

    slashes only ............................. 33.7%  <- what a first pass reports
    + allow - . and spaces as separators ..... 17.6%  <- OCR does not always emit "/"
    + allow "March 11, 2026" .................  6.1%  <- 89 rows, nowhere in the document
    - category 100, never summarized .........  60 rows, reach no deliverable
    = in a category that ships ...............  29 rows = 1.99%
      of which only the DAY differs ..........   5 rows, reported separately, NOT excused
      no trace of the date at all ............  24 rows = 1.6%

Every one of those steps was the CHECK being wrong, not the pipeline. So the buckets are reported
separately and the headline is the last pair, because anyone who quotes the first line overstates the
defect by a factor of twenty.

The day_differs bucket is reported, never discounted. An earlier version of this script called it
`ocr_repair` and treated it as benign - and that buried a real error, a row emitting 06/08 against
text that plainly reads 06/18. The model had a clean date and produced a different one. So the defect
count is a RANGE, and the report prints it that way.

THE DENOMINATOR, corrected 2026-08-21, and it moved the answer. The 1,458-row figure above pools
every uploaded copy of a record, and the same PDF is uploaded more than once as a matter of course:
`documents.sha256` shows 53 documents but only 39 distinct PDFs, and every one of user 5's seven
records is a byte-identical re-upload of user 3's. Two copies of one record are not two samples -
they are the same pages through the same pipeline - so pooling them dilutes the rate with duplicates
of whatever the first copy did.

Deduplicating by sha256 does not leave the number alone. Re-measured the same day on a wider corpus,
coverage having grown as dedup jobs populated more `page_texts`:

    every uploaded copy ....... 25 of 1775 rows absent = 1.4%, day_differs 5   -> 25 to 30
    one copy per distinct PDF . 19 of  954 rows absent = 2.0%, day_differs 1   -> 19 to 20

So one copy per PDF is the DEFAULT here, and `--all-copies` is available for anyone who wants the
pooled figure. Quoting 1.4% understates the rate by about a third, purely from counting seven records
twice.

Coverage is still not the population - 1,920 rows are skipped for incomplete stored page text, and
that count falls every time a dedup pass stores more pages. Re-run after one rather than treating any
single run as final.

    docker compose exec -T -e PYTHONPATH=/app api python scripts/eval/date_in_source.py [--user N]
        [--all-copies]
"""

from __future__ import annotations

import argparse
import re
from collections import Counter

_MONTHS = (
    "january february march april may june july august september october november december".split()
)

# Buckets, worst last. `absent` is the only one that is unambiguously the pipeline's fault.
IN_ROW = "in_row"
WITHIN_MARGIN = "within_2_pages"
ELSEWHERE = "elsewhere_in_doc"
DAY_DIFFERS = "day_differs"
ABSENT = "absent"
NOT_DELIVERED = "wrong_but_never_shipped"
NO_DATE = "no_date_to_check"
ORDER = (IN_ROW, WITHIN_MARGIN, ELSEWHERE, DAY_DIFFERS, ABSENT, NOT_DELIVERED, NO_DATE)


def date_patterns(date_str: str) -> dict[str, re.Pattern] | None:
    """Regexes matching ``MM/DD/YYYY`` in the forms OCR actually produces, or None if unparseable.

    Four of them, and each exists because it changed the answer:

      strict     slashes only - what a naive check uses, and what over-reports by 2x
      loose      any of / - . with optional spaces, which is most of that 2x
      spelled    "March 11, 2026" - another 1.5x on top
      month_only same month and year, ANY day. Reported as its own bucket because the day being
                 the ONLY disagreement has two readings and this check cannot tell them apart: the
                 model repairing a smudged digit (correct), or the model emitting a different day from
                 a perfectly clean one (a defect). Named for what it OBSERVES, not for either reading -
                 an earlier version called it `ocr_repair` and that buried a real error: 06/08 emitted
                 against a row whose text plainly reads 06/18, which is a corruption rather than a
                 repair. Read this bucket, do not discount it.
    """
    parts = (date_str or "").split("/")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    m, d, y = (p.lstrip("0") or "0" for p in parts)
    if not (1 <= int(m) <= 12 and 1 <= int(d) <= 31) or len(parts[2]) != 4:
        return None
    yr = f"({parts[2]}|{parts[2][-2:]})"
    sep = r"\s?[/.-]\s?"
    return {
        "strict": re.compile(rf"(^|[^0-9])0?{m}/0?{d}/{yr}([^0-9]|$)"),
        "loose": re.compile(rf"(^|[^0-9])0?{m}{sep}0?{d}{sep}{yr}([^0-9]|$)"),
        "spelled": re.compile(rf"{_MONTHS[int(m) - 1]}\s+0?{d},?\s+{parts[2]}", re.I),
        "month_only": re.compile(rf"(^|[^0-9])0?{m}{sep}[0-9]{{1,2}}{sep}{yr}([^0-9]|$)"),
    }


def _found(patterns: dict[str, re.Pattern], text: str, keys=("loose", "spelled")) -> bool:
    return any(patterns[k].search(text or "") for k in keys)


def classify_date(
    date_str: str,
    *,
    row_text: str,
    margin_text: str,
    doc_text: str,
    summarized: bool,
) -> str:
    """Which bucket this row's date falls in. Pure, so the narrowing itself is testable.

    ``margin_text`` should cover the row's pages PLUS a couple either side: a date found only there is
    a segmentation boundary landing a page late, which is a different defect from an invented date and
    must not be reported as one. ``summarized`` is False for a category excluded by default, where a
    wrong date reaches no deliverable.
    """
    patterns = date_patterns(date_str)
    if patterns is None:
        # "-" is the CORRECT answer for a document that states no date, and an unparseable value is
        # not a date claim at all. Neither is evidence about this check, so they get their own bucket
        # rather than sitting with rows that carry a wrong date - conflating the two hides both.
        return NO_DATE
    if _found(patterns, row_text):
        return IN_ROW
    if _found(patterns, margin_text):
        return WITHIN_MARGIN
    if _found(patterns, doc_text):
        return ELSEWHERE
    if not summarized:
        return NOT_DELIVERED
    if patterns["month_only"].search(row_text or ""):
        return DAY_DIFFERS
    return ABSENT


def summarise(buckets) -> str:
    """The narrowing, as a report. Headline is the LAST line, deliberately."""
    counts = Counter(buckets)
    total = sum(counts.values())
    if not total:
        return "no rows checked"
    lines = [f"{'bucket':<24}{'rows':>6}{'share':>8}"]
    lines.append("-" * 38)
    for name in ORDER:
        n = counts.get(name, 0)
        lines.append(f"{name:<24}{n:>6}{100.0 * n / total:>7.1f}%")
    absent = counts.get(ABSENT, 0)
    day = counts.get(DAY_DIFFERS, 0)
    lines.append("-" * 38)
    lines.append(f"{'checked':<24}{total:>6}")
    lines.append("")
    lines.append(
        f"HEADLINE: {absent} of {total} rows ({100.0 * absent / total:.1f}%) carry a date with no "
        f"trace in their document, plus {day} where only the DAY differs"
    )
    lines.append(
        "  Both are in categories that ship. Quote this pair, not the first line: the buckets above\n"
        "  are what a naive check miscounts as defects - separators OCR renders differently,\n"
        "  spelled-out months, boundary-adjacent pages, and rows nobody summarizes.\n"
        "  day_differs is NOT a free pass. It can be the model repairing a smudged digit, or the model\n"
        "  emitting a different day from a clean one. This check cannot tell those apart, so the true\n"
        f"  defect count is between {absent} and {absent + day}."
    )
    return "\n".join(lines)


def one_copy_per_pdf(pairs):
    """Keep the rows of ONE document per distinct `sha256`; -> (kept, rows dropped, docs dropped).

    THE DENOMINATOR PROBLEM this exists for. `documents.sha256` is indexed and not unique, and the
    same PDF is uploaded more than once as a matter of course - re-running a case is legitimate, and
    the upload warning is scoped to one user (`api/documents.py`), so a second account re-uploading
    the same record is not warned at all. Measured 2026-08-21: 53 documents, 39 distinct PDFs, and
    every one of user 5's seven records was a byte-identical re-upload of user 3's.

    Any rate pooled across accounts therefore counts those records twice, and the copies are not
    independent samples - they are the same pages through the same pipeline. That does not
    necessarily move a percentage much, but it makes the denominator a number nobody can interpret.

    Earliest copy wins, so the figure is stable as further copies are uploaded.
    """
    documents = {doc.id: doc for _row, doc in pairs}
    by_sha: dict[str, object] = {}
    for doc in documents.values():
        winner = by_sha.get(doc.sha256)
        # (created_at, id): earliest copy wins, id breaks a same-timestamp tie so the choice is
        # deterministic. `created_at` is NOT NULL on documents, so no null case to carry.
        if winner is None or (doc.created_at, doc.id) < (winner.created_at, winner.id):
            by_sha[doc.sha256] = doc
    keep_ids = {doc.id for doc in by_sha.values()}
    kept = [(row, doc) for row, doc in pairs if doc.id in keep_ids]
    return kept, len(pairs) - len(kept), len(documents) - len(keep_ids)


def main() -> None:  # pragma: no cover - I/O wrapper around the tested functions above
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", type=int, default=None, help="restrict to one owner id")
    ap.add_argument("--margin", type=int, default=2, help="pages either side counted as adjacent")
    ap.add_argument(
        "--all-copies",
        action="store_true",
        help="count every uploaded copy of a record, not one per distinct PDF (see DENOMINATOR)",
    )
    args = ap.parse_args()

    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Document, ReviewRow
    from app.services import catalog

    maker = get_sessionmaker()
    with maker() as session:
        q = select(ReviewRow, Document).join(Document, Document.id == ReviewRow.document_id)
        if args.user is not None:
            q = q.where(Document.user_id == args.user)
        pairs = session.execute(q).all()
        if not args.all_copies:
            pairs, duplicate_rows, duplicate_docs = one_copy_per_pdf(pairs)
            if duplicate_docs:
                print(
                    f"excluded {duplicate_rows} row(s) in {duplicate_docs} redundant copy/copies of "
                    f"a record already counted (--all-copies to include them)\n"
                )

        # One page-text read per document, not per row: a 2,600-page record has hundreds of rows.
        texts: dict[str, dict[int, str]] = {}
        buckets, skipped = [], 0
        for row, doc in pairs:
            if doc.id not in texts:
                from sqlalchemy import text as sql_text

                got = session.execute(
                    sql_text("select page, text from page_texts where document_id=:d"),
                    {"d": doc.id},
                ).all()
                texts[doc.id] = {p.page: (p.text or "") for p in got}
            pages = texts[doc.id]
            span = range(int(row.start), int(row.end) + 1)
            # A row with any page missing is NOT checkable: the date could be on the page we lack.
            if not pages or any(p not in pages for p in span):
                skipped += 1
                continue
            summarized = catalog.summarize_default_for(session, row.category)
            buckets.append(
                classify_date(
                    row.date,
                    row_text="\n".join(pages[p] for p in span),
                    margin_text="\n".join(
                        pages[p]
                        for p in range(row.start - args.margin, row.end + args.margin + 1)
                        if p in pages
                    ),
                    doc_text="\n".join(pages[p] for p in sorted(pages)),
                    summarized=summarized,
                )
            )

    print(summarise(buckets))
    print(f"\nskipped {skipped} row(s) with incomplete stored page text - not checkable either way")


if __name__ == "__main__":  # pragma: no cover
    main()
