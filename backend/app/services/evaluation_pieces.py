"""A QME/AME evaluation cut into pieces is offered back to the reviewer as one, as suggestions.

The senior reviewer, 2026-11-02: "for QME/AME reports (or any report that has a Medical Record
Review as part of the report), the segmentation is splitting the individual summaries as different
entries, despite just being a summary as part of the larger report." And again 2026-11-05, on a
record cut into some thirty pieces across three evaluations. The segmentation prompt already says to
keep an evaluation whole, but a window that starts inside the evaluation's records review sees a run
of dated summaries of other records, each looking like a document of its own.

A PIECE is a draft row that follows a category 12 or 13 row (an evaluation) and whose first page
CONTINUES it: the page label in the first or last lines of the page ("Page 7", "Page 7 of 40",
"7 of 40") is one more than the label of the page before, under the same total when both state one.
The walk forward from the evaluation goes on while the labels run on and stops at the first cut
where they do not - the next document starts its own numbering, or carries none. A summary of an
earlier evaluation inside the records review is filed 12 or 13 itself; it is a piece like any other
and the walk goes on through it.

The evaluation's numbering must be ITS OWN: the first label among its first three pages is no
higher than that page's place in it (its first page 1, its second 1 or 2, ...). A higher number is a
stamp across the whole bundle - a fax transmission or Bates numbers - which runs on through every
document after the evaluation too, and no walk starts there.

The first version (#656) also required evaluator wording (QME, AME, medical-legal) on both pages of
every cut. A records review rarely names the evaluator on its pages, so that version found 69 of the
382 pieces reviewers had merged into evaluations on the live box; see "MEASURED" below.

MEASURED, read-only on the live box (2026-11-05): the newest draft of 502 records holding a category
12/13 row, against the reviewer's final rows. A draft start strictly inside a final 12/13 row is a
piece the reviewer merged in: 382 of them. This rule marks 325 rows: 267 such pieces (70%; 97 of
the 131 on exported records), 43 rows reviewers kept as unticked rows of their own, 3 they merged
elsewhere, and 12 they kept as ticked documents (6 of them in one record). #656's rule marked 108:
69 pieces (18%), 31 unticked, 8 ticked. On the email's record this rule marks all 33 pieces of its
three evaluations and nothing else. Without the "its own numbering" check the walk found 294 pieces
but marked 73 ticked documents, most in records stamped page by page across every document. Most
pieces it still misses sit at a cut where a page carries no label (the footer was not read, or the
review is unnumbered); bridging one unlabelled page found 10 more and was left out for the extra
rules it needs.

ONLY SUGGESTIONS. A row is marked `suggest_merge`, the flag the verify pass sets, so the reviewer
sees it and merges it into the evaluation with one click; no boundary moves here, and nothing
already marked is cleared. A wrong suggestion costs a click, a wrong merge would hide a document.
Deterministic and on stored page text: no model call. Off by default
(``SUGGEST_EVALUATION_PIECES``), turned on per box.
"""

import re
from collections.abc import Callable, Iterator

_EVALUATION_CATEGORIES = frozenset({"12", "13"})
# Where a page label is printed: the running header or the footer. A page number quoted in the body
# ("see page 247 of the records") is not the page's own.
_EDGE_LINES = 4
_PAGE_LABEL = re.compile(
    r"\bpage\s{0,9}[:#.]?\s{0,9}(\d{1,3})(?:\s{0,9}(?:of|/)\s{0,9}(\d{1,3}))?\b", re.IGNORECASE
)
_N_OF_M = re.compile(r"\b(\d{1,3})\s{1,9}of\s{1,9}(\d{1,3})\b", re.IGNORECASE)
# How far into the evaluation its first page label may sit (a cover page is often unnumbered).
_FIRST_PAGES = 3

type Label = tuple[int, int | None]


def _labels(text: str) -> set[Label]:
    """The page labels printed in the page's first and last lines: (number, total or None)."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    found: set[Label] = set()
    for line in lines[:_EDGE_LINES] + lines[-_EDGE_LINES:]:
        for match in _PAGE_LABEL.finditer(line):
            found.add((int(match[1]), int(match[2]) if match[2] else None))
        for match in _N_OF_M.finditer(line):
            found.add((int(match[1]), int(match[2])))
    return {(number, total) for number, total in found if total is None or 0 < number <= total}


def _runs_on(before: set[Label], page: set[Label], step: int = 1) -> bool:
    """Does a label on ``page`` follow one on ``before`` by ``step``, under the same total?"""
    return any(
        number == previous + step
        and (total is None or previous_total is None or total == previous_total)
        for previous, previous_total in before
        for number, total in page
    )


def _is_evaluation(row: dict) -> bool:
    return str(row.get("category")) in _EVALUATION_CATEGORIES


def _numbering_starts_with_it(start: int, page_text_fn: Callable[[int], str]) -> bool:
    """The evaluation's numbering is its own: the first labelled page among its first
    ``_FIRST_PAGES`` is numbered no higher than its place (page 1 as 1, page 2 as 1 or 2, ...).
    A bundle-wide stamp numbers it from wherever the bundle had got to."""
    for offset in range(_FIRST_PAGES):
        labels = _labels(page_text_fn(start + offset) or "")
        if labels:
            return min(number for number, _total in labels) <= offset + 1
    return False


def _pieces_after(rows: list[dict], i: int, page_text_fn: Callable[[int], str]) -> Iterator[int]:
    """The pieces of the evaluation at row ``i``: the walk forward described in the module."""
    for j in range(i + 1, len(rows)):
        start = int(rows[j]["start"])
        before, page = _labels(page_text_fn(start - 1) or ""), _labels(page_text_fn(start) or "")
        if not _runs_on(before, page):
            return
        yield j


def evaluation_piece_indices(rows: list[dict], page_text_fn: Callable[[int], str]) -> list[int]:
    """Indices of the rows that continue the evaluation before them - see the module."""
    pieces: set[int] = set()
    for i, row in enumerate(rows):
        if i in pieces or not _is_evaluation(row):
            continue
        if _numbering_starts_with_it(int(row["start"]), page_text_fn):
            pieces.update(_pieces_after(rows, i, page_text_fn))
    return sorted(pieces)


def suggest_evaluation_pieces(
    rows: list[dict], page_text_fn: Callable[[int], str]
) -> tuple[list[dict], dict]:
    """``rows`` with every evaluation piece marked as a suggested merge, and counts for the log."""
    indices = set(evaluation_piece_indices(rows, page_text_fn))
    out = [{**row, "suggest_merge": True} if i in indices else row for i, row in enumerate(rows)]
    return out, {"evaluation_pieces": len(indices)}
