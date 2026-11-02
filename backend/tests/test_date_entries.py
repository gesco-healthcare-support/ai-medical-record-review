"""One entry per date (`services/date_entries.py`): a Kaiser or hospital record's neighbouring
same-date rows made one, diagnostics, labs and allergy or immunization rows left alone, labs
unticked. Synthetic rows only."""

from app.services.date_entries import merge_by_date

_DAY = "03/02/2026"
_NEXT_DAY = "03/03/2026"


def _row(start, end=None, category="1", title="Progress Note", date=_DAY, include=True, flag="-"):
    return {
        "start": start,
        "end": start if end is None else end,
        "category": category,
        "title": title,
        "date": date,
        "injury_date": "-",
        "flag": flag,
        "suggest_merge": False,
        "include": include,
    }


def test_a_run_of_same_date_neighbours_becomes_one_progress_note():
    """DEMONSTRATES the answer: "keep everything from one date as one entry ... summarized as a
    progress note". The merged row is category 1, ticked, titled from its first ticked piece."""
    rows = [
        _row(1, 2, category="100", title="Patient Instructions", include=False),
        _row(3, 4, category="16", title="Inpatient Daily Note"),
        _row(5, 5, category="5", title="Physical Therapy Note"),
    ]
    result = merge_by_date(rows)
    assert result.rows == [
        {**_row(1, 5, category="1", title="Inpatient Daily Note"), "include": True}
    ]
    assert (result.runs, result.rows_folded, result.labs_unticked) == (1, 2, 0)


def test_each_date_gets_its_own_entry():
    """DEMONSTRATES: a run ends where the date changes."""
    rows = [_row(1), _row(2), _row(3, date=_NEXT_DAY), _row(4, date=_NEXT_DAY)]
    result = merge_by_date(rows)
    assert [(r["start"], r["end"], r["date"]) for r in result.rows] == [
        (1, 2, _DAY),
        (3, 4, _NEXT_DAY),
    ]
    assert result.runs == 2


def test_a_date_written_another_way_is_the_same_date():
    """DEMONSTRATES: dates are read the export's way, so the separator does not split a day."""
    result = merge_by_date([_row(1, date="03/02/2026"), _row(2, date="03-02-2026")])
    assert [(r["start"], r["end"]) for r in result.rows] == [(1, 2)]


def test_every_lab_result_ends_unticked_and_is_never_merged():
    """GUARD (both halves of answer 3 for these records): a category 14 row stays its own row and
    ends unticked, and it breaks a run - the two notes either side of it are not neighbours."""
    rows = [
        _row(1),
        _row(2, category="14", title="CBC"),
        _row(3, category="14", title="BMP"),
        _row(4),
    ]
    result = merge_by_date(rows)
    assert [(r["start"], r["category"], r["include"]) for r in result.rows] == [
        (1, "1", True),
        (2, "14", False),
        (3, "14", False),
        (4, "1", True),
    ]
    assert (result.runs, result.labs_unticked) == (0, 2)


def test_a_diagnostic_study_is_never_merged():
    """GUARD: "The exception would be for diagnostics, which should be separated." A study keeps
    its row, category and tick, and breaks the run around it."""
    rows = [_row(1), _row(2), _row(3, category="3", title="MRI Lumbar Spine"), _row(4)]
    result = merge_by_date(rows)
    assert [(r["start"], r["end"], r["category"]) for r in result.rows] == [
        (1, 2, "1"),
        (3, 3, "3"),
        (4, 4, "1"),
    ]
    assert result.rows[1] == _row(3, category="3", title="MRI Lumbar Spine")


def test_an_allergy_or_immunization_row_is_never_merged():
    """GUARD: answer 1 - such a record is disregarded, so it is never folded into a visit's entry,
    whatever category it was filed under."""
    rows = [
        _row(1),
        _row(2, category="16", title="Allergies"),
        _row(3, title="Immunization Record"),
    ]
    result = merge_by_date(rows)
    assert result.rows == rows
    assert result.runs == 0


def test_a_run_of_general_rows_only_stays_general_and_unticked():
    """GUARD: merging paperwork does not turn it into a progress note or put it in the letter."""
    rows = [
        _row(1, category="100", title="Facesheet", include=False),
        _row(2, category="100", title="Patient Instructions", include=False),
    ]
    result = merge_by_date(rows)
    assert [(r["start"], r["end"], r["category"], r["include"]) for r in result.rows] == [
        (1, 2, "100", False)
    ]


def test_a_run_the_reviewer_had_unticked_stays_unticked():
    """GUARD: ticked when any piece was, the rule a hand merge keeps - a reviewer who unticked
    every piece of a date does not get it back in the letter."""
    rows = [_row(1, include=False), _row(2, category="5", include=False)]
    result = merge_by_date(rows)
    assert [(r["category"], r["include"]) for r in result.rows] == [("1", False)]


def test_undated_rows_and_different_dates_are_never_merged():
    """GUARD: only a KNOWN date joins rows."""
    rows = [_row(1, date="-"), _row(2, date="-"), _row(3), _row(4, date=_NEXT_DAY)]
    result = merge_by_date(rows)
    assert result.rows == rows
    assert (result.runs, result.rows_folded) == (0, 0)


def test_a_merged_row_is_built_like_a_hand_merge():
    """GUARD: the manual-check flag survives if any piece had it, the row runs from the first
    piece's start to the last page any piece covers, and a lone row keeps its own category."""
    rows = [_row(1, 2), _row(4, 6, flag="x"), _row(8, category="5", date=_NEXT_DAY)]
    result = merge_by_date(rows)
    assert [(r["start"], r["end"], r["flag"], r["category"]) for r in result.rows] == [
        (1, 6, "x", "1"),
        (8, 8, "-", "5"),
    ]


def test_rows_out_of_page_order_are_merged_in_page_order():
    """GUARD: neighbours are neighbours in the PDF, whatever order the rows arrive in."""
    result = merge_by_date([_row(2), _row(1)])
    assert [(r["start"], r["end"]) for r in result.rows] == [(1, 2)]
