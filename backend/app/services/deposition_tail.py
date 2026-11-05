"""A deposition's closing paperwork offered back to it as a merge HINT, never a merge.

A deposition transcript ends with its own paperwork - the reporter's certificate, the errata or
correction sheet, the witness's signature or read-and-sign page, the stipulations, an index of
exhibits. Segmentation often cuts that off as a short row of its own, and reviewers join it back to
the transcript.

A TAIL is a row of at most ``_MAX_TAIL_PAGES`` pages directly after a category 9 (deposition) row
whose title names such paperwork (``_PAPERWORK_TITLE``). It gets ``suggest_merge``, the flag the
verify pass sets, so the reviewer joins it to the deposition with one click.

MEASURED on the reviewers' finished records (2026-11-05): short rows right after a deposition whose
title names paperwork were joined back 15 of 16 times on Gemini's drafts (283 live records) and 2 of
2 on our model's; short rows after a deposition with any other title only 12 of 57 and 1 of 10. So
the title is what makes the hint safe, and no other row is hinted.

No model call, no boundary moves, nothing already hinted is cleared. Behind ``VISIT_MERGE_HINTS``,
with the other hints that cost no model call.
"""

import re

_DEPOSITION_CATEGORY = "9"
_MAX_TAIL_PAGES = 6
_PAPERWORK_TITLE = re.compile(
    r"certif|errata|signature|stipulat|procedure|reporter|read and sign|correction sheet|witness"
    r"|\bindex|exhibit|attestation|jurat|\boath\b",
    re.IGNORECASE,
)


def _is_tail(prev: dict, row: dict) -> bool:
    pages = int(row["end"]) - int(row["start"]) + 1
    return (
        str(prev.get("category")) == _DEPOSITION_CATEGORY
        and pages <= _MAX_TAIL_PAGES
        and bool(_PAPERWORK_TITLE.search(str(row.get("title") or "")))
    )


def hint_deposition_paperwork(rows: list[dict]) -> tuple[list[dict], int]:
    """``rows`` with every deposition tail hinted (see the module), and how many rows that newly
    hinted. The caller's rows are not changed."""
    out, hinted = [], 0
    for i, row in enumerate(rows):
        if i and not row.get("suggest_merge") and _is_tail(rows[i - 1], row):
            row = {**row, "suggest_merge": True}
            hinted += 1
        out.append(row)
    return out, hinted
