"""The human-entry date comparison, unit tested. No DB, no Vertex, no human report on disk.

These exist because the raw version of this measurement was wrong in a way that read as a
catastrophe: "no row carries this date" came to 11 encounters across six records and looked like 11
missed documents. Ten were re-dated - the pages ARE delivered, under a different date - and one was
genuinely unfound. Each test below pins one of the distinctions that separates those.
"""

import sys
from pathlib import Path

import pytest

# scripts/ is not a package and not on the path; the eval scripts are run by path in the container.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from date_vs_human_entries import (  # noqa: E402
    DELIVERED,
    EXCLUDED,
    EXTRA,
    RE_DATED,
    UNFOUND,
    appears_in_text,
    classify_entries,
    human_entry_dates,
    normalise,
)


def _row(date, included=True, start=1, end=1):
    return (date, included, start, end)


def _nonzero(buckets):
    """Only the buckets that fired, so a test can assert the WHOLE outcome, not one count."""
    return {key: count for key, count in buckets.items() if count}


# --- parsing the human report ------------------------------------------------------------------


def test_human_entries_are_the_date_led_lines():
    text = (
        "MEDICAL RECORD REVIEW\n\n03/07/23\tA. PHYSICIAN\tsummary text\n03/09/23\tB. OTHER\tmore\n"
    )
    assert human_entry_dates(text) == {"03/07/2023", "03/09/2023"}


def test_a_date_inside_a_summary_body_is_not_an_entry():
    """Anchored on the tab. Bodies quote dates constantly - "returned on 05/04/2021" is not an entry,
    and counting it would invent encounters the human never wrote up."""
    text = "03/07/23\tA. PHYSICIAN\tAdvised to return on 05/04/2021 for review.\n"
    assert human_entry_dates(text) == {"03/07/2023"}


def test_a_two_digit_year_becomes_twenty_something():
    assert normalise("3", "7", "23") == "03/07/2023"
    assert normalise("03", "07", "2023") == "03/07/2023"


def test_no_entries_found_is_an_empty_set_not_a_guess():
    """main() turns this into a hard exit rather than reporting a clean result - a truncated report
    would otherwise score as perfect agreement."""
    assert (
        human_entry_dates("PANEL QUALIFIED MEDICAL EVALUATION\n\nprose with no entries\n") == set()
    )


# --- does the date appear in the OCR at all ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "DATE OF EXAM: 03/11/2026",
        "Date of exam 3-11-2026",
        "exam date 03.11.2026",
        "seen 03 11 26",
        "DATE OF EXAM: 3/11/26",
        "examined on March 11, 2026",
        "examined on march 11th, 2026",
    ],
)
def test_a_date_is_found_in_the_forms_ocr_produces(text):
    """A slashes-only test reports about twice as many dates missing as really are. Each form here
    changed the answer when it was added to date_in_source."""
    assert appears_in_text("03/11/2026", text)


@pytest.mark.parametrize("text", ["DATE OF EXAM: 03/12/2026", "no dates here", "2026", ""])
def test_a_date_that_is_not_there_is_not_found(text):
    assert not appears_in_text("03/11/2026", text)


def test_a_day_that_is_only_a_prefix_does_not_match():
    """03/1/2026 must not satisfy 03/11/2026 - the word boundary does that work."""
    assert not appears_in_text("03/11/2026", "DATE OF EXAM: 03/1/2026 only")


def test_an_unparseable_date_is_not_found_rather_than_raising():
    assert not appears_in_text("-", "any text at all")


# --- the buckets --------------------------------------------------------------------------------


def test_a_date_we_deliver_is_delivered():
    buckets, _detail = classify_entries({"03/11/2026"}, [_row("03/11/2026")], "")
    assert buckets[DELIVERED] == 1


def test_a_row_we_hold_but_do_not_include_is_the_defect_bucket():
    """The one bucket that is unambiguously lost content: we have the pages, categorised, and ship
    nothing for them."""
    buckets, detail = classify_entries(
        {"03/11/2026"}, [_row("03/11/2026", included=False)], "DATE OF EXAM: 03/11/2026"
    )
    assert buckets[EXCLUDED] == 1
    assert detail[EXCLUDED] == ["03/11/2026"]
    assert buckets[RE_DATED] == 0, "a row we hold is never absent, whatever the OCR says"


def test_a_date_we_do_not_carry_but_the_document_does_is_re_dated_not_lost():
    """Ten of the eleven original 'missing' encounters were this: we deliver the pages under a
    different date. Reporting it as lost content overstates the defect by an order of magnitude."""
    buckets, detail = classify_entries(
        {"03/12/2026"}, [_row("03/11/2026")], "Printed on 3/12/26 ... DATE OF EXAM: 03/11/2026"
    )
    assert (buckets[RE_DATED], buckets[UNFOUND]) == (1, 0)
    assert detail[RE_DATED] == ["03/12/2026"]


def test_a_date_in_neither_our_rows_nor_the_ocr_is_unfound():
    buckets, _detail = classify_entries({"01/05/2026"}, [_row("01/06/2026")], "exam on 01/06/2026")
    assert _nonzero(buckets) == {UNFOUND: 1, EXTRA: 1}


def test_a_date_we_deliver_that_the_human_never_wrote_up_is_extra():
    """Expected to be noisy: one human entry per encounter versus one summary per document."""
    buckets, detail = classify_entries(set(), [_row("03/11/2026"), _row("03/12/2026")], "")
    assert buckets[EXTRA] == 2
    assert detail[EXTRA] == ["03/11/2026", "03/12/2026"]


def test_an_excluded_row_is_not_counted_as_extra():
    """`extra` is about what we DELIVER. A row we hold and drop is already the excluded bucket, and
    counting it twice would make over- and under-inclusion look like the same thing."""
    buckets, _detail = classify_entries(set(), [_row("03/11/2026", included=False)], "")
    assert buckets[EXTRA] == 0


def test_rows_with_no_date_are_ignored_rather_than_matched():
    """The field spec writes "-" when the document states no date; that is not a date."""
    buckets, _detail = classify_entries({"03/11/2026"}, [_row("-"), _row("-", included=False)], "")
    assert buckets[UNFOUND] == 1
    assert buckets[EXTRA] == 0


def test_several_rows_on_one_date_count_the_date_once():
    """A same-day batch is three rows and one human entry; the comparison is per DATE, so this must
    not report the date three times."""
    buckets, _detail = classify_entries(
        {"03/11/2026"}, [_row("03/11/2026"), _row("03/11/2026"), _row("03/11/2026")], ""
    )
    assert buckets[DELIVERED] == 1


def test_one_delivered_row_on_a_date_rescues_the_date_from_excluded():
    """If a visit produced three documents and we deliver even one of them, the human's entry for
    that date IS represented. Calling it excluded because a sibling row was dropped would report
    lost content that is not lost."""
    buckets, _detail = classify_entries(
        {"03/11/2026"},
        [_row("03/11/2026", included=False), _row("03/11/2026", included=True)],
        "",
    )
    assert buckets[DELIVERED] == 1
    assert buckets[EXCLUDED] == 0
