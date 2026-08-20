"""The date-in-source narrowing, unit tested. No DB, no Vertex.

These exist because the naive version of this check reported 33.7% of all row dates as missing and the
honest figure is 1.6% - a factor of twenty, entirely from the check's own blind spots. Each test below
is one of those blind spots, pinned so the number cannot silently regress to the wrong one.
"""

import sys
from pathlib import Path

import pytest

# scripts/ is not a package and not on the path; the eval scripts are run by path in the container.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from date_in_source import (  # noqa: E402
    ABSENT,
    ELSEWHERE,
    IN_ROW,
    NO_DATE,
    NOT_DELIVERED,
    DAY_DIFFERS,
    WITHIN_MARGIN,
    classify_date,
    date_patterns,
    summarise,
)


def _call(date="03/11/2026", *, row="", margin=None, doc=None, summarized=True):
    """margin/doc default to containing the row, which is how real page ranges nest."""
    margin = row if margin is None else margin
    doc = margin if doc is None else doc
    return classify_date(
        date, row_text=row, margin_text=margin, doc_text=doc, summarized=summarized
    )


# --------------------------------------------------------------------------- the separator blind spot
@pytest.mark.parametrize(
    "rendered",
    [
        "Visit Date: 3/11/2026",  # unpadded
        "Visit Date: 03/11/2026",  # padded
        "Visit Date: 3/11/26",  # two-digit year
        "Visit Date: 03-11-2026",  # dashes
        "Visit Date: 03.11.2026",  # dots
        "Visit Date: 3 / 11 / 2026",  # spaces around the separator
        "DATE OF EXAM 03/11/2026 PATIENT",  # embedded in a run of text
    ],
)
def test_a_date_counts_as_present_in_every_form_ocr_produces(rendered):
    """Slashes-only matching is what turned 17.6% into 33.7%. OCR does not always emit "/"."""
    assert _call(row=rendered) == IN_ROW


def test_a_spelled_out_month_counts_as_present():
    """Another 1.5x: some documents write the date out, and one such row was in the sample."""
    assert _call(row="Dictated March 11, 2026 by the examiner") == IN_ROW
    assert _call(row="dictated march 11 2026") == IN_ROW


@pytest.mark.parametrize("other", ["03/12/2026", "3/1/2026", "03/11/2025", "13/11/2026"])
def test_a_different_date_does_not_count(other):
    """The looseness must not become a wildcard - a neighbouring date is not this date."""
    assert _call(row=f"Printed on {other}") != IN_ROW


def test_the_day_is_not_matched_as_a_prefix():
    """1 must not match 11: the fix for unpadded months must not make the day a prefix match."""
    assert _call(date="03/01/2026", row="Visit Date: 3/11/2026") != IN_ROW
    assert _call(date="03/11/2026", row="Visit Date: 3/1/2026") != IN_ROW


# ------------------------------------------------------------------------- the boundary blind spot
def test_a_date_on_an_adjacent_page_is_a_boundary_artefact_not_an_invention():
    """A segmentation boundary landing a page late is a DIFFERENT defect and must not be reported as
    a fabricated date. 100 of 1,459 rows sat in this bucket."""
    assert _call(row="no dates here", margin="Visit Date: 03/11/2026") == WITHIN_MARGIN


def test_a_date_elsewhere_in_the_document_is_context_bleed_not_an_invention():
    """The model saw a whole window, so a date from a neighbouring document is a real but separate
    failure - it read something, just not from this row."""
    assert _call(row="none", margin="none", doc="Visit Date: 03/11/2026") == ELSEWHERE


# ----------------------------------------------------------------------------- the impact blind spot
def test_a_row_nobody_summarizes_is_not_a_deliverable_defect():
    """60 of the 89 nowhere-found rows were category 100, excluded by default. A wrong date on a row
    that is never summarized reaches nobody, and counting it triples the apparent rate."""
    assert _call(row="no dates", summarized=False) == NOT_DELIVERED


def test_a_day_only_disagreement_gets_its_own_bucket_without_a_verdict():
    """Same month and year, different day. Two readings and this check cannot separate them, so the
    bucket is named for what it observes.

    An earlier version called this `ocr_repair` and treated it as benign, which buried a real error:
    one row emitted 06/08 against text that plainly reads 06/18 - the model had a clean date and
    produced a different one. Calling that a repair would have excused it."""
    assert _call(date="03/11/2026", row="Visit Date: 03/17/2026") == DAY_DIFFERS
    # the real case: a clean date in the source, a different day emitted
    assert _call(date="06/08/2026", row="Visit Date: 06/18/2026") == DAY_DIFFERS


def test_a_date_with_no_trace_at_all_is_the_defect():
    """The one bucket with no innocent explanation left: not in the row, not adjacent, not elsewhere
    in the document, and not even the month and year. Segmentation reads OCR text only, so there was
    nothing else to read it from.

    Note the month differs here as well as the day - that is what separates this from DAY_DIFFERS."""
    assert _call(date="06/08/2026", row="Visit Date: 11/18/2026 ... Printed 11/18/2026") == ABSENT


def test_a_row_with_no_date_is_not_counted():
    """ "-" is the correct answer when a document states no date, so it is not a finding."""
    assert _call(date="-", row="anything") == NO_DATE
    assert _call(date="", row="anything") == NO_DATE


# ------------------------------------------------------------------------------------- the patterns
@pytest.mark.parametrize(
    "bad", ["-", "", "2026-03-11", "03/11/26", "3/11", "aa/bb/cccc", "13/40/2026"]
)
def test_unparseable_dates_are_refused_rather_than_guessed(bad):
    assert date_patterns(bad) is None


def test_patterns_are_built_for_a_valid_date():
    got = date_patterns("03/11/2026")
    assert got is not None and set(got) == {"strict", "loose", "spelled", "month_only"}


# --------------------------------------------------------------------------------------- the report
def test_the_report_headlines_the_narrowest_number():
    """Anyone quoting the first line of this report overstates the defect twentyfold, so the headline
    has to be the last bucket rather than the first."""
    out = summarise(
        [IN_ROW] * 89
        + [WITHIN_MARGIN] * 5
        + [NOT_DELIVERED] * 2
        + [NO_DATE]
        + [ABSENT] * 2
        + [DAY_DIFFERS]
    )
    assert "HEADLINE: 2 of 100 rows (2.0%)" in out
    assert "plus 1 where only the DAY differs" in out
    # and it must say the range rather than let the reader take 2 as the whole answer
    assert "between 2 and 3" in out
    assert out.index("in_row") < out.index("HEADLINE")


def test_the_report_survives_an_empty_run():
    assert summarise([]) == "no rows checked"
