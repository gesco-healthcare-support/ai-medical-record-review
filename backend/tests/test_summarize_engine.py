"""Unit tests for summarize_row: the factuality-hardening preamble, the configured temperature,
and the faithfulness verify pass wiring.

Pure (no DB, no Vertex): _generate, the OCR call, and verify_summary are monkeypatched, so the
tests assert what summarize_row FEEDS the model and how it threads the verify result.
"""

import pytest

from app.config import get_settings
from app.errors import EmptyExtractionError
from app.services import summarize_engine as se

_NO_ISSUES = {"fixed_text": "", "issues": []}


@pytest.fixture(autouse=True)
def _stub_doi(monkeypatch):
    # Isolated DOI extraction hits a real PDF/Vertex; existing tests don't exercise it, so default
    # it to "-" (no DOI). DOI-specific tests below re-patch it.
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")


def _row(**over):
    row = {
        "start": 1,
        "end": 2,
        "category": "1",
        "date": "2026-01-01",
        "injury_date": "-",
        "flag": "",
    }
    row.update(over)
    return row


def _fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
    """_generate's contract: (text, truncated). Nothing here hits the token cap."""
    text = "Progress Note - Dr Smith" if system_msg == se.TITLE_PROMPT else "SUMMARY BODY"
    return text, False


def test_summary_call_uses_hardening_preamble_and_configured_temperature(monkeypatch):
    calls = []

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        calls.append(
            {
                "system_msg": system_msg,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        )
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")

    summary_call = next(c for c in calls if c["system_msg"] != se.TITLE_PROMPT)
    title_call = next(c for c in calls if c["system_msg"] == se.TITLE_PROMPT)
    assert summary_call["system_msg"] == se.HARDENING_PREAMBLE + "CATEGORY PROMPT"
    assert summary_call["temperature"] == get_settings().summary_temperature
    assert title_call["temperature"] == 0.0  # title is pure extraction, always deterministic
    assert "SUMMARY BODY" in out["summaryText"]
    assert out["sourceText"] == "raw OCR text"


def test_empty_ocr_fails_fast(monkeypatch):
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "   "
    )
    monkeypatch.setattr(se, "_generate", lambda *a, **k: ("unused", False))
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    with pytest.raises(EmptyExtractionError):
        se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")


def test_verify_populates_verified_fields_when_issues_found(monkeypatch):
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False)
            if system_msg == se.TITLE_PROMPT
            else ("SUMMARY BODY with a fabrication", False)
        ),
    )
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None: {
            "fixed_text": "SUMMARY BODY",
            "fixed_title": title,
            "issues": [{"type": "unsupported", "detail": "a fabrication"}],
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verified"] is True
    assert out["verifyIssues"] == [{"type": "unsupported", "detail": "a fabrication"}]
    assert "SUMMARY BODY" in out["verifiedText"]
    assert "fabrication" not in out["verifiedText"]
    # The raw model output stays the un-fixed body (immutable training data).
    assert "fabrication" in out["summaryText"]
    # The title came back unchanged, so nothing is stored to override it.
    assert out["verifiedTitle"] is None


def test_verified_title_is_stored_decorated_when_the_pass_corrects_it(monkeypatch):
    # WHEN the verify pass corrects the title, THE SYSTEM SHALL store it with the same decorations
    # as the raw title, so it is a drop-in replacement in every view.
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None: {
            "fixed_text": "SUMMARY BODY",
            "fixed_title": "CORRECTED HEADER",
            "issues": [{"type": "laterality", "detail": "left/right"}],
        },
    )

    out = se.summarize_row("/x.pdf", _row(category="3", flag="x"), prompt="P", verify=True)

    assert out["verifiedTitle"] == "[ManualCheck] CORRECTED HEADER [Diagnostic Study] (Pages 1-2)"
    # The raw title keeps the model's own output.
    assert "Progress Note" in out["summaryTitle"]


def test_verified_title_stays_none_when_the_pass_finds_nothing(monkeypatch):
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None: {
            "fixed_text": "",
            "fixed_title": "SOMETHING ELSE",
            "issues": [],
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)
    assert out["verifiedTitle"] is None
    assert out["verifiedText"] is None


def test_the_title_is_handed_to_the_verify_pass(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)

    def fake_verify(model, source, summary, title=None):
        seen["title"] = title
        return {"fixed_text": summary, "fixed_title": title, "issues": []}

    monkeypatch.setattr(se, "verify_summary", fake_verify)
    se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)
    # The BARE model title, without the page suffix or tags - those are added after verification.
    assert seen["title"] == "Progress Note - Dr Smith"


def test_the_doi_call_runs_on_the_flash_model_not_the_summary_model(monkeypatch):
    # summary_model (2.5-pro) quota is the binding constraint - a measured evening saw 181 rejected
    # calls, enough to pause a summarize job. Reading a date off a page is extraction, so it must run
    # on genai_model. Passing summarize_row's own model here is the regression.
    seen = {}
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    def fake_extract(pdf_path, start, end, model=None, strict=False):
        seen["model"] = model
        return "-"

    monkeypatch.setattr(se, "extract_injury_date", fake_extract)
    se.summarize_row("/x.pdf", _row(), prompt="P", model="gemini-2.5-pro", extract_doi=True)
    # None means "use the function's own default", which is genai_model (flash).
    assert seen["model"] is None


def test_stored_ocr_is_reused_instead_of_extracting_twice(monkeypatch):
    # WHEN the row already carries the duplicate check's OCR of these pages, THE SYSTEM SHALL reuse it
    # and NOT run OCR again - on a 1500-page record that second pass is ~45 wasted minutes.
    calls = []
    monkeypatch.setattr(
        se,
        "extract_text_from_selected_pages",
        lambda path, pages, mark_pages=False: calls.append(pages) or "FRESH OCR",
    )
    seen = {}

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        if system_msg != se.TITLE_PROMPT:
            seen["body"] = user_text
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    se.summarize_row("/x.pdf", _row(source_text="STORED OCR TEXT"), prompt="P")
    assert calls == []  # no OCR
    assert seen["body"] == "STORED OCR TEXT"


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_blank_stored_ocr_falls_back_to_extracting(monkeypatch, stored):
    # A row whose OCR failed carries "" (dedup's sentinel). That must NOT be reused as "no text" -
    # it gets another attempt, otherwise one bad page condemns the row forever.
    calls = []
    monkeypatch.setattr(
        se,
        "extract_text_from_selected_pages",
        lambda path, pages, mark_pages=False: calls.append(pages) or "FRESH OCR",
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    out = se.summarize_row("/x.pdf", _row(source_text=stored), prompt="P")
    assert calls == [[1, 2]]
    assert out["sourceText"] == "FRESH OCR"


def test_a_deposition_re_extracts_with_page_markers(monkeypatch):
    # E-01: a deposition is summarised one line per transcript page, so its input needs page
    # boundaries. They cannot be retrofitted onto text that was concatenated without them, so a
    # category-9 row IGNORES the stored dedup OCR and re-extracts with markers.
    calls = []

    def fake_extract(path, pages, mark_pages=False):
        calls.append({"pages": pages, "mark_pages": mark_pages})
        return "Page 1:\nbody\n"

    monkeypatch.setattr(se, "extract_text_from_selected_pages", fake_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    se.summarize_row("/x.pdf", _row(category="9", source_text="STORED UNMARKED OCR"), prompt="P")

    assert len(calls) == 1  # the stored text was deliberately not reused
    assert calls[0]["mark_pages"] is True


@pytest.mark.parametrize("category", ["1", "3", "5", "13", "100"])
def test_other_categories_reuse_stored_ocr_and_never_get_markers(monkeypatch, category):
    # Markers are deposition-only: elsewhere they would put page numbers into ordinary summaries and
    # throw away the OCR saving from PR #56.
    calls = []

    def fake_extract(path, pages, mark_pages=False):
        calls.append(mark_pages)
        return "FRESH"

    monkeypatch.setattr(se, "extract_text_from_selected_pages", fake_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(category=category, source_text="STORED OCR"), prompt="P")
    assert calls == []  # reused, no OCR at all
    assert out["sourceText"] == "STORED OCR"


def test_a_deposition_without_stored_text_still_gets_markers(monkeypatch):
    calls = []

    def fake_extract(path, pages, mark_pages=False):
        calls.append(mark_pages)
        return "Page 1:\nbody\n"

    monkeypatch.setattr(se, "extract_text_from_selected_pages", fake_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    se.summarize_row("/x.pdf", _row(category="9"), prompt="P")
    assert calls == [True]


def test_doi_prefix_from_isolated_extraction(monkeypatch):
    # WHEN extract_doi and the isolated extraction returns a date, THE SYSTEM SHALL prefix it.
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "09/25/23")
    out = se.summarize_row("/x.pdf", _row(injury_date="-"), prompt="P", extract_doi=True)
    assert out["summaryText"].startswith("**DOI**: 09/25/23.")


def test_doi_prefix_carries_a_cumulative_trauma_period(monkeypatch):
    # WHEN the document states a cumulative-trauma period, THE SYSTEM SHALL carry the whole period
    # as one value rather than splitting it into two injury dates.
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "CT 01/02/20-03/04/21")
    out = se.summarize_row("/x.pdf", _row(injury_date="-"), prompt="P", extract_doi=True)
    assert out["summaryText"].startswith("**DOI**: CT 01/02/20-03/04/21.")


def test_doi_prefix_omitted_when_isolated_returns_dash(monkeypatch):
    # WHEN the isolated extraction returns "-", THE SYSTEM SHALL omit the prefix even though the
    # (propagated) row value carries a date.
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: "-")
    out = se.summarize_row("/x.pdf", _row(injury_date="05/08/2022"), prompt="P", extract_doi=True)
    assert "**DOI**" not in out["summaryText"]


def test_extract_doi_false_uses_row_value_without_calling(monkeypatch):
    # WHEN extract_doi is False, THE SYSTEM SHALL use row["injury_date"] and NOT call extraction.
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    called = []
    monkeypatch.setattr(se, "extract_injury_date", lambda *a, **k: called.append(1) or "99/99/9999")
    out = se.summarize_row("/x.pdf", _row(injury_date="05/07/2018"), prompt="P", extract_doi=False)
    assert out["summaryText"].startswith("**DOI**: 05/07/2018.")
    assert called == []


def test_verify_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False) if system_msg == se.TITLE_PROMPT else ("BODY", False)
        ),
    )
    called = []

    def _spy(*a, **k):
        called.append(1)
        return _NO_ISSUES

    monkeypatch.setattr(se, "verify_summary", _spy)

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=False)

    assert called == []  # verify must not run when explicitly disabled (bundle export path)
    assert out["verified"] is False
    assert out["verifiedText"] is None
    assert out["verifyIssues"] is None


def test_configured_token_budget_reaches_the_model_and_max_tokens_is_reported(monkeypatch):
    # WHERE SUMMARY_MAX_OUTPUT_TOKENS is set, THE SYSTEM SHALL pass that budget to the model; WHEN
    # the model reports a MAX_TOKENS finish, _generate SHALL report the reply as truncated.
    from types import SimpleNamespace

    from google.genai import types

    captured = {}

    def fake_retry(client, *, model, contents, config):
        captured["config"] = config
        return SimpleNamespace(
            text="HALF A SUMMARY",
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
        )

    monkeypatch.setattr(se, "get_genai_client", lambda: object())
    monkeypatch.setattr(se, "generate_with_retry", fake_retry)

    text, truncated = se._generate("m", "sys", "user text", 0.0, max_output_tokens=4321)

    assert captured["config"].max_output_tokens == 4321
    assert text == "HALF A SUMMARY"
    assert truncated is True


def test_normal_finish_is_not_reported_as_truncated(monkeypatch):
    # WHEN the model finishes normally, THE SYSTEM SHALL NOT report truncation.
    from types import SimpleNamespace

    from google.genai import types

    monkeypatch.setattr(se, "get_genai_client", lambda: object())
    monkeypatch.setattr(
        se,
        "generate_with_retry",
        lambda client, **kw: SimpleNamespace(
            text=" BODY ", candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)]
        ),
    )
    assert se._generate("m", "sys", "user text", 0.0) == ("BODY", False)


def test_truncated_summary_is_flagged_for_manual_check(monkeypatch):
    # WHEN the body hit the token cap, THE SYSTEM SHALL flag the summary and SHALL NOT alter its
    # text (a cut-off summary must be visible to the reviewer, not stored as if it were finished).
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False)
            if system_msg == se.TITLE_PROMPT
            else ("SUMMARY BODY cut off mid-sen", True)
        ),
    )
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert out["truncated"] is True
    assert out["manualCheck"] == ""  # the row's own review flag is untouched
    assert out["summaryText"].endswith("SUMMARY BODY cut off mid-sen")
    assert "Truncated" not in out["summaryTitle"]


def _system_messages(monkeypatch, row, **kw):
    """Run summarize_row against stubs and return every system message it sent."""
    seen = []

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        seen.append(system_msg)
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "OCR text"
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    se.summarize_row("/x.pdf", row, prompt="CATEGORY PROMPT", **kw)
    return [msg for msg in seen if msg != se.TITLE_PROMPT]


_STUDIES = [
    {"title": "MRI OF THE LUMBAR SPINE", "date": "03/12/24"},
    {"title": "X-RAY OF THE LEFT WRIST", "date": "-"},
]


@pytest.mark.parametrize("category", ["12", "13"])
def test_standalone_studies_reach_the_system_message_for_a_review_category(monkeypatch, category):
    # E-08: WHEN a category-12 or 13 row is summarized in a record that has standalone diagnostic
    # studies, THE SYSTEM SHALL name those studies in the system message, so the embedded records
    # review does not restate a study summarized in its own right elsewhere.
    (system_msg,) = _system_messages(
        monkeypatch, _row(category=category), standalone_studies=_STUDIES
    )

    assert system_msg.startswith(se.HARDENING_PREAMBLE + "CATEGORY PROMPT")  # rules come first
    assert "MRI OF THE LUMBAR SPINE (03/12/24)" in system_msg
    assert "X-RAY OF THE LEFT WRIST" in system_msg
    assert "X-RAY OF THE LEFT WRIST (-)" not in system_msg  # a missing date is omitted, not printed


@pytest.mark.parametrize("category", ["1", "3", "5", "9", "100"])
def test_other_categories_never_receive_the_study_list(monkeypatch, category):
    # Only categories 12 and 13 have a rule that reads the list. Sending it to category 1 - the
    # highest-volume one - would spend tokens on context nothing acts on.
    (system_msg,) = _system_messages(
        monkeypatch, _row(category=category), standalone_studies=_STUDIES
    )
    assert system_msg == se.HARDENING_PREAMBLE + "CATEGORY PROMPT"


@pytest.mark.parametrize("studies", [None, [], [{"title": "-", "date": "-"}], [{"title": ""}]])
def test_nothing_listable_leaves_the_system_message_unchanged(monkeypatch, studies):
    # A record with no standalone studies, or rows whose titles never got extracted, must send the
    # prompt exactly as before - not an empty heading the model has to interpret.
    (system_msg,) = _system_messages(monkeypatch, _row(category="12"), standalone_studies=studies)
    assert system_msg == se.HARDENING_PREAMBLE + "CATEGORY PROMPT"


def test_standalone_studies_from_rows_takes_only_diagnostic_studies():
    rows = [
        {"start": 1, "end": 2, "category": "1", "title": "PROGRESS NOTE", "date": "01/01/24"},
        {"start": 3, "end": 4, "category": "3", "title": "MRI OF THE KNEE", "date": "02/02/24"},
        {"start": 5, "end": 9, "category": "13", "title": "QME REPORT", "date": "03/03/24"},
        {"start": 10, "end": 10, "category": "14", "title": "LABORATORY RESULTS", "date": "-"},
    ]
    assert se.standalone_studies_from_rows(rows) == [
        {"title": "MRI OF THE KNEE", "date": "02/02/24"}
    ]


def test_a_study_is_never_listed_against_itself():
    # WHEN the row being summarized is itself a category-3 study, THE SYSTEM SHALL leave it out of
    # its own context list. Matched on the page range, which identifies a row within a record.
    rows = [
        {"start": 1, "end": 2, "category": "3", "title": "MRI OF THE KNEE", "date": "-"},
        {"start": 3, "end": 4, "category": "3", "title": "CT OF THE HEAD", "date": "-"},
    ]
    studies = se.standalone_studies_from_rows(rows, exclude=rows[0])
    assert [study["title"] for study in studies] == ["CT OF THE HEAD"]


def test_neither_review_category_still_tells_the_model_to_ignore_the_review():
    # The shared preamble rule (take the review's diagnostic studies) and these two category prompts
    # contradicted each other until 2026-07-30: the prompts said to treat the review as absent. A
    # prompt edit that reinstates that wording silently reverts E-08's whole point.
    from app.services.prompts import prompts

    for key in ("category_12", "category_13"):
        text = prompts[key].lower()
        assert "as if it does not exist" not in text
        assert "do not take info from the mrr" not in text
        assert "only the diagnostic studies it reports" in text


def test_untruncated_summary_reports_no_truncation(monkeypatch):
    monkeypatch.setattr(
        se, "extract_text_from_selected_pages", lambda path, pages, mark_pages=False: "raw OCR text"
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    assert se.summarize_row("/x.pdf", _row(), prompt="P")["truncated"] is False
