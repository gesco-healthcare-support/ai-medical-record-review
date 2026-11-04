"""One entry per date: a Kaiser or hospital record's review rows merged by date.

The senior reviewer's answers of 2026-10-30, for Kaiser and hospital records: "For simplicity, I
think we can keep everything from one date as one entry moving forward, and have it summarized as a
progress note ... The exception would be for diagnostics, which should be separated." And: "Things
such as allergies and immunization records aren't important", and "Especially for Kaiser/Hospital
records, I think it would help to untick [labs] so we can save time". And on 2026-11-04: "for
discharge summaries, those can be merged together. I think the only things that should be its own
entry would be Operative Reports (Category 8) and Diagnostics (Category 3)".

ONLY ON THE REVIEWER'S CHOICE. On ordinary records reviewers keep same-date neighbours apart - 2,089
same-date neighbouring rows kept apart across 276 live records - and keep most lab results ticked
(153 of 249), so neither rule may apply to every record. A hospital record cannot be told from its
rows reliably, so the reviewer presses "One entry per date" on the workbench; the route that answers
it (`api/documents.py` `one_entry_per_date`) stores what this returns through the same row store a
hand merge saves through, and marks the document so the letter folds the same-date entries that are
not neighbours (`summarize_engine.fold_record_entries`, ``by_date``).

What it does to the rows, in page order:

- every run of NEIGHBOURING rows that share one known date becomes one row. A diagnostic study
  (category 3), an operative report (category 8), a lab result (category 14) and an allergy or
  immunization row stay as they are and break a run, so a study or an operation never disappears
  into a visit. A discharge summary (category 16) merges like any other row;
- the merged row is category 1 (a progress note, the reviewer's first choice) with the run's date
  and the title of its first ticked piece. A run of General (100) rows only stays General;
- it is ticked when any piece was, the rule a hand merge keeps (`mergeRows` in
  `frontend/lib/review-rows.ts`): on a fresh draft that is every run with a clinical piece, and a
  run the reviewer had unticked piece by piece stays unticked rather than being put back;
- every category 14 row is unticked.

Pure: rows in, rows out, no database. The Qwen session replays it read-only on finished records to
report the before and after.
"""

from dataclasses import dataclass

from app.services.classification import is_allergy_or_immunization
from app.services.reporting import parsed_date

_GENERAL = "100"
_PROGRESS_NOTE = "1"
_DIAGNOSTIC = "3"
_OPERATIVE = "8"
_LAB = "14"
# The categories that keep their own entry, whatever their date.
_OWN_ENTRY = (_DIAGNOSTIC, _OPERATIVE, _LAB)


@dataclass(frozen=True)
class DateMerge:
    """The merged rows, and counts for the audit trail."""

    rows: list[dict]
    runs: int  # runs of two or more rows made one
    rows_folded: int  # rows that disappeared into a run
    labs_unticked: int


def _keeps_its_own_entry(row: dict) -> bool:
    return str(row.get("category")) in _OWN_ENTRY or is_allergy_or_immunization(row.get("title"))


def _date_of(row: dict):
    """The row's date as a datetime, or None when it states none. The export's own reading of a
    date (`reporting.parsed_date`), so "10/01/2025" and "10-01-2025" are one day."""
    return parsed_date({"summaryDate": str(row.get("date") or "")})


def _merged(run: list[dict]) -> dict:
    ticked = [row for row in run if row.get("include", True)]
    lead = ticked[0] if ticked else run[0]
    clinical = any(str(row.get("category")) != _GENERAL for row in run)
    return {
        **lead,
        "start": run[0]["start"],
        "end": max(int(row["end"]) for row in run),
        "category": _PROGRESS_NOTE if clinical else _GENERAL,
        "flag": "x" if any(row.get("flag") == "x" for row in run) else "-",
        "suggest_merge": bool(run[0].get("suggest_merge")),
        "include": bool(ticked),
    }


def _runs(rows: list[dict]):
    """The rows in page order, grouped: each run of neighbours sharing a known date, and every row
    that keeps its own entry or has no date on its own."""
    run: list[dict] = []
    run_date = None
    for row in sorted(rows, key=lambda r: (int(r["start"]), int(r["end"]))):
        date = None if _keeps_its_own_entry(row) else _date_of(row)
        if run and (date is None or date != run_date):
            yield run
            run = []
        run.append(row)
        run_date = date
    if run:
        yield run


def merge_by_date(rows: list[dict]) -> DateMerge:
    """``rows`` (editor rows, in page order) with each same-date run made one and labs unticked."""
    out: list[dict] = []
    runs = 0
    for run in _runs(rows):
        if len(run) > 1:
            out.append(_merged(run))
            runs += 1
        else:
            out.extend(run)
    labs_unticked = 0
    for i, row in enumerate(out):
        if str(row.get("category")) == _LAB and row.get("include", True):
            out[i] = {**row, "include": False}
            labs_unticked += 1
    return DateMerge(
        rows=out, runs=runs, rows_folded=len(rows) - len(out), labs_unticked=labs_unticked
    )
