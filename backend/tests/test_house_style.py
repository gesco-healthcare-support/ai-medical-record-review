"""Unit tests for the deterministic capitalisation transform (services.house_style).

Pure string work: no DB, no model. Every case here came from the measured offender list in stored
summaries on 2026-07-30 (VITAMIN 24, LASIX 12, LIVER 12, TRANSPLANT 9, KEFLEX 7) or from the two
examples Adrian highlighted in a live export (an employer and an occupation in capitals).
"""

import pytest

from app.services.house_style import sentence_case_caps_runs as recase


@pytest.mark.parametrize(
    "given,expected",
    [
        # A clinical phrase mid-sentence: lowercased, no stray capital introduced.
        ("Reported LIVER TRANSPLANT last year.", "Reported liver transplant last year."),
        # ...and at the start of a point, where it does take a capital.
        (
            "**Diagnoses**: CARPAL TUNNEL SYNDROME on the right.",
            "**Diagnoses**: Carpal tunnel syndrome on the right.",
        ),
        # An acronym inside a run survives; the rest does not.
        (
            "The study was an MRI OF THE LUMBAR SPINE without contrast.",
            "The study was an MRI of the lumbar spine without contrast.",
        ),
        # A run that is ENTIRELY acronyms is left alone.
        ("Studies included EMG NCS ECG results.", "Studies included EMG NCS ECG results."),
        # Lone words of 4+ letters are title-cased, so a drug name reads correctly.
        ("Prescribed VITAMIN D and LASIX daily.", "Prescribed Vitamin D and Lasix daily."),
        # Adrian's two highlighted examples.
        (
            "**Employer**: CEDAR RIDGE LOGISTICS, INC. **Occupation**: GENERAL LABORER.",
            "**Employer**: Cedar Ridge Logistics, Inc. **Occupation**: General laborer.",
        ),
        # A facility name is title-cased, not lowercased: shouting is a lesser error than "sharp
        # imaging medical group".
        ("Imaging at SHARP IMAGING MEDICAL GROUP.", "Imaging at Sharp Imaging Medical Group."),
        # A hyphenated compound is handled as one token rather than cut in half.
        ("FOLLOW-UP in four weeks.", "Follow-up in four weeks."),
    ],
)
def test_recasing(given, expected):
    assert recase(given) == expected


@pytest.mark.parametrize(
    "text",
    [
        "A PR-2 report and an EMG/NCS of the right upper extremity.",
        "Findings at L4-L5 and L5 S1 were noted.",
        "MRI of the left shoulder w/o contrast.",
        "Diagnoses: 1. BMI 42.5, severe obesity equivalent. 2. TFCC tear.",
        "Right wrist flexion was 70 degrees and extension 65 degrees.",
        "",
    ],
)
def test_text_that_must_not_change(text):
    # Acronym compounds, spinal levels, measurements and the BMI diagnosis all have to survive: a
    # transform that mangles "EMG/NCS" or "L4-L5" reads as a typo in a medico-legal document.
    assert recase(text) == text


def test_a_lone_short_capital_is_assumed_to_be_an_acronym():
    # Below four letters, an unknown capitalised token is far more likely an acronym than a shouted
    # word, and the measured offenders were all longer. "TMJ" is not in the allowlist and must survive.
    assert recase("Reported TMJ pain.") == "Reported TMJ pain."


def test_the_number_in_a_measurement_is_never_touched():
    # The whole point of the ROM rule is that the value is copied exactly; the transform must not be
    # the thing that changes it.
    given = "**Physical Examination**: LEFT ANKLE DORSIFLEXION was reduced at 5 degrees."
    out = recase(given)
    assert "5 degrees" in out
    assert "Left ankle dorsiflexion" in out


# --- one paragraph --------------------------------------------------------------------------
# Adam Flake, 2026-09-24: "some strange formatting happening in the 06/18/26 PT evaluation report".
# The delivered body broke the one-paragraph rule with line breaks and headings that introduced
# nothing; the reviewer's own correction ran the content together inline with those headings gone.
from app.services.house_style import one_paragraph  # noqa: E402

_PT_EVAL_SHAPE = (
    "**DOI**: 01/02/2026. **Subjective Findings**: pain 6/10.\n"
    "**Objective Findings**:\n"
    "**Range of Motion**:\n"
    "Lumbar: flexion 50; extension 10\n"
    "Cervical: within limits.\n"
    "- Grip 40 lbs\n"
    "- Pinch 12 lbs\n"
    "**Diagnosis**: lumbar strain.\n"
    "**Assessment**:"
)


def test_a_multi_line_summary_becomes_one_paragraph():
    """DEMONSTRATES the fix on the reported shape: no line break survives, list markers go with
    their break, and each join is punctuated so two items do not run into one clause."""
    flat = one_paragraph(_PT_EVAL_SHAPE)
    assert "\n" not in flat
    assert flat == (
        "**DOI**: 01/02/2026. **Subjective Findings**: pain 6/10. **Range of Motion**: "
        "Lumbar: flexion 50; extension 10; Cervical: within limits. Grip 40 lbs; Pinch 12 lbs. "
        "**Diagnosis**: lumbar strain."
    )


def test_a_heading_that_introduces_nothing_is_removed():
    """DEMONSTRATES the empty-heading half, including one at the very end."""
    flat = one_paragraph(_PT_EVAL_SHAPE)
    assert "**Objective Findings**" not in flat
    assert "**Assessment**" not in flat
    assert one_paragraph("**Plan**: PT. **Work Status**:") == "**Plan**: PT."


@pytest.mark.parametrize(
    "text",
    [
        "**DOI**: 01/02/2026. **Diagnoses**: strain. **Treatment Plan**: PT twice weekly.",
        "A single paragraph with no headings at all.",
        "**Impression**: No acute fracture.",
        "",
    ],
)
def test_a_conforming_summary_passes_through_unchanged(text):
    """GUARD: a summary already in house style is returned byte for byte."""
    assert one_paragraph(text) == text


def _stored(**fields):
    from app.models import Summary

    base = dict(title="A REPORT", text="", row_start=1, row_end=2, row_category="5")
    base.update(fields)
    return Summary(**base)


def test_the_export_flattens_a_summary_stored_before_the_fix():
    """DEMONSTRATES the export half: a multi-line body already on the box ships as one paragraph,
    so stored records do not need re-running."""
    from app.api.documents import _export_title_and_text

    _, text = _export_title_and_text(_stored(text=_PT_EVAL_SHAPE))
    assert "\n" not in text
    assert "**Objective Findings**" not in text


def test_the_export_leaves_a_deposition_and_a_reviewer_edit_alone():
    """GUARD: a deposition is grouped by page on purpose, and a reviewer's own edit is theirs."""
    from app.api.documents import _export_title_and_text

    grouped = "Pages 1-3: testimony.\nPages 4-6: more testimony."
    _, text = _export_title_and_text(_stored(text=grouped, row_category="9"))
    assert text == grouped
    _, text = _export_title_and_text(
        _stored(text="Raw.\nBody.", edited_text="Line one.\nLine two.")
    )
    assert text == "Line one.\nLine two."
