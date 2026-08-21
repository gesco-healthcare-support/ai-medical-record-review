"""Which encounters did the human write up that our deliverable has nothing for?

Option 1 from #130, and the companion to `date_in_source.py`. That script compares the pipeline
against its OWN input and needs no human reference; this one compares it against the finished report
a reviewer wrote for the same record, which is the only thing that can say we MISSED an encounter
rather than merely mis-dated one.

Read-only. No model calls, no writes. Prints COUNTS, dates, page ranges and category ids - never a
title, never page text, never a line of the human report.

THE DEFINITION THIS MEASURES AGAINST, settled 2026-08-21: the encounter date is the date the
encounter the document is about actually happened - when the MRI was performed, when the visit
occurred. For a document that IS the encounter rather than a record of one, its creation date. Never
the date of injury, and never any other date the page happens to carry.

WHY DATES AND NOT TITLES. Dates are the one field both sides carry: every human entry is a date-led
line, and every row of ours has a `date`. A title comparison would need the two to word a document
the same way, which they do not - measured across two runs of one PDF, only 49.7% of rows starting on
the same page got the same generated title.

FOUR BUCKETS, and only two of them are defects:

    delivered   the human has an entry on this date and we deliver a row carrying it
    excluded    we have a row with this date but it is not included - content we hold and drop
    absent      no row of ours carries this date at all
    extra       we deliver a date the human has no entry for

`extra` IS EXPECTED TO BE NOISY and is not a defect count. The human writes one entry per ENCOUNTER
and we write one summary per DOCUMENT, so a visit that produced a work status report, a progress
report and office notes is one human entry and up to three rows of ours. Measured across six records,
the human runs 1.09 to 1.53 entries per distinct date and we run 1.16 to 2.28 - the same order, so
the convention gap is real but small.

`absent` SPLITS FURTHER, and this is the part worth having. A date we do not carry is two very
different things, and they look identical in a count:

    re-dated   the date IS in the document's OCR. We have the pages and deliver them; we put a
               different date on the row. A labelling disagreement, not lost content.
    unfound    the date appears nowhere in the OCR. Either the reviewer read something the scan
               dropped, or they inferred it.

Measured on six records before this split existed, "no row carries this date" came to 11 encounters
and read as 11 missed documents. Ten of the eleven were re-dated - the content was delivered under a
different date - and one was unfound. Reporting the raw count without the split overstates lost
content by an order of magnitude, which is exactly the mistake `date_in_source.py`'s narrowing exists
to prevent on the other axis.

    docker compose exec -T -e PYTHONPATH=/app api python scripts/eval/date_vs_human_entries.py \
        --document <id> --human /tmp/03_human_summary.txt

The human report is PHI. Copy it into the container for the run and delete it afterwards; never into
the repo, and never into `uploads/`.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter

# A human entry is a date-led line: "MM/DD/YY<TAB>AUTHOR<TAB>...". Anchored on the tab so a date
# mentioned mid-sentence in a summary body is not mistaken for the start of an entry.
HUMAN_ENTRY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})\t")

DELIVERED = "delivered"
EXCLUDED = "excluded"
RE_DATED = "absent_but_re_dated"
UNFOUND = "absent_and_unfound"
EXTRA = "extra"
ORDER = (DELIVERED, EXCLUDED, RE_DATED, UNFOUND, EXTRA)

_MONTHS = (
    "january february march april may june july august september october november december".split()
)


def normalise(month: str, day: str, year: str) -> str:
    """-> MM/DD/YYYY. A two-digit year is 20xx; these records do not predate 2000."""
    y = int(year)
    return f"{int(month):02d}/{int(day):02d}/{y + 2000 if y < 100 else y}"


def human_entry_dates(text: str) -> set[str]:
    """Every date the human report has an entry for, as MM/DD/YYYY."""
    return {
        normalise(*match.groups())
        for match in (HUMAN_ENTRY.match(line) for line in text.splitlines())
        if match
    }


def appears_in_text(date: str, text: str) -> bool:
    """Is this date written anywhere in `text`, in the forms OCR actually produces?

    Deliberately the same tolerance as date_in_source.date_patterns - slashes, any of / - . with
    optional spaces, and the spelled-out month. A slashes-only test reports about twice as many
    dates missing as are really missing, which is the single biggest way this measurement goes wrong.
    """
    try:
        month, day, year = (int(part) for part in date.split("/"))
    except ValueError:
        return False
    separator = r"[/\-.\s]{1,3}"
    short = str(year)[2:]
    numeric = rf"\b0?{month}{separator}0?{day}{separator}(?:{year}|{short})\b"
    spelled = rf"\b{_MONTHS[month - 1]}\.?\s+0?{day}(?:st|nd|rd|th)?,?\s+{year}\b"
    return bool(re.search(numeric, text) or re.search(spelled, text, re.I))


def classify_entries(human_dates, rows, document_text):
    """Bucket every human date and every date we deliver.

    `rows` is [(date, included, start, end)] - the date string as stored, whether the reviewer's
    include box is set, and the page span. `document_text` is the whole document's OCR, used only to
    split `absent` into re-dated and unfound.
    """
    delivered_dates = {date for date, included, _s, _e in rows if included and date != "-"}
    our_dates = {date for date, _i, _s, _e in rows if date != "-"}

    buckets: Counter = Counter()
    detail: dict[str, list] = {key: [] for key in ORDER}
    for date in sorted(human_dates):
        if date in delivered_dates:
            key = DELIVERED
        elif date in our_dates:
            key = EXCLUDED
        elif appears_in_text(date, document_text):
            key = RE_DATED
        else:
            key = UNFOUND
        buckets[key] += 1
        detail[key].append(date)
    for date in sorted(delivered_dates - human_dates):
        buckets[EXTRA] += 1
        detail[EXTRA].append(date)
    return buckets, detail


def summarise(buckets, detail, rows, human_dates) -> str:
    """The report. `absent` is never printed as one number - see the module docstring."""
    lines = [
        f"human entries on {len(human_dates)} distinct date(s); "
        f"we hold {len(rows)} row(s), delivering {sum(1 for _d, i, _s, _e in rows if i)}",
        "",
        f"{'bucket':<22} {'dates':>6}",
        "-" * 30,
    ]
    for key in ORDER:
        lines.append(f"{key:<22} {buckets.get(key, 0):>6}")
    lost = buckets.get(EXCLUDED, 0)
    lines += [
        "",
        f"CONTENT WE HOLD AND DO NOT DELIVER: {lost} encounter(s).",
        "  That is the defect bucket. `absent_but_re_dated` is a labelling disagreement - the pages",
        "  ARE delivered, under a different date - and `extra` is mostly the one-entry-per-encounter",
        "  versus one-summary-per-document convention gap, not an error.",
    ]
    for key in (EXCLUDED, RE_DATED, UNFOUND):
        if detail.get(key):
            lines.append(f"  {key}: {', '.join(detail[key])}")
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - I/O wrapper around the tested functions above
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--document", required=True, help="document id (a prefix is enough)")
    ap.add_argument("--human", required=True, help="path to that record's 03_human_summary.txt")
    args = ap.parse_args()

    from pathlib import Path

    from sqlalchemy import text as sql_text

    from app.db import get_sessionmaker

    human_text = Path(args.human).read_text(encoding="utf-8", errors="replace")
    human_dates = human_entry_dates(human_text)
    if not human_dates:
        raise SystemExit(
            f"no date-led entries found in {args.human} - the report may be truncated, or its "
            "entries may not be tab-separated. Refusing to report 0 as a clean result."
        )

    maker = get_sessionmaker()
    with maker() as session:
        document_id = session.scalar(
            sql_text("select id from documents where id like :p order by created_at limit 1"),
            {"p": args.document + "%"},
        )
        if not document_id:
            raise SystemExit(f"no document matching {args.document!r}")
        rows = [
            (row.date, bool(row.include), int(row.start), int(row.end))
            for row in session.execute(
                sql_text(
                    'select date, include, start, "end" from review_rows '
                    "where document_id = :d order by start"
                ),
                {"d": document_id},
            )
        ]
        pages = session.execute(
            sql_text("select text from page_texts where document_id = :d"), {"d": document_id}
        ).all()
        document_text = "\n".join(page.text or "" for page in pages)

    if not rows:
        raise SystemExit(f"document {document_id[:8]} has no review rows")
    if not document_text.strip():
        print(
            "WARNING: no stored page text for this document, so `absent` cannot be split into "
            "re-dated and unfound. Every absent date will be reported as unfound.\n"
        )

    buckets, detail = classify_entries(human_dates, rows, document_text)
    print(f"document {document_id[:8]}, human report {Path(args.human).name}\n")
    print(summarise(buckets, detail, rows, human_dates))


if __name__ == "__main__":  # pragma: no cover
    main()
