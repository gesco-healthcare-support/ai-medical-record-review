"""When a document labels its own encounter date, do we pick that one?

Read-only. No model calls, no writes, no human reference. Prints COUNTS and dates only, never page
text. The third date check, and the one that tests the RULE rather than the outcome:

    date_in_source.py        is our date anywhere in the pages it describes?
    date_vs_human_entries.py does the human have an entry we have nothing for?
    this one                 when the page says DATE OF EXAM, is that what we took?

WHY IT EXISTS. The encounter date is "the date the encounter the document is about actually
happened" - when the exam or visit occurred, never the date of injury and never a date the page
merely carries. A document dated 08/21 and signed 08/23 has an encounter date of 08/21.

The instruction that produces our date is one clause of the segmentation prompt:

    "d" document date: the visit/encounter date of THIS document as MM/DD/YYYY (it may be near the
    signature at the end); ignore fax, print, and re-send dates, and never report the date of injury.

Three things about that clause are what this script measures:

  * it names NO labels. "date of exam", "visit date" and "DOS" appear nowhere in the prompt, so the
    model is given the concept and left to locate it.
  * it points at the END of the document - "near the signature" - when the labelled encounter date is
    usually in the header on the first page.
  * "signature date" is NOT in its ignore list, which reads "fax, print, and re-send". Combined with
    the parenthetical, the prompt arguably invites the one date we least want.

So the buckets are named after WHICH labelled date we took, and the interesting one is
`signature_date`: taking it is the specific failure the prompt wording would produce.

Measured on the stored OCR: 5,558 pages carry 275 "visit date", 265 labelled "DOS", 257 "date of
service" and 255 "date of exam" - and 612 a signature date, 594 a print date. More pages carry a date
we should ignore than carry any single label we want.

    docker compose exec -T -e PYTHONPATH=/app api python scripts/eval/date_label_check.py [--user N]
        [--all-copies]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# scripts/ is not a package. Running this file by path already puts its directory on sys.path, but
# being explicit means the import works from a test too - and at MODULE level rather than inside
# main(), so a missing helper fails loudly on import and the suite catches it. Hidden inside main()
# it would pass CI and raise only when someone ran the script against the box.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# From `corpus` rather than `date_in_source`: the rule is a property of the corpus, not of that
# script, and importing one eval script from another made it look like a local convention (#219).
from corpus import one_copy_per_pdf  # noqa: E402

# A date in the forms OCR produces. Deliberately the same tolerance as date_in_source: a
# slashes-only reader over-reports by about 2x.
_DATE = r"(\d{1,2})\s?[/.-]\s?(\d{1,2})\s?[/.-]\s?(\d{2,4})"

# Up to this much noise between a label and its date - a colon, whitespace, an OCR smudge, a
# stray bar from a table cell. Bounded rather than `.*` so there is no runaway match.
_GAP = r"[^0-9\n]{0,24}"

# Label families, and the bucket each one implies. Order matters only for reporting; matching
# collects all of them.
LABELS: dict[str, tuple[str, ...]] = {
    # The date we want. Several spellings because records use all of them.
    "encounter_date": (
        r"date of exam(?:ination)?",
        r"exam(?:ination)? date",
        r"date of visit",
        r"visit date",
        r"date of service",
        r"\bd\.?o\.?s\.?\b",
        r"date of encounter",
        r"encounter date",
        r"date seen",
        r"service date",
    ),
    # The date the clinician signed, which can be days after the encounter. The example that
    # prompted this check: a document dated 08/21 signed 08/23 has an encounter date of 08/21.
    "signature_date": (
        r"signature date",
        r"date signed",
        r"signed on",
        r"electronically signed(?:\s+by[^0-9\n]{0,40})?",
        r"e-?signed",
    ),
    # Administrative dates the prompt already names.
    "print_or_fax_date": (
        r"printed on",
        r"print date",
        r"date printed",
        r"fax(?:ed)? date",
        r"date faxed",
        r"received date",
        r"date received",
    ),
    # Explicitly never the answer.
    "date_of_injury": (r"date of injury", r"\bd\.?o\.?i\.?\b", r"injury date"),
}

# Buckets, best first.
TOOK_ENCOUNTER = "took_the_labelled_encounter_date"
TOOK_SIGNATURE = "took_the_signature_date"
TOOK_PRINT = "took_a_print_or_fax_date"
TOOK_INJURY = "took_the_date_of_injury"
UNLABELLED = "date_not_next_to_any_label"
NO_LABELS = "no_labels_on_these_pages"
NO_DATE = "no_date_to_check"
ORDER = (
    TOOK_ENCOUNTER,
    TOOK_SIGNATURE,
    TOOK_PRINT,
    TOOK_INJURY,
    UNLABELLED,
    NO_LABELS,
    NO_DATE,
)


def normalise(month: str, day: str, year: str) -> str | None:
    """-> MM/DD/YYYY, or None if the pieces are not a plausible date."""
    try:
        m, d, y = int(month), int(day), int(year)
    except ValueError:
        return None
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    if y < 100:
        y += 2000
    if not (1900 <= y <= 2100):
        return None
    return f"{m:02d}/{d:02d}/{y:04d}"


def labelled_dates(text: str) -> dict[str, set[str]]:
    """Every date that sits next to a recognised label, grouped by label family.

    A date with no label nearby is NOT collected: this measures labelled dates specifically, because
    an unlabelled date says nothing about whether we followed the rule.
    """
    found: dict[str, set[str]] = {kind: set() for kind in LABELS}
    lowered = (text or "").lower()
    for kind, labels in LABELS.items():
        for label in labels:
            for match in re.finditer(rf"{label}{_GAP}{_DATE}", lowered):
                date = normalise(*match.groups())
                if date:
                    found[kind].add(date)
    return found


def classify_row(assigned: str, labelled: dict[str, set[str]]) -> str:
    """Which labelled date we took, in priority order.

    Priority matters and is not arbitrary: a document signed the same day it was written has the same
    date under both labels, and calling that a signature-date error would be wrong. So an
    encounter-label match always wins, and `took_the_signature_date` counts only rows where the
    signature date is the one we took AND no encounter label agrees with it.
    """
    if not assigned or assigned == "-":
        return NO_DATE
    if not any(labelled.values()):
        return NO_LABELS
    if assigned in labelled["encounter_date"]:
        return TOOK_ENCOUNTER
    if assigned in labelled["signature_date"]:
        return TOOK_SIGNATURE
    if assigned in labelled["print_or_fax_date"]:
        return TOOK_PRINT
    if assigned in labelled["date_of_injury"]:
        return TOOK_INJURY
    return UNLABELLED


def recoverable(labelled: dict[str, set[str]]) -> bool:
    """Was a labelled encounter date available on these pages at all?

    The number that decides whether the prompt wording is worth changing: a row that took the
    signature date while an encounter label sat on the same pages is a miss we could have avoided. A
    row with no encounter label anywhere had nothing better to take.
    """
    return bool(labelled["encounter_date"])


def summarise(rows) -> str:
    """`rows` is [(bucket, recoverable)]. The headline is the avoidable subset, deliberately."""
    counts = Counter(bucket for bucket, _r in rows)
    total = sum(counts.values())
    if not total:
        return "no rows checked"
    lines = [f"{'bucket':<34}{'rows':>6}{'share':>8}", "-" * 48]
    for name in ORDER:
        n = counts.get(name, 0)
        lines.append(f"{name:<34}{n:>6}{100.0 * n / total:>7.1f}%")
    lines += ["-" * 48, f"{'checked':<34}{total:>6}", ""]

    wrong = [b for b, _r in rows if b in (TOOK_SIGNATURE, TOOK_PRINT, TOOK_INJURY)]
    avoidable = [b for b, r in rows if b in (TOOK_SIGNATURE, TOOK_PRINT, TOOK_INJURY) and r]
    labelled_rows = [b for b, r in rows if r]
    lines.append(
        f"HEADLINE: {len(avoidable)} row(s) took a signature, print or injury date while a LABELLED "
        f"encounter date sat on the same pages"
    )
    if labelled_rows:
        lines.append(
            f"  That is {100.0 * len(avoidable) / len(labelled_rows):.1f}% of the "
            f"{len(labelled_rows)} rows where a labelled encounter date was available to take."
        )
    lines.append(
        f"  {len(wrong) - len(avoidable)} further row(s) took one of those dates with NO encounter "
        f"label on the page - nothing better was there, so the prompt cannot fix them."
    )
    lines.append(
        f"  `{UNLABELLED}` is not a defect: the date may be in a header with no label, which is "
        f"common and correct."
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - I/O wrapper around the tested functions above
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--user", type=int, default=None, help="restrict to one owner id")
    ap.add_argument(
        "--all-copies",
        action="store_true",
        help="count every uploaded copy of a record, not one per distinct PDF",
    )
    args = ap.parse_args()

    from sqlalchemy import select, text as sql_text

    from app.db import get_sessionmaker
    from app.models import Document, ReviewRow

    maker = get_sessionmaker()
    with maker() as session:
        q = select(ReviewRow, Document).join(Document, Document.id == ReviewRow.document_id)
        if args.user is not None:
            q = q.where(Document.user_id == args.user)
        pairs = session.execute(q).all()
        if not args.all_copies:
            pairs, dropped_rows, dropped_docs = one_copy_per_pdf(pairs)
            if dropped_docs:
                print(
                    f"excluded {dropped_rows} row(s) in {dropped_docs} redundant copy/copies of a "
                    f"record already counted (--all-copies to include them)\n"
                )

        texts: dict[str, dict[int, str]] = {}
        rows, skipped = [], 0
        for row, doc in pairs:
            if doc.id not in texts:
                got = session.execute(
                    sql_text("select page, text from page_texts where document_id=:d"),
                    {"d": doc.id},
                ).all()
                texts[doc.id] = {p.page: (p.text or "") for p in got}
            pages = texts[doc.id]
            span = range(int(row.start), int(row.end) + 1)
            if not pages or any(p not in pages for p in span):
                skipped += 1
                continue
            labelled = labelled_dates("\n".join(pages[p] for p in span))
            rows.append((classify_row(row.date, labelled), recoverable(labelled)))

    print(summarise(rows))
    print(f"\nskipped {skipped} row(s) with incomplete stored page text - not checkable either way")


if __name__ == "__main__":  # pragma: no cover
    main()
