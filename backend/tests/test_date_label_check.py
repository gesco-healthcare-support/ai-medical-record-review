"""The labelled-date check, unit tested. No DB, no Vertex, no page text on disk.

The rule under test: the encounter date is the date the encounter happened. A document written on
08/21 and signed on 08/23 has an encounter date of 08/21, so taking the signature date is wrong even
though it is the more prominent date on the page.
"""

import sys
from pathlib import Path

import pytest

# scripts/ is not a package and not on the path; the eval scripts are run by path in the container.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "eval"))

from date_label_check import (  # noqa: E402
    NO_DATE,
    NO_LABELS,
    TOOK_ENCOUNTER,
    TOOK_INJURY,
    TOOK_PRINT,
    TOOK_SIGNATURE,
    UNLABELLED,
    classify_row,
    labelled_dates,
    normalise,
    recoverable,
)


# --- normalising ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("8", "21", "2026"), "08/21/2026"),
        (("08", "21", "26"), "08/21/2026"),
        (("12", "31", "1999"), "12/31/1999"),
    ],
)
def test_normalise_accepts_the_forms_records_use(parts, expected):
    assert normalise(*parts) == expected


@pytest.mark.parametrize(
    "parts",
    [
        ("13", "01", "2026"),  # month 13
        ("00", "01", "2026"),  # month 0
        ("01", "32", "2026"),  # day 32
        ("01", "01", "1850"),  # year out of range
        ("x", "01", "2026"),  # not a number
    ],
)
def test_normalise_rejects_a_non_date(parts):
    assert normalise(*parts) is None


# --- finding labelled dates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "DATE OF EXAM: 08/21/2026",
        "Date of Examination: 08/21/2026",
        "EXAM DATE 8/21/26",
        "Visit Date: 08-21-2026",
        "DATE OF VISIT .... 08/21/2026",
        "Date of Service: 08.21.2026",
        "DOS: 8/21/26",
        "D.O.S. 08/21/2026",
        "Date of Encounter: 08/21/2026",
        "Encounter Date | 08/21/2026",
        "Date Seen: 08/21/2026",
    ],
)
def test_every_encounter_label_spelling_is_found(text):
    """Records use all of these. A reader that only knows "date of exam" measures a third of them."""
    assert labelled_dates(text)["encounter_date"] == {"08/21/2026"}


def test_the_signature_date_is_collected_separately():
    text = "DATE OF EXAM: 08/21/2026\nElectronically signed by A. PHYSICIAN 08/23/2026"
    found = labelled_dates(text)
    assert found["encounter_date"] == {"08/21/2026"}
    assert found["signature_date"] == {"08/23/2026"}


@pytest.mark.parametrize(
    "text",
    [
        "Signature Date: 08/23/2026",
        "Date Signed: 08/23/2026",
        "Signed on 08/23/2026",
        "E-Signed 8/23/26",
    ],
)
def test_signature_label_spellings(text):
    assert labelled_dates(text)["signature_date"] == {"08/23/2026"}


@pytest.mark.parametrize(
    "text",
    ["Printed on 08/23/2026", "Print Date: 08/23/2026", "Date Received 08/23/2026"],
)
def test_administrative_label_spellings(text):
    assert labelled_dates(text)["print_or_fax_date"] == {"08/23/2026"}


def test_an_unlabelled_date_is_not_collected():
    """This measures LABELLED dates. A bare date in a header says nothing about the rule, and
    collecting it would let any date on the page count as a match."""
    found = labelled_dates("ACME CLINIC\n08/21/2026\nprogress note")
    assert not any(found.values())


def test_a_label_far_from_its_date_is_not_paired():
    """The gap is bounded. Without that, a label at the top of a page pairs with a date at the
    bottom and the measurement becomes 'is there a date on this page'."""
    text = "DATE OF EXAM:" + " filler text " * 20 + "08/21/2026"
    assert not labelled_dates(text)["encounter_date"]


def test_a_label_does_not_cross_a_line_break_to_find_a_date():
    """The gap excludes newlines: a labelled field with an EMPTY value must not borrow the date from
    the next line, which is usually a different field."""
    assert not labelled_dates("DATE OF EXAM:\n08/21/2026")["encounter_date"]


def test_several_dates_under_one_label_are_all_kept():
    text = "Date of Service: 08/21/2026 ... Date of Service: 08/28/2026"
    assert labelled_dates(text)["encounter_date"] == {"08/21/2026", "08/28/2026"}


# --- classifying a row --------------------------------------------------------------------------


def _labels(**kwargs):
    base = {
        "encounter_date": set(),
        "signature_date": set(),
        "print_or_fax_date": set(),
        "date_of_injury": set(),
    }
    base.update({k: set(v) for k, v in kwargs.items()})
    return base


def test_taking_the_labelled_encounter_date_is_the_good_bucket():
    assert classify_row("08/21/2026", _labels(encounter_date=["08/21/2026"])) == TOOK_ENCOUNTER


def test_taking_the_signature_date_is_the_defect_this_measures():
    """The 08/21-written, 08/23-signed case: the signature date is the more prominent one and the
    prompt currently points at it."""
    got = classify_row(
        "08/23/2026", _labels(encounter_date=["08/21/2026"], signature_date=["08/23/2026"])
    )
    assert got == TOOK_SIGNATURE


def test_a_same_day_signature_is_not_counted_as_a_signature_error():
    """A document signed the day it was written carries the same date under both labels. Counting
    that as taking the signature date would invent defects - so an encounter match always wins."""
    got = classify_row(
        "08/21/2026", _labels(encounter_date=["08/21/2026"], signature_date=["08/21/2026"])
    )
    assert got == TOOK_ENCOUNTER


def test_taking_a_print_date_is_its_own_bucket():
    got = classify_row(
        "08/23/2026", _labels(encounter_date=["08/21/2026"], print_or_fax_date=["08/23/2026"])
    )
    assert got == TOOK_PRINT


def test_taking_the_date_of_injury_is_its_own_bucket():
    """The prompt forbids this one explicitly, so it is worth counting separately from the rest."""
    got = classify_row(
        "02/15/2026", _labels(encounter_date=["08/21/2026"], date_of_injury=["02/15/2026"])
    )
    assert got == TOOK_INJURY


def test_a_date_matching_no_label_is_not_a_defect():
    """Plenty of documents put the encounter date in an unlabelled header. Calling that wrong would
    report the majority of correct rows as errors."""
    got = classify_row("08/25/2026", _labels(encounter_date=["08/21/2026"]))
    assert got == UNLABELLED


def test_pages_with_no_labels_at_all_are_their_own_bucket():
    assert classify_row("08/21/2026", _labels()) == NO_LABELS


@pytest.mark.parametrize("assigned", ["-", "", None])
def test_a_row_with_no_date_is_not_checked(assigned):
    """ "-" is the CORRECT answer for a document that states no date; it is not a date claim."""
    assert classify_row(assigned, _labels(encounter_date=["08/21/2026"])) == NO_DATE


# --- recoverability -----------------------------------------------------------------------------


def test_recoverable_means_an_encounter_label_was_available():
    """The number that decides whether the prompt wording is worth changing: a row that took the
    signature date while an encounter label sat on the same pages is an avoidable miss."""
    assert recoverable(_labels(encounter_date=["08/21/2026"], signature_date=["08/23/2026"]))


def test_not_recoverable_when_only_the_wrong_labels_are_present():
    """Nothing better was on the page, so no prompt change fixes these - they must not be counted
    in the headline."""
    assert not recoverable(_labels(signature_date=["08/23/2026"]))
