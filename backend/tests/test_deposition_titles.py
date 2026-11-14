"""A deposition is titled "DEPOSITION OF <NAME>", with its volume, and nothing else.

Reviewer feedback, 2026-11-13: remove the court reporter, her licence number and the reporting
agency the title model put in front of the deponent, and add "Volume I, II, III ... if applicable".
The rule (`summarize_engine.deposition_title`) runs wherever `category_title` does - at generation,
on the audit's corrected title, at export and on the Summaries tab - and the volume is read from the
transcript's own cover where the summary is written (`cover_volume`). The record-level provider
passes leave a deposition's title alone (`consistent_titles`). Synthetic names only.
"""

import pytest

from app.services import ocr
from app.services import summarize_engine as se

_NO_ISSUES = {"fixed_text": "", "issues": [], "ok": True}


# --- the title rule ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        # the reported shape: reporter, licence number, agency, then the deponent
        "JANE R. ROE, CSR NO. 1234, SAMPLE DEPOSITION SOLUTIONS, DEPOSITION OF JOHN DOE",
        "JANE ROE, CSR NO. 1234, CERTIFIED SHORTHAND REPORTER, SAMPLE LEGAL SOLUTIONS, "
        "DEPOSITION OF JOHN DOE",
        "JANE ROE, CSR #1234, SAMPLE REPORTERS, DEPOSITION OF JOHN DOE",
        "JANE R. ROE, CSR NO. 1234, SAMPLE DEPO COURT REPORTING AND VIDEO DEPOSITION OF JOHN DOE",
        "JANE ROE, CSR, RPR. SAMPLE. DEPOSITION OF JOHN DOE",
        "JANE ROE, M.D. DEPOSITION OF JOHN DOE",
        # what the model writes after the name
        "DEPOSITION OF JOHN DOE.",
        "DEPOSITION OF JOHN DOE, JANUARY 5, 2026",
        "DEPOSITION OF JOHN DOE 01/05/2026 SAMPLE LEGAL",
        "DEPOSITION OF JOHN DOE. SAMPLE COURT REPORTERS",
        "DEPOSITION OF JOHN DOE (APPLICANT)",
        "DEPOSITION OF JOHN DOE ADJ1234567",
        "DEPOSITION OF JOHN DOE WCAB CASE NO. ADJ1234567",
        # how it was taken, before the name
        "VIDEOCONFERENCE DEPOSITION OF JOHN DOE",
        "DEPOSITION BY ZOOM OF JOHN DOE",
        "JANE ROE, CSR NO. 1234, SAMPLE DEPOSITION BY ZOOM VIDEOCONFERENCE OF JOHN DOE",
        "REMOTE DEPOSITION OF JOHN DOE",
        "TRANSCRIPT OF THE DEPOSITION OF JOHN DOE",
        "DEPOSITION TRANSCRIPT OF JOHN DOE",
    ],
)
def test_a_deposition_title_is_the_deponent_and_nothing_else(title):
    """DEMONSTRATES: everything but "DEPOSITION OF <NAME>" goes."""
    assert se.deposition_title(title) == "DEPOSITION OF JOHN DOE"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("DEPOSITION OF JOHN DOE VOLUME II", "DEPOSITION OF JOHN DOE, VOLUME II"),
        ("DEPOSITION OF JOHN DOE, VOL. 2", "DEPOSITION OF JOHN DOE, VOLUME II"),
        ("DEPOSITION OF JOHN DOE, VOLUME TWO", "DEPOSITION OF JOHN DOE, VOLUME II"),
        ("DEPOSITION OF JOHN DOE VOLUME NO. 3", "DEPOSITION OF JOHN DOE, VOLUME III"),
        ("JANE ROE, CSR. VOLUME I. DEPOSITION OF JOHN DOE", "DEPOSITION OF JOHN DOE, VOLUME I"),
        ("DEPOSITION BY ZOOM OF JOHN DOE VOLUME I 01/02/2026", "DEPOSITION OF JOHN DOE, VOLUME I"),
    ],
)
def test_a_deposition_s_volume_is_written_after_the_name(title, expected):
    """DEMONSTRATES: a volume named anywhere, in any spelling, is ", VOLUME <roman>"."""
    assert se.deposition_title(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("DEPOSITION OF JOHN SMITH, M.D.", "DEPOSITION OF JOHN SMITH, M.D."),
        ("DEPOSITION OF JOHN SMITH, M.D., PH.D. SAMPLE", "DEPOSITION OF JOHN SMITH, M.D., PH.D."),
        ("DEPOSITION OF JOHN DOE JR.", "DEPOSITION OF JOHN DOE JR."),
        ("DEPOSITION OF JOHN DOE, JR.", "DEPOSITION OF JOHN DOE, JR."),
        ("DEPOSITION OF DR. JANE ROE", "DEPOSITION OF DR. JANE ROE"),
        ("DEPOSITION OF JOHN Q. DOE", "DEPOSITION OF JOHN Q. DOE"),
        ("DEPOSITION OF MARY O'BRIEN-SMITH", "DEPOSITION OF MARY O'BRIEN-SMITH"),
        ("DEPOSITION OF THE APPLICANT", "DEPOSITION OF THE APPLICANT"),
        (
            "DEPOSITION OF CUSTODIAN OF RECORDS, SAMPLE HOSPITAL",
            "DEPOSITION OF CUSTODIAN OF RECORDS",
        ),
    ],
)
def test_the_whole_name_stays(title, expected):
    """GUARD: a credential, a suffix, an honorific, an initial and an "OF" inside the name stay -
    reviewers kept them."""
    assert se.deposition_title(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "DEPOSITION OF JOHN DOE",
        "DEPOSITION OF JOHN DOE, VOLUME II",
        # no deponent named: nothing to rewrite to, and the rule does not guess
        "JANE ROE, CSR NO. 1234 SAMPLE. DEPOSITION.",
        "JANE ROE, CERTIFIED SHORTHAND REPORTER, SAMPLE COURT REPORTERS DEPOSITION",
        "DEPOSITION TRANSCRIPT 01/02/2026 JOHN DOE",
        "NOTICE OF DEPOSITION",
        # an agency named "... DEPOSITION SOLUTIONS OF <PLACE>" is not a way of taking it
        "SAMPLE DEPOSITION SOLUTIONS OF CALIFORNIA",
        "DEPOSITION SUMMARY OF JOHN DOE",
        # not a deposition at all
        "TRANSCRIPT OF HEARING",
        "",
    ],
)
def test_a_title_the_rule_cannot_improve_is_returned_unchanged(title):
    """GUARD: byte for byte, the same object."""
    assert se.deposition_title(title) is title


def test_the_decoration_of_a_stored_title_stays():
    stored = "[ManualCheck] JANE ROE, CSR NO. 1, SAMPLE, DEPOSITION OF JOHN DOE (Pages 27-73)"
    assert se.deposition_title(stored) == "[ManualCheck] DEPOSITION OF JOHN DOE (Pages 27-73)"


def test_a_title_in_mixed_case_keeps_its_case():
    assert (
        se.deposition_title("Deposition of John Doe, vol 3") == "Deposition of John Doe, Volume III"
    )


def test_category_title_applies_the_rule_to_category_9_only():
    """DEMONSTRATES the wiring; GUARD that category 7's rule and every other category stand."""
    title = "JANE ROE, CSR NO. 1234, SAMPLE DEPOSITION SOLUTIONS, DEPOSITION OF JOHN DOE"
    assert se.category_title(title, "9") == "DEPOSITION OF JOHN DOE"
    assert se.category_title(title, 9) == "DEPOSITION OF JOHN DOE"
    assert se.category_title(title, "1") is title
    assert se.category_title(title, None) is title
    board = "WORKERS' COMPENSATION APPEALS BOARD. APPLICATION FOR ADJUDICATION OF CLAIM."
    assert se.category_title(board, "7") == "APPLICATION FOR ADJUDICATION OF CLAIM."


def test_a_stored_title_is_delivered_clean_without_a_re_run():
    """DEMONSTRATES the export half: `presentable_title` runs `category_title`, so a deposition
    summarized before the rule is delivered under it; a reviewer's own title is theirs."""
    stored = (
        "[ManualCheck] JANE ROE, CSR NO. 1234, SAMPLE LEGAL, DEPOSITION OF JOHN DOE (Pages 3-9)"
    )
    assert se.presentable_title(stored, category="9") == "DEPOSITION OF JOHN DOE"
    typed = "JANE ROE, CSR, DEPOSITION OF JOHN DOE"
    assert se.presentable_title(typed, reviewer_edited=True, category="9") == typed


# --- the volume from the cover -----------------------------------------------------------------------


def _pages(*texts):
    return "".join(f"Page {n}:\n{body}\n" for n, body in enumerate(texts, start=1))


@pytest.mark.parametrize(
    ("text", "volume"),
    [
        (_pages("SUPERIOR COURT\nVOLUME II\nPAGES 151 - 300", "APPEARANCES"), "II"),
        (_pages("CAPTION", "Deposition of the witness, Volume 1"), "I"),
        (_pages("CAPTION", "APPEARANCES", "Q. Is the volume I asked about right?"), None),
        (_pages("CAPTION", "APPEARANCES", "VOLUME III"), None),
        (_pages("CAPTION, NO VOLUME STATED", "APPEARANCES"), None),
        ("VOLUME IV of the transcript, no page marks", "IV"),
        ("", None),
        (None, None),
    ],
)
def test_the_cover_volume_is_read_from_the_first_two_pages(text, volume):
    assert se.cover_volume(text) == volume


def test_text_without_page_marks_is_read_for_about_two_pages():
    assert se.cover_volume("x" * (se._COVER_CHARS + 10) + " VOLUME II") is None


# --- where the summary is written --------------------------------------------------------------------


def _deposition_row(monkeypatch, *, title, cover, audit=_NO_ISSUES):
    """A category 9 row with every model seam stubbed; the transcript's first page is ``cover``."""
    marked = f"Page 1:\n{cover}\n" + "".join(f"Page {n}:\nQ. A.\n" for n in range(2, 9))

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        if system_msg == se.TITLE_PROMPT:
            return title, False
        return "On pages 1 to 8, asked about the injury; stated it happened at work.", False

    report = {"pages": [10, 11, 12], "errored": [], "blank": []}
    monkeypatch.setattr(se, "has_condensed_sheets", lambda *a, **k: True)
    monkeypatch.setattr(
        se,
        "extract_condensed_transcript",
        lambda path, pages: (marked, report, ocr.TranscriptNumbers(True, [])),
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: audit)
    return {
        "start": 10,
        "end": 12,
        "category": "9",
        "date": "01/31/2012",
        "injury_date": "-",
        "flag": "",
    }


def test_a_generated_deposition_title_is_the_deponent_and_the_cover_s_volume(monkeypatch):
    """DEMONSTRATES end to end: the reporter goes, and the volume the cover states is added."""
    row = _deposition_row(
        monkeypatch,
        title="JANE ROE, CSR NO. 1234, SAMPLE DEPOSITION SOLUTIONS, DEPOSITION OF JOHN DOE",
        cover="SUPERIOR COURT\nVOLUME II\nPAGES 151 - 300",
    )
    out = se.summarize_row("/x.pdf", row, prompt="P")
    assert out["summaryTitle"] == "DEPOSITION OF JOHN DOE, VOLUME II (Pages 10-12)"


def test_a_cover_without_a_volume_adds_none(monkeypatch):
    row = _deposition_row(monkeypatch, title="DEPOSITION OF JOHN DOE", cover="SUPERIOR COURT")
    out = se.summarize_row("/x.pdf", row, prompt="P")
    assert out["summaryTitle"] == "DEPOSITION OF JOHN DOE (Pages 10-12)"


def test_the_title_s_own_volume_wins_over_the_cover(monkeypatch):
    row = _deposition_row(monkeypatch, title="DEPOSITION OF JOHN DOE, VOLUME III", cover="VOLUME I")
    out = se.summarize_row("/x.pdf", row, prompt="P")
    assert out["summaryTitle"] == "DEPOSITION OF JOHN DOE, VOLUME III (Pages 10-12)"


def test_an_audited_title_keeps_the_deponent_rule_and_the_volume(monkeypatch):
    """DEMONSTRATES: the audit's corrected title goes through the same rule, so it cannot bring the
    reporter back or drop the cover's volume."""
    audit = {
        "fixed_text": "",
        "fixed_title": "JANE ROE, CSR. DEPOSITION OF JOHN A. DOE",
        "issues": [{"type": "title", "detail": "middle initial"}],
        "ok": True,
    }
    row = _deposition_row(
        monkeypatch, title="DEPOSITION OF JOHN DOE", cover="VOLUME II", audit=audit
    )
    out = se.summarize_row("/x.pdf", row, prompt="P", verify=True)
    assert out["verifiedTitle"] == "DEPOSITION OF JOHN A. DOE, VOLUME II (Pages 10-12)"


def test_a_cover_s_volume_i_is_not_added_on_its_own(monkeypatch):
    """GUARD: most "VOLUME I" covers are the only volume in their record (21 of 26 on the live box),
    where it tells the reader nothing; the record-level pass adds it where a later volume exists."""
    row = _deposition_row(monkeypatch, title="DEPOSITION OF JOHN DOE", cover="VOLUME I")
    out = se.summarize_row("/x.pdf", row, prompt="P")
    assert out["summaryTitle"] == "DEPOSITION OF JOHN DOE (Pages 10-12)"


# --- the record-level passes -------------------------------------------------------------------------


def test_the_first_volume_is_numbered_where_a_later_one_is_in_the_record():
    """DEMONSTRATES: the unnumbered transcript of a deponent whose VOLUME II the record holds is
    VOLUME I; another deponent's is left alone."""
    titles = [
        "DEPOSITION OF JOHN DOE",
        "JANE ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE",
        "DEPOSITION OF JOHN DOE, VOLUME II",
        "DEPOSITION OF MARY ROE",
    ]
    assert se.consistent_titles(titles, ["9", "1", "9", "9"]) == [
        "DEPOSITION OF JOHN DOE, VOLUME I",
        "JANE ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE",
        "DEPOSITION OF JOHN DOE, VOLUME II",
        "DEPOSITION OF MARY ROE",
    ]


def test_a_reviewer_typed_later_volume_counts_and_a_typed_title_is_never_rewritten():
    titles = ["DEPOSITION OF JOHN DOE", "DEPOSITION OF JOHN DOE VOLUME II"]
    out = se.first_volumes(titles, [False, True])
    assert out == ["DEPOSITION OF JOHN DOE, VOLUME I", "DEPOSITION OF JOHN DOE VOLUME II"]
    assert se.first_volumes(titles, [True, False]) == titles


@pytest.mark.parametrize(
    "titles",
    [
        ["DEPOSITION OF JOHN DOE"],
        ["DEPOSITION OF JOHN DOE", "DEPOSITION OF MARY ROE, VOLUME II"],
        # two unnumbered transcripts: which one is the first cannot be told
        ["DEPOSITION OF JOHN DOE", "DEPOSITION OF JOHN DOE", "DEPOSITION OF JOHN DOE, VOLUME II"],
        [
            "DEPOSITION OF JOHN DOE",
            "DEPOSITION OF JOHN DOE, VOLUME I",
            "DEPOSITION OF JOHN DOE, VOLUME II",
        ],
        ["JANE ROE, CSR. DEPOSITION.", "DEPOSITION OF JOHN DOE, VOLUME II"],
    ],
)
def test_volume_i_is_added_only_where_it_is_certain(titles):
    """GUARD."""
    assert se.first_volumes(titles) == titles


def test_two_deponents_sharing_a_surname_keep_their_own_names():
    """DEMONSTRATES: the provider passes read "DEPOSITION OF <NAME>, M.D." as an author whose first
    name is "DEPOSITION", so they wrote one deponent's name over the other's."""
    titles = [
        "DEPOSITION OF JOHN SMITH, M.D.",
        "DEPOSITION OF JOHN SMITH, M.D.",
        "DEPOSITION OF MARY SMITH, M.D.",
    ]
    assert se.consistent_titles(titles, ["9", "9", "9"]) == titles
    volumes = ["DEPOSITION OF JOHN DOE, VOLUME I", "DEPOSITION OF MARY DOE, VOLUME I"] * 2
    assert se.consistent_titles(volumes, ["9"] * 4) == volumes


def test_the_other_titles_still_go_through_the_passes():
    """GUARD: a deposition in the record changes nothing for the rest - their spellings are still
    joined, in their places."""
    titles = [
        "JANE ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE",
        "DEPOSITION OF JANE ROE, M.D.",
        "JANE ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE",
        "JANE A. ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE",
    ]
    out = se.consistent_titles(titles, ["1", "9", "1", "1"], [False, False, False, True])
    assert out[1] == "DEPOSITION OF JANE ROE, M.D."
    rest = se.consistent_titles([titles[0], titles[2], titles[3]], ["1"] * 3, [False, False, True])
    kept = [out[0], out[2], out[3]]
    assert kept == rest
    assert rest[0] == "JANE A. ROE, M.D. SAMPLE ORTHOPEDICS. PROGRESS NOTE", "the passes ran"
