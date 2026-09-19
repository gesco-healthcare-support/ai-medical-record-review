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
    _COVER_CONTENT,
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


# The three below are about GEOMETRY, and they exist because the content assertions above could
# not see the defect Adam reported: every column had collapsed to its minimum width, so the page
# read as overlapping gibberish - and the heading, the three labels, the date and the border
# count were all still present and correct. A page can contain everything it should and still be
# unreadable.

_SAMPLE_ROWS = [
    ("04/25/18", "ROLLING OAKS RADIOLOGY – TIRMAN, F., M.D.", "FLUOROSCOPIC RIGHT WRIST"),
    ("06/02/18", "ROLLING OAKS RADIOLOGY – SHAH, DISHANT, M.D.", "X-RAY OF THE RIGHT WRIST"),
]


def _column_edges(page) -> list[float]:
    """The x of every vertical rule the table draws, i.e. the real column boundaries.

    Read off the drawn borders rather than off word positions: a collapsed column still places
    words, which is how the defect stayed invisible to a text assertion."""
    edges = set()
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if (
                item[0] == "l"
                and abs(item[1].x - item[2].x) < 0.5
                and abs(item[1].y - item[2].y) > 3
            ):
                edges.add(round(item[1].x, 1))
            elif item[0] == "re" and item[1].width < 2 and item[1].height > 3:
                edges.add(round(item[1].x0, 1))
    return sorted(edges)


def test_every_column_is_wide_enough_to_hold_its_own_content():
    """DEMONSTRATES the defect Adam reported. Story reads the column widths off the FIRST row,
    and they were declared on `td` only, so the header cells carried none and every column
    collapsed: measured 8.7pt / 11.9pt / 448.4pt against a 504pt content rect. Date could not
    hold `04/25/18` and PROVIDER could not hold one word, so both wrapped a word per line and
    the three header labels overlapped each other.

    A date is the narrowest thing the table must hold, so it is the honest floor: anything that
    cannot fit `04/25/18` on one line cannot fit anything."""
    doc = pymupdf.open(
        stream=build_cover_pdf("LIST OF DIAGNOSTIC AND OPERATIVE REPORTS", _SAMPLE_ROWS),
        filetype="pdf",
    )
    edges = _column_edges(doc[0])
    # Outer border, then a rule between each pair of columns, then the outer border again; the
    # widths that matter are the gaps that are not the 3pt border/padding runs.
    widths = [b - a for a, b in zip(edges, edges[1:]) if b - a > 5]
    assert len(widths) == len(COVER_COLUMNS), f"expected 3 real columns, got {widths}"
    date_width = min(widths)
    assert date_width > 40, f"the narrowest column is {date_width:.1f}pt - too narrow for a date"


def test_no_cell_wraps_one_word_per_line():
    """DEMONSTRATES the visible symptom. With the columns collapsed, `ROLLING OAKS RADIOLOGY`
    came out stacked vertically one word to a line. Two rows plus a header is three table rows,
    so a correct render is a handful of lines; the broken one was nine and climbing with the
    longest provider name."""
    doc = pymupdf.open(
        stream=build_cover_pdf("LIST OF DIAGNOSTIC AND OPERATIVE REPORTS", _SAMPLE_ROWS),
        filetype="pdf",
    )
    words = [w for w in doc[0].get_text("words") if w[1] > 90]  # below the heading
    lines = {round(w[1], 1) for w in words}
    assert len(lines) <= 6, f"{len(lines)} text lines for 3 table rows - cells are wrapping"


def test_the_cover_table_spans_the_content_rect():
    """GUARD on the two constants `_cover_column_widths` derives from.

    Story ignores a percentage width, so the widths are absolute points, and absolute points
    only stay correct while the inset and the per-cell overhead hold. Both were measured off the
    rendered geometry rather than reasoned from the box model, so a MuPDF release is free to
    move them - and the failure would be silent, the table drifting past the right margin. This
    fails instead."""
    doc = pymupdf.open(
        stream=build_cover_pdf("LIST OF DIAGNOSTIC AND OPERATIVE REPORTS", _SAMPLE_ROWS),
        filetype="pdf",
    )
    edges = _column_edges(doc[0])
    assert edges, "the table drew no vertical rules at all"
    assert edges[-1] <= _COVER_CONTENT.x1 + 0.5, (
        f"the table reaches x={edges[-1]}, past the content rect at {_COVER_CONTENT.x1}"
    )
    # And it should actually USE the width it has, rather than huddling in the left third.
    assert edges[-1] - edges[0] > _COVER_CONTENT.width * 0.9


# --- The deposition title shape (reviewer-reported, 2026-09-18). ------------------------------
#
# TITLE_PROMPT now tells the model to write a deposition as "DEPOSITION OF <NAME>" with no AUTHOR
# and no FACILITY element, because the signature block on a transcript belongs to the certified
# shorthand reporter and the letterhead to the reporting service - so the general rule was naming
# the two people who appear on EVERY deposition and never the one whose testimony it is.
#
# That makes a ONE-ELEMENT title routine where it used to be a long tail, and this splitter is a
# consumer of the shape TITLE_PROMPT specifies. These pin that it stays graceful.


def test_a_deposition_title_puts_everything_under_the_report_title():
    """GUARD. A title with nothing to split must leave PROVIDER empty rather than inventing one.

    The behaviour already exists - its docstring records 72 of 268 titles arriving as a single
    element - but the deposition rule makes it load-bearing rather than incidental, so a future
    "always split something out" change has to fail here.
    """
    provider, report = split_deliverable_title("DEPOSITION OF JANE SMITH")
    assert provider == ""
    assert report == "DEPOSITION OF JANE SMITH"


def test_a_deposed_physician_keeps_their_name_in_one_element():
    """The deponent is often a treating physician, so the name arrives WITH a credential.

    The credential's own period must not be read as a separator - that is what `_elements`
    guards - so this stays one element and the provider column stays empty.

    Asserted as the splitter actually behaves, not as I first assumed: the trailing period is
    dropped, so this reads `M.D` rather than `M.D.`. That is pre-existing and general to any
    title whose LAST element ends in an abbreviation; the credential-period restoration runs on
    the provider column, which is empty here. Left alone deliberately - depositions are category
    9 and this cover page is built for the diagnostic bundle, so no deposition title reaches it
    today. Recorded rather than fixed so the next reader does not take it for a new defect.
    """
    provider, report = split_deliverable_title("DEPOSITION OF JANE SMITH, M.D.")
    assert provider == ""
    assert report == "DEPOSITION OF JANE SMITH, M.D"


def test_the_old_reporter_fronted_shape_still_parses():
    """GUARD, and the reason this change is not retroactive.

    Every deposition already stored was titled under the old rule, so the cover page must keep
    splitting that shape for as long as those rows exist. Only a NEW summary gets the new title.

    It also shows why the old titles read badly even here: the reorder fires only on a leading
    element carrying a RECOGNISED credential, and `C.S.R.` is not one of them - so the reporter's
    name is not identified as a person and our author-first order is kept. Compare the M.D. case
    below, which is reordered facility-first. Once a deposition stops naming the reporter this
    stops mattering, which is why the credential list is not being widened to include C.S.R.
    """
    provider, report = split_deliverable_title(
        "JANE SMITH, C.S.R. ACME COURT REPORTING. DEPOSITION."
    )
    assert provider.startswith("JANE SMITH, C.S.R.")
    assert provider.endswith("ACME COURT REPORTING")
    assert report == "DEPOSITION"

    reordered, _ = split_deliverable_title("JANE SMITH, M.D. ACME COURT REPORTING. DEPOSITION.")
    assert reordered.startswith("ACME COURT REPORTING")
    assert reordered.endswith("JANE SMITH, M.D.")
