"""Unit tests for the isolated DOI extraction service (app.services.summary_doi).

The vision call is mocked; these tests pin the reply-parsing, page cap, fail-safe, and the
summary DOI-prefix rewrite used by the backfill.
"""

import types as pytypes

import pytest

from app.services import summary_doi as sd


def test_clean_normalises_to_two_digit_years():
    # House format, measured on 813 human entries: every dated DOI is MM/DD/YY.
    assert sd._clean("09/25/2023") == "09/25/23"
    assert sd._clean("The date of injury is 5/7/2018.") == "05/07/18"
    assert sd._clean("05/07/18") == "05/07/18"


def test_clean_joins_several_dates_with_an_ampersand():
    # Humans write "DOI: MM/DD/YY & MM/DD/YY"; no comma-joined instance exists in the corpus.
    assert sd._clean("05/07/2018, 06/01/2019") == "05/07/18 & 06/01/19"


def test_clean_keeps_a_cumulative_trauma_range_as_one_item():
    # 90 corpus instances of "CT MM/DD/YY-MM/DD/YY". The range must NOT split into two dates.
    assert sd._clean("CT 01/02/2020-03/04/2021") == "CT 01/02/20-03/04/21"
    assert sd._clean("ct 01/02/20 to 03/04/21") == "CT 01/02/20-03/04/21"


def test_clean_keeps_a_bare_range_without_inventing_the_ct_marker():
    assert sd._clean("01/02/20-03/04/21") == "01/02/20-03/04/21"


def test_clean_mixes_a_range_and_a_single_date():
    assert sd._clean("CT 01/02/20-03/04/21 & 06/01/22") == "CT 01/02/20-03/04/21 & 06/01/22"


def test_clean_handles_absence():
    assert sd._clean("-") == "-"
    assert sd._clean("none stated") == "-"
    assert sd._clean("") == "-"


def test_clean_dedups_preserving_order():
    assert sd._clean("09/25/2023 (see 09/25/2023)") == "09/25/23"


class _FakeReader:
    def __init__(self, path):
        self.pages = [object()] * 60


class _FakeWriter:
    def add_page(self, page):
        pass

    def write(self, buffer):
        buffer.write(b"%PDF-1.4 fake")


def _patch_pdf(monkeypatch, writer=_FakeWriter):
    monkeypatch.setattr(sd, "PdfReader", _FakeReader)
    monkeypatch.setattr(sd, "PdfWriter", writer)
    monkeypatch.setattr(sd, "get_genai_client", lambda: None)


def test_extract_returns_the_model_date(monkeypatch):
    _patch_pdf(monkeypatch)
    monkeypatch.setattr(
        sd, "generate_with_retry", lambda *a, **k: pytypes.SimpleNamespace(text="09/25/2023")
    )
    assert sd.extract_injury_date("/x.pdf", 1, 3) == "09/25/23"


def test_extract_returns_a_cumulative_trauma_period(monkeypatch):
    _patch_pdf(monkeypatch)
    monkeypatch.setattr(
        sd,
        "generate_with_retry",
        lambda *a, **k: pytypes.SimpleNamespace(text="CT 01/02/20-03/04/21"),
    )
    assert sd.extract_injury_date("/x.pdf", 1, 3) == "CT 01/02/20-03/04/21"


def test_extract_sets_its_own_thinking_budget(monkeypatch):
    # REGRESSION: summarize_row passes summary_model (2.5-pro) here, and the retry seam applies
    # thinking_budget=0 to any call that does not set one - which that model rejects with a 400.
    # This function is fail-safe, so the rejection was silent and every document looked like it
    # stated no injury date.
    seen = {}
    _patch_pdf(monkeypatch)

    def gen(client, *, model, contents, config):
        seen["thinking"] = config.thinking_config
        return pytypes.SimpleNamespace(text="09/25/23")

    monkeypatch.setattr(sd, "generate_with_retry", gen)
    sd.extract_injury_date("/x.pdf", 1, 3)
    assert seen["thinking"] is not None
    assert seen["thinking"].thinking_budget != 0


def test_extract_returns_dash_when_no_date(monkeypatch):
    _patch_pdf(monkeypatch)
    monkeypatch.setattr(
        sd, "generate_with_retry", lambda *a, **k: pytypes.SimpleNamespace(text="-")
    )
    assert sd.extract_injury_date("/x.pdf", 1, 3) == "-"


def test_extract_is_failsafe_on_error(monkeypatch):
    _patch_pdf(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("vertex down")

    monkeypatch.setattr(sd, "generate_with_retry", boom)
    assert sd.extract_injury_date("/x.pdf", 1, 3) == "-"


def test_extract_caps_pages_sent(monkeypatch):
    added: list = []

    class CountingWriter(_FakeWriter):
        def add_page(self, page):
            added.append(page)

    _patch_pdf(monkeypatch, writer=CountingWriter)
    monkeypatch.setattr(
        sd, "generate_with_retry", lambda *a, **k: pytypes.SimpleNamespace(text="-")
    )
    sd.extract_injury_date("/x.pdf", 1, 50)  # 50-page span
    assert len(added) == sd._MAX_PAGES


@pytest.mark.parametrize("body", [None, ""])
def test_apply_prefix_noop_on_empty(body):
    assert sd.apply_doi_prefix(body, "09/25/2023") == body


def test_apply_prefix_adds_the_house_grammar_when_absent():
    assert sd.apply_doi_prefix("Body text.", "09/25/23") == "**DOI**: 09/25/23. Body text."


def test_apply_prefix_emits_a_cumulative_trauma_period():
    assert (
        sd.apply_doi_prefix("Body.", "CT 01/02/20-03/04/21")
        == "**DOI**: CT 01/02/20-03/04/21. Body."
    )


def test_apply_prefix_removes_either_grammar_when_dash():
    assert sd.apply_doi_prefix("**DOI**: 09/25/23. Body text.", "-") == "Body text."
    # Legacy stored bodies (comma terminator, 4-digit year) must still be strippable.
    assert sd.apply_doi_prefix("**DOI**:09/25/2023, Body text.", "-") == "Body text."


def test_apply_prefix_upgrades_a_legacy_prefix_to_the_house_grammar():
    assert (
        sd.apply_doi_prefix("**DOI**:01/01/2000, Body.", "09/25/23") == "**DOI**: 09/25/23. Body."
    )


def test_apply_prefix_replaces_a_new_grammar_prefix():
    assert sd.apply_doi_prefix("**DOI**: 01/01/00. Body.", "09/25/23") == "**DOI**: 09/25/23. Body."


def test_apply_prefix_strips_multi_date_prefixes_in_both_grammars():
    assert sd.apply_doi_prefix("**DOI**:06/04/2024, 01/03/2025, Body.", "-") == "Body."
    assert sd.apply_doi_prefix("**DOI**: 06/04/24 & 01/03/25. Body.", "-") == "Body."


def test_apply_prefix_leaves_non_prefixed_body_when_dash():
    assert sd.apply_doi_prefix("No prefix here.", "-") == "No prefix here."


def test_doi_prefix_reads_the_house_grammar():
    assert sd.doi_prefix("**DOI**: 05/08/22. Body.") == "**DOI**: 05/08/22."
    assert sd.doi_prefix("**DOI**: 05/08/22 & 06/01/23. Body.") == "**DOI**: 05/08/22 & 06/01/23."
    assert sd.doi_prefix("**DOI**: CT 01/02/20-03/04/21. Body.") == "**DOI**: CT 01/02/20-03/04/21."


def test_doi_prefix_still_reads_legacy_stored_prefixes_verbatim():
    # 709 summaries predate the grammar change; export re-applies exactly what was stored, so the
    # legacy comma form must round-trip unchanged rather than being silently rewritten.
    assert sd.doi_prefix("**DOI**:05/08/2022, Body.") == "**DOI**:05/08/2022,"
    assert (
        sd.doi_prefix("**DOI**:05/08/2022, 06/01/2023, Body.") == "**DOI**:05/08/2022, 06/01/2023,"
    )


@pytest.mark.parametrize(
    "body",
    [
        "Body with no prefix.",
        "Body mentioning **DOI**:05/08/2022, in the middle.",
        "",
        None,
    ],
)
def test_doi_prefix_is_empty_without_a_leading_prefix(body):
    assert sd.doi_prefix(body) == ""


# The `CT:` / `C.T.` marker loss (plan 2026-07-31 task 4.2). `(?P<ct>CT\s*)?` admitted no colon and no
# dots, so a cumulative-trauma PERIOD silently degraded to a bare date range - it stopped saying that
# the injury accrued over time. CAMPUS_NIKKI page 236 literally reads "Date of injury: CT: 11/30/2015 -
# 12/04/2025", so the variant is real rather than hypothetical.
@pytest.mark.parametrize(
    "reply",
    [
        "CT 11/30/2015 - 12/04/2025",
        "CT: 11/30/2015 - 12/04/2025",
        "C.T. 11/30/2015-12/04/2025",
        "CT:11/30/2015-12/04/2025",
        "Date of injury: CT: 11/30/2015 - 12/04/2025",
    ],
)
def test_clean_keeps_the_ct_marker_however_the_source_punctuates_it(reply):
    """WHEN a reply states a cumulative-trauma period as CT, CT: or C.T., THE SYSTEM SHALL store one
    item prefixed "CT "."""
    assert sd._clean(reply) == "CT 11/30/15-12/04/25"


def test_clean_does_not_read_a_ct_marker_out_of_a_surrounding_word():
    """The marker must be anchored. Before the fix the letters inside "IMPACT" matched, so a bare range
    came back classified as cumulative trauma - inventing exactly the classification this module exists
    to avoid inventing."""
    assert sd._clean("IMPACT 11/30/2015 - 12/04/2025") == "11/30/15-12/04/25"
    # A lone letter is not a marker either.
    assert sd._clean("C 11/30/2015 - 12/04/2025") == "11/30/15-12/04/25"
    assert sd._clean("T 11/30/2015 - 12/04/2025") == "11/30/15-12/04/25"


def test_the_isolated_call_now_sends_up_to_ten_pages():
    """WHEN a sub-document is 6 to 10 pages, THE SYSTEM SHALL still send the page that states the DOI.

    Measured 2026-07-31: capture on rows whose DOI label is followed by a digit was 83.5% (n=79) at
    1-5 pages and 59.5% (n=37) at 6+, because past page 5 the field was never in the payload.
    """
    assert sd._MAX_PAGES == 10


def test_a_thirty_page_row_is_still_bounded(monkeypatch):
    """The cap has to keep BOUNDING the payload - raising it is not the same as removing it."""
    added: list = []

    class CountingWriter(_FakeWriter):
        def add_page(self, page):
            added.append(page)

    _patch_pdf(monkeypatch, writer=CountingWriter)
    monkeypatch.setattr(
        sd, "generate_with_retry", lambda *a, **k: pytypes.SimpleNamespace(text="-")
    )
    sd.extract_injury_date("/x.pdf", 1, 30)
    assert len(added) == 10


# "Date of Onset" as a DOI synonym (Adrian's domain call, 2026-08-03). Claim forms label the same field
# either way and the DLSR 5021 combines them outright ("Date and hour of injury or onset of illness").
# Distinct from the synonym expansion the 2026-07-31 plan rejected: that measured `D/I` and
# `Date and Hour of Injury`, and "onset" appeared in neither the prompt nor that measurement.
def test_the_isolated_prompt_recognises_a_date_of_onset_field():
    """WHEN a document labels the field "Date of Onset", THE SYSTEM SHALL treat it as the injury date."""
    assert "Date of Onset" in sd._ISOLATION_PROMPT
    assert "onset of illness" in sd._ISOLATION_PROMPT  # the combined DLSR 5021 wording
    # The original labels must survive - this is an addition, not a replacement.
    assert "Date of Injury" in sd._ISOLATION_PROMPT
    assert "DOI" in sd._ISOLATION_PROMPT


def test_the_isolated_prompt_still_requires_a_labelled_field():
    """The guard that makes the synonym safe: "onset" also occurs in narrative prose about when symptoms
    began, which is not a stated injury date. Without this the widened label would invite a date the
    document never asserted as the DOI - the propagation problem this module exists to remove."""
    assert "never from narrative prose" in sd._ISOLATION_PROMPT
    # And the existing exclusions are untouched.
    assert (
        "never use a date of exam, visit, service, report, birth, or signature"
        in sd._ISOLATION_PROMPT
    )


def test_the_segmentation_prompt_also_recognises_onset():
    """Segmentation fills the row's injury_date, so a document labelled only "Date of Onset" would
    otherwise reach the summary with nothing for the isolated pass to confirm."""
    from app.services.gemini import SEGMENTATION_PROMPT

    assert "Date of Onset" in SEGMENTATION_PROMPT
    assert "not from prose describing when symptoms began" in SEGMENTATION_PROMPT


def test_the_segmentation_prompt_version_was_bumped_with_the_prompt():
    """WHEN the segmentation prompt changes, THE SYSTEM SHALL bump PROMPT_VERSION.

    Its own contract: the stamp is stored on every Job row so SegmentRows stay traceable to the prompt
    that produced them, which the fine-tuning dataset depends on. Leaving it at "2" after editing the
    prompt would silently attribute new rows to the old text - the kind of error that is invisible until
    someone trains on the dataset.
    """
    from app.services.gemini import PROMPT_VERSION

    assert PROMPT_VERSION == "3"
