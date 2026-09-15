"""The list page that goes in front of a combined bundle PDF.

Asked whether the download folder should carry the combined PDF or a written report for
Diagnostics, the reviewers answered: "for the diagnostics we just want a PDF with the documents
together. Preferably with a cover page that includes a list of reports", and attached one of
their own. Theirs is a bordered three-column table - Date | PROVIDER | REPORT TITLE - under the
heading LIST OF DIAGNOSTIC AND OPERATIVE REPORTS, continuing onto a second page when the list
runs long. These pin that shape.
"""

import io

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from app.services.bundles import (
    COVER_COLUMNS,
    build_bundle_pdf,
    build_cover_pdf,
    cover_entries,
    split_deliverable_title,
)

# `AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.` - the shape TITLE_PROMPT specifies.
_FULL = "JANE SMITH, M.D. ACME IMAGING CENTER. MRI OF THE CERVICAL SPINE WITHOUT CONTRAST."


def _source_pdf(pages: int) -> io.BytesIO:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    return buffer


def _text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def test_the_document_type_is_taken_from_the_end_and_the_rest_identifies_the_provider():
    """The split follows the prompt's own contract rather than recognising a study by name.
    TITLE_PROMPT puts DOCUMENT TYPE last and says an absent element is omitted along with its
    separator, so the type is the last element whatever else survived."""
    provider, report = split_deliverable_title(_FULL)
    assert report == "MRI OF THE CERVICAL SPINE WITHOUT CONTRAST"
    assert provider == "ACME IMAGING CENTER – JANE SMITH, M.D."


def test_the_facility_leads_the_provider_column_when_the_author_is_identifiable():
    """Their column reads facility first and ours states the author first, so the two are
    swapped - but ONLY on the credential, which is the one unambiguous sign of a person."""
    provider, _ = split_deliverable_title("PAT LEE, PSY.D. BEHAVIORAL HEALTH. PSYCH EVALUATION.")
    assert provider == "BEHAVIORAL HEALTH – PAT LEE, PSY.D."


def test_a_credential_keeps_its_final_period():
    """GUARDS a defect the split creates and a client would read: `M.D.` ends with the very
    character the separator is, so splitting on ". " eats it and the column said `JANE SMITH,
    M.D` - a typo in a delivered document."""
    for title, ending in (
        (_FULL, "M.D."),
        ("LISA CUDDY, D.O. MERCY RADIOLOGY. X-RAY OF THE CHEST.", "D.O."),
        ("A DOE, D.D.S. DENTAL GROUP. PANORAMIC X-RAY.", "D.D.S."),
    ):
        assert split_deliverable_title(title)[0].endswith(ending)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # No author: two elements, so nothing is reordered and the facility stays where it is.
        ("ACME IMAGING CENTER. CT OF THE HEAD.", ("ACME IMAGING CENTER", "CT OF THE HEAD")),
        # Nothing to split. Measured on one server, 72 of 268 diagnostic titles arrive like this.
        ("X-RAY OF THE CHEST, 1 VIEW.", ("", "X-RAY OF THE CHEST, 1 VIEW")),
        ("", ("", "")),
        (None, ("", "")),
    ],
)
def test_a_title_that_will_not_split_is_left_whole_rather_than_guessed_at(title, expected):
    assert split_deliverable_title(title) == expected


def test_the_list_reads_what_the_deliverable_states_not_what_the_row_holds():
    """The row carries what segmentation read off the page; the deliverable carries the formatted
    header line the summarizer writes and the date the MRR itself prints. Only the second pair
    splits into their columns AND agrees with the report the client reads beside it.

    The row's own date differs here on purpose - with both set to the same value the test would
    pass whichever one the code read."""
    rows = [{"start": 4, "end": 6, "date": "09/09/99", "title": "mri report"}]
    assert cover_entries(rows, {4: ("01/13/15", _FULL)}) == [
        (
            "01/13/15",
            "ACME IMAGING CENTER – JANE SMITH, M.D.",
            "MRI OF THE CERVICAL SPINE WITHOUT CONTRAST",
        )
    ]


def test_an_unsummarized_row_still_gets_a_line_from_its_own_title():
    """A cover page naming the documents roughly beats no cover page, so a record whose rows have
    not been summarized falls back rather than dropping the row out of the list."""
    rows = [{"start": 7, "end": 9, "date": "02/02/20", "title": "OPERATIVE REPORT"}]
    assert cover_entries(rows, {}) == [("02/02/20", "", "OPERATIVE REPORT")]


def test_the_cover_page_is_their_table():
    """DEMONSTRATES the page against the structure of the one they sent: the heading, the three
    column labels, and real cell borders rather than laid-out text."""
    pdf = build_cover_pdf(
        "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS",
        cover_entries(
            [
                {"start": 1, "end": 2, "date": "05/16/11", "title": ""},
            ],
            {1: ("05/16/11", _FULL)},
        ),
    )
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = doc[0].get_text()
    assert "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS" in text
    for column in COVER_COLUMNS:
        assert column in text
    assert "05/16/11" in text
    # Their page is a bordered table; a borderless run of text would satisfy the strings above.
    assert len(doc[0].get_drawings()) > 10


def test_a_long_list_runs_onto_a_second_page():
    """Theirs does, and any record with enough imaging will. Story paginates the table itself,
    which is why this is one table rather than a hand-placed grid."""
    entries = [
        (
            f"01/{n % 12 + 1:02d}/20",
            "A VERY LONG IMAGING FACILITY NAME – SOME DOCTOR, M.D.",
            "MRI OF THE CERVICAL SPINE WITHOUT CONTRAST",
        )
        for n in range(60)
    ]
    doc = pymupdf.open(stream=build_cover_pdf("LIST OF REPORTS", entries), filetype="pdf")
    assert doc.page_count > 1


def test_the_cover_goes_in_front_of_the_documents():
    """The whole point of the page: it is the first thing opened, not an appendix."""
    rows = [{"start": 2, "end": 3, "date": "05/16/11", "title": "x"}]
    cover = build_cover_pdf(
        "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS", cover_entries(rows, {2: ("05/16/11", _FULL)})
    )
    combined = build_bundle_pdf(_source_pdf(5), rows, cover=cover).getvalue()

    reader = PdfReader(io.BytesIO(combined))
    assert len(reader.pages) == 1 + 2  # the list, then the two document pages
    assert "LIST OF DIAGNOSTIC AND OPERATIVE REPORTS" in _text(combined).split("\n")[0]


def test_without_a_cover_the_bundle_is_exactly_what_it_was():
    """GUARD. Depositions was not asked for a cover page, so that bundle must come out byte for
    byte the way it did before this existed."""
    rows = [{"start": 2, "end": 3, "date": "", "title": ""}]
    assert len(PdfReader(build_bundle_pdf(_source_pdf(5), rows)).pages) == 2


def test_the_field_specs_empty_sentinel_is_read_as_empty():
    """`_store_rows` writes the literal "-" for a row field the reviewer left blank, so a naive
    read put a bordered table of dashes in front of the documents. `reporting.date_label`
    special-cases the same sentinel."""
    rows = [{"start": 1, "end": 2, "date": "-", "title": "-"}]
    assert cover_entries(rows, {}) == [("", "", "")]


@pytest.mark.parametrize(
    ("title", "provider"),
    [
        ("JANE SMITH, M.D. ST. MARY'S HOSPITAL. MRI OF THE KNEE.", "ST. MARY'S HOSPITAL"),
        ("JOHN DOE, D.O. MT. SINAI MEDICAL CENTER. CT HEAD.", "MT. SINAI MEDICAL CENTER"),
        ("A B, M.D. U.S. HEALTHWORKS. X-RAY.", "U.S. HEALTHWORKS"),
    ],
)
def test_an_abbreviated_facility_name_is_not_torn_in_half(title, provider):
    r"""DEMONSTRATES a defect a client would have read. `re.split(r"\.\s+")` cannot tell an
    element separator from an abbreviation's own period, so the PROVIDER column said
    `ST – MARY'S HOSPITAL` - the facility split across the dash that is supposed to separate
    it from the doctor. `ST.`, `MT.` and `U.S.` are ordinary facility names."""
    assert split_deliverable_title(title)[0].startswith(provider)


def test_a_reviewer_edited_lowercase_title_keeps_its_credential_period():
    """GUARDS the fix against the case it originally missed. `_CREDENTIALS` is case-insensitive
    so a lowercase credential triggers the reorder, while the restoration was uppercase-only -
    which left the very `M.D` typo this exists to prevent, in lower case.

    Reachable: `Summary.effective_title()` returns `edited_title` FIRST, above the verified and
    the raw model title, and a reviewer editing a title in the UI is not governed by the prompt's
    ALL CAPS instruction."""
    assert split_deliverable_title("r chase, m.d. valley imaging. ct abdomen.")[0] == (
        "valley imaging – r chase, m.d."
    )


def test_a_facility_ending_in_a_lone_letter_gains_no_period():
    """The restoration fires on an abbreviation - a lone letter with a period in FRONT of it -
    not on any trailing capital. `IMAGING CENTER A`, `SUITE B` and `BUILDING C` are not
    abbreviations, and the earlier rule's justification ("a facility name ends in a word") was
    simply untrue of them."""
    assert split_deliverable_title("JANE SMITH, M.D. IMAGING CENTER A. MRI KNEE.")[0] == (
        "IMAGING CENTER A – JANE SMITH, M.D."
    )


def test_a_short_final_element_stays_the_report_title():
    """GUARD. The rejoin only fires when a short fragment has something AFTER it, so a genuinely
    two-character document type is not swallowed into the provider."""
    assert split_deliverable_title("JANE SMITH, M.D. ACME IMAGING. CT.") == (
        "ACME IMAGING – JANE SMITH, M.D.",
        "CT",
    )


def test_the_provider_parts_are_joined_with_their_own_en_dash():
    """Not a mistyped hyphen: U+2013 appears 19 times in the reference list they sent, in exactly
    this position, and matching their document is the point of the page."""
    assert "–" in split_deliverable_title(_FULL)[0]
