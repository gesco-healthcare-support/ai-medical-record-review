"""A QME/AME evaluation cut into pieces is offered back to the reviewer as one, as suggestions.

The senior reviewer, 2026-11-02: "for QME/AME reports (or any report that has a Medical Record
Review as part of the report), the segmentation is splitting the individual summaries as different
entries, despite just being a summary as part of the larger report." The segmentation prompt already
says to keep an evaluation whole, but a window that starts inside the evaluation's records review
sees a run of dated summaries of other records, each looking like a document of its own.

A PIECE is a draft row that follows a category 12 or 13 row (an evaluation) and whose first page
CONTINUES it: that page and the one before both carry evaluator wording - the evaluation's running
header names the QME or AME - and the page numbering runs on across them (page k, then page k + 1).
The walk forward from the evaluation stops at the first boundary whose pages do not both carry the
wording, and after a category 12 or 13 row of its own, which starts a walk of its own.

MEASURED before shipping, read-only on the live box: every draft cut inside a category 12/13 row's
evaluator-worded pages on 448 finished records, against the reviewer's final rows. 376 such cuts;
reviewers merged 80 away, kept 178 as unticked rows and 118 as ticked documents. The cuts whose page
numbering runs on: 104 - merged 67, kept unticked 31, kept ticked 6. So this finds 67 of the 80 the
reviewers merged by hand, and 6 of the rows it marks are real documents a reviewer kept. Neither the
running header alone nor a page of dated entries added a merged cut the numbering missed.

ONLY SUGGESTIONS. A row is marked `suggest_merge`, the flag the verify pass sets, so the reviewer
sees it and merges it into the evaluation with one click; no boundary moves here, and nothing
already marked is cleared. A wrong suggestion costs a click, a wrong merge would hide a document.
Deterministic and on stored page text: no model call. Off by default
(``SUGGEST_EVALUATION_PIECES``), turned on per box.
"""

import re
from collections.abc import Callable, Iterator

_EVALUATION_CATEGORIES = frozenset({"12", "13"})
_EVALUATOR_WORDING = re.compile(
    r"\b(?:qme|ame|pqme)\b|qualified medical evaluat|agreed medical evaluat|medical.legal",
    re.IGNORECASE,
)
_PAGE_NUMBER = re.compile(r"\bpage\s+(\d{1,3})\b", re.IGNORECASE)


def _page_numbers(text: str) -> set[int]:
    return {int(number) for number in _PAGE_NUMBER.findall(text or "")}


def _numbering_runs_on(before: str, page: str) -> bool:
    previous = _page_numbers(before)
    return any(number - 1 in previous for number in _page_numbers(page))


def _is_evaluation(row: dict) -> bool:
    return str(row.get("category")) in _EVALUATION_CATEGORIES


def _pieces_after(rows: list[dict], i: int, page_text_fn: Callable[[int], str]) -> Iterator[int]:
    """The pieces of the evaluation at row ``i``: the walk forward described in the module."""
    for j in range(i + 1, len(rows)):
        start = int(rows[j]["start"])
        before, page = page_text_fn(start - 1) or "", page_text_fn(start) or ""
        if not (_EVALUATOR_WORDING.search(before) and _EVALUATOR_WORDING.search(page)):
            return
        if _numbering_runs_on(before, page):
            yield j
        if _is_evaluation(rows[j]):
            return


def evaluation_piece_indices(rows: list[dict], page_text_fn: Callable[[int], str]) -> list[int]:
    """Indices of the rows that continue the evaluation before them - see the module."""
    pieces = {
        j
        for i, row in enumerate(rows)
        if _is_evaluation(row)
        for j in _pieces_after(rows, i, page_text_fn)
    }
    return sorted(pieces)


def suggest_evaluation_pieces(
    rows: list[dict], page_text_fn: Callable[[int], str]
) -> tuple[list[dict], dict]:
    """``rows`` with every evaluation piece marked as a suggested merge, and counts for the log."""
    indices = set(evaluation_piece_indices(rows, page_text_fn))
    out = [{**row, "suggest_merge": True} if i in indices else row for i, row in enumerate(rows)]
    return out, {"evaluation_pieces": len(indices)}
