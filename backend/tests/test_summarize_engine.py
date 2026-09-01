"""Unit tests for summarize_row: the factuality-hardening preamble, the configured temperature,
and the faithfulness verify pass wiring.

Pure (no DB, no Vertex): _generate, the OCR call, and verify_summary are monkeypatched, so the
tests assert what summarize_row FEEDS the model and how it threads the verify result.
"""

import pytest

from app.config import get_settings
from app.errors import EmptyExtractionError, is_daily_quota, is_rate_limited
from app.services import summarize_engine as se
from app.services.llm import gemini as gm

_NO_ISSUES = {"fixed_text": "", "issues": [], "ok": True}  # the audit RAN and found nothing


def _clean(pages, errored=(), blank=()):
    """A report from ``ocr.extract_pages_with_report``: ``{"pages", "errored", "blank"}``.

    summarize_row reads ``errored`` only, but the stubs return the whole shape so one cannot keep
    passing after the real contract changes underneath it. Defaults to "every page read fine", which
    is what every test but the unreadable-page ones below wants.
    """
    listed = sorted({int(p) for p in pages})
    return {
        "pages": len(listed),
        "errored": [p for p in listed if p in set(errored)],
        "blank": [p for p in listed if p in set(blank)],
    }


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
    text = "Progress Note - Dr Smith" if system_msg == se.TITLE_PROMPT else "Summary body"
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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="CATEGORY PROMPT")

    summary_call = next(c for c in calls if c["system_msg"] != se.TITLE_PROMPT)
    title_call = next(c for c in calls if c["system_msg"] == se.TITLE_PROMPT)
    # The rules come first; a category-1 row then gets its document-date block appended (see the
    # current-visit tests below), so this is a prefix assertion rather than an equality one.
    assert summary_call["system_msg"].startswith(se.build_preamble("1") + "CATEGORY PROMPT")
    assert summary_call["temperature"] == get_settings().summary_temperature
    assert title_call["temperature"] == 0.0  # title is pure extraction, always deterministic
    assert "Summary body" in out["summaryText"]
    assert out["sourceText"] == "raw OCR text"


def test_empty_ocr_fails_fast(monkeypatch):
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("   ", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", lambda *a, **k: ("unused", False))
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    row = _row()
    with pytest.raises(EmptyExtractionError):
        se.summarize_row("/x.pdf", row, prompt="CATEGORY PROMPT")


def test_verify_populates_verified_fields_when_issues_found(monkeypatch):
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False)
            if system_msg == se.TITLE_PROMPT
            else ("Summary body with a fabrication", False)
        ),
    )
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Summary body",
            "fixed_title": title,
            "issues": [{"type": "unsupported", "detail": "a fabrication"}],
            "ok": True,
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verified"] is True
    assert out["verifyIssues"] == [{"type": "unsupported", "detail": "a fabrication"}]
    assert "Summary body" in out["verifiedText"]
    assert "fabrication" not in out["verifiedText"]
    # The raw model output stays the un-fixed body (immutable training data).
    assert "fabrication" in out["summaryText"]
    # The title came back unchanged, so nothing is stored to override it.
    assert out["verifiedTitle"] is None


def test_a_failed_audit_is_not_recorded_as_verified(monkeypatch):
    """WHEN the audit does not complete, THE SYSTEM SHALL NOT mark the summary verified.

    `verified` used to be set from the SETTING that requested the pass, so a summary whose audit
    threw or came back truncated was still stored claiming a faithfulness check had run. On a
    medical summary that is a false record, and it is unrecoverable after the fact: no query can
    separate "audited, nothing to fix" from "audit crashed, nobody looked".
    """
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False) if system_msg == se.TITLE_PROMPT else ("Summary body", False)
        ),
    )
    # The fail-safe shape verify_summary returns when the reply truncated or the parse threw: the
    # originals, no issues, and ok False.
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": summary,
            "fixed_title": title,
            "issues": [],
            "ok": False,
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verified"] is False
    assert out["verifiedText"] is None  # nothing was verified, so nothing overrides the raw body
    assert "Summary body" in out["summaryText"]  # the summary itself still ships


def test_verified_title_is_stored_decorated_when_the_pass_corrects_it(monkeypatch):
    # WHEN the verify pass corrects the title, THE SYSTEM SHALL store it with the same decorations
    # as the raw title, so it is a drop-in replacement in every view.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Summary body",
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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)

    def fake_verify(model, source, summary, title=None, document_date=None):
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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    # WHEN a row is summarized, THE SYSTEM SHALL make NO injury-date call. The read moved to the end
    # of segmentation, where it happens once per sub-document and its result is stored on the row.
    # This test used to assert the call went to flash rather than 2.5-pro; the call is gone entirely,
    # which settles that concern more thoroughly than routing it did.
    seen["model"] = "sentinel-never-overwritten"
    se.summarize_row("/x.pdf", _row(), prompt="P", model="gemini-2.5-pro")
    assert seen["model"] == "sentinel-never-overwritten"
    assert not hasattr(se, "extract_injury_date")


def test_stored_ocr_is_reused_instead_of_extracting_twice(monkeypatch):
    # WHEN the row already carries the duplicate check's OCR of these pages, THE SYSTEM SHALL reuse it
    # and NOT run OCR again - on a 1500-page record that second pass is ~45 wasted minutes.
    calls = []
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: (
            calls.append(pages) or "FRESH OCR",
            _clean(pages),
        ),
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
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: (
            calls.append(pages) or "FRESH OCR",
            _clean(pages),
        ),
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

    def fake_extract(path, pages, mark_pages=False, **_kw):
        calls.append({"pages": pages, "mark_pages": mark_pages})
        return "Page 1:\nbody\n", _clean(pages)

    monkeypatch.setattr(se, "extract_pages_with_report", fake_extract)
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

    def fake_extract(path, pages, mark_pages=False, **_kw):
        calls.append(mark_pages)
        return "FRESH", _clean(pages)

    monkeypatch.setattr(se, "extract_pages_with_report", fake_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(category=category, source_text="STORED OCR"), prompt="P")
    assert calls == []  # reused, no OCR at all
    assert out["sourceText"] == "STORED OCR"


def test_a_deposition_without_stored_text_still_gets_markers(monkeypatch):
    calls = []

    def fake_extract(path, pages, mark_pages=False, **_kw):
        calls.append(mark_pages)
        return "Page 1:\nbody\n", _clean(pages)

    monkeypatch.setattr(se, "extract_pages_with_report", fake_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    se.summarize_row("/x.pdf", _row(category="9"), prompt="P")
    assert calls == [True]


def test_doi_prefix_comes_from_the_row(monkeypatch):
    # WHEN the row carries an injury date, THE SYSTEM SHALL prefix the summary with it. The row is the
    # single source of truth: segmentation read it once, per sub-document, in isolation.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    out = se.summarize_row("/x.pdf", _row(injury_date="09/25/23"), prompt="P")
    assert out["summaryText"].startswith("**DOI**: 09/25/23.")


def test_doi_prefix_carries_a_cumulative_trauma_period(monkeypatch):
    # WHEN the document states a cumulative-trauma period, THE SYSTEM SHALL carry the whole period
    # as one value rather than splitting it into two injury dates.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    out = se.summarize_row("/x.pdf", _row(injury_date="CT 01/02/20-03/04/21"), prompt="P")
    assert out["summaryText"].startswith("**DOI**: CT 01/02/20-03/04/21.")


def test_doi_prefix_omitted_when_the_row_states_none(monkeypatch):
    # WHEN the row's injury date is the "-" sentinel, THE SYSTEM SHALL omit the prefix. "-" means this
    # document states no injury date, which is a fact worth honouring rather than a missing value.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    out = se.summarize_row("/x.pdf", _row(injury_date="-"), prompt="P")
    assert "**DOI**" not in out["summaryText"]


# The DOI prefix's separator. Both bodies were built as f"{doi_final} {body}" with an unconditional
# literal space, so a row whose document states no injury date - where the prefix is "" - stored a body
# beginning with a space. Nothing downstream strips it, and the Word renderer writes the title, then
# ". ", then the body unmodified, so those entries shipped with TWO spaces after the title while their
# DOI-carrying and reviewer-edited neighbours shipped with one. The linked PDF showed one either way,
# because HTML collapses whitespace - so the .docx and the .pdf of one record disagreed.
#
# The test above asserts only that "**DOI**" is absent, which is why nothing pinned this.
def _stub_extract(path, pages, mark_pages=False, **_kw):
    """`ocr.extract_pages_with_report` stand-in: fixed text and a clean per-page report."""
    return "OCR text", _clean(pages)


def _stub_a_clean_summarize(monkeypatch, audit=None):
    """OCR + generation stubbed; ``audit`` replaces the verify result when a rewrite is wanted."""
    monkeypatch.setattr(se, "extract_pages_with_report", _stub_extract)
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", audit or (lambda *a, **k: _NO_ISSUES))


@pytest.mark.parametrize("injury", ["-", ""])
def test_a_row_with_no_injury_date_does_not_start_its_body_with_a_space(monkeypatch, injury):
    _stub_a_clean_summarize(monkeypatch)
    body = se.summarize_row("/x.pdf", _row(injury_date=injury), prompt="P")["summaryText"]
    assert "**DOI**" not in body
    assert body == body.lstrip(), f"body starts with whitespace: {body[:24]!r}"


def test_a_row_with_an_injury_date_keeps_exactly_one_space_after_the_prefix(monkeypatch):
    """The separator moved onto the prefix, so the DOI case must be unchanged - one space, not zero."""
    _stub_a_clean_summarize(monkeypatch)
    body = se.summarize_row("/x.pdf", _row(injury_date="09/25/23"), prompt="P")["summaryText"]
    assert body.startswith("**DOI**: 09/25/23. ")
    assert not body.startswith("**DOI**: 09/25/23.  "), "two spaces after the prefix"


def test_an_audited_body_also_starts_with_content(monkeypatch):
    """`verified_text` is built the same way and is what `effective_text()` PREFERS, so the audited
    body carried the leading space too - and that is the one the export actually delivers.

    An issue is required for the rewrite to be kept at all: with none, the raw body stands and
    verifiedText is None.
    """
    _stub_a_clean_summarize(
        monkeypatch,
        audit=lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Audited body text.",
            "fixed_title": title,
            "issues": [{"type": "unsupported", "detail": "a fabrication"}],
            "ok": True,
        },
    )
    verified = se.summarize_row("/x.pdf", _row(injury_date="-"), prompt="P", verify=True)[
        "verifiedText"
    ]
    assert verified is not None, "the audit returned a rewrite, so verifiedText must be set"
    assert verified == verified.lstrip(), f"audited body starts with whitespace: {verified[:24]!r}"


def test_a_reviewers_corrected_injury_date_reaches_the_summary(monkeypatch):
    """WHEN a reviewer has corrected a row's injury date, THE SYSTEM SHALL use the corrected value.

    This is the defect the single-source change exists to fix. Summarize used to run its own isolated
    read whose result WON over the row, so a manual correction was silently discarded - which is why
    "zero reviewer DOI corrections across 2,247 rows" looked like agreement rather than the absence of
    any feedback loop. The row now decides, unconditionally.
    """
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    out = se.summarize_row("/x.pdf", _row(injury_date="05/07/2018"), prompt="P")
    assert out["summaryText"].startswith("**DOI**: 05/07/2018.")


def test_verify_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False) if system_msg == se.TITLE_PROMPT else ("Body text", False)
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

    def fake_retry(client, *, model, contents, config, **_kw):
        captured["config"] = config
        return SimpleNamespace(
            text="HALF A SUMMARY",
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
        )

    monkeypatch.setattr(gm, "get_genai_client", lambda: object())
    monkeypatch.setattr(gm, "generate_with_retry", fake_retry)

    text, truncated = se._generate("m", "sys", "user text", 0.0, max_output_tokens=4321)

    assert captured["config"].max_output_tokens == 4321
    assert text == "HALF A SUMMARY"
    assert truncated is True


def test_normal_finish_is_not_reported_as_truncated(monkeypatch):
    # WHEN the model finishes normally, THE SYSTEM SHALL NOT report truncation.
    from types import SimpleNamespace

    from google.genai import types

    monkeypatch.setattr(gm, "get_genai_client", lambda: object())
    monkeypatch.setattr(
        gm,
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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, user_text, temperature, max_output_tokens=None: (
            ("Title - Dr", False)
            if system_msg == se.TITLE_PROMPT
            else ("Summary body cut off mid-sen", True)
        ),
    )
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert out["truncated"] is True
    assert out["manualCheck"] == ""  # the row's own review flag is untouched
    assert out["summaryText"].endswith("Summary body cut off mid-sen")
    assert "Truncated" not in out["summaryTitle"]


def _system_messages(monkeypatch, row, **kw):
    """Run summarize_row against stubs and return every system message it sent."""
    seen = []

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        seen.append(system_msg)
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
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

    assert system_msg.startswith(se.build_preamble(category) + "CATEGORY PROMPT")  # rules first
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
    # Asserted on the study block alone, not on the whole message: categories 1 and 2 legitimately
    # carry a document-date block of their own (see the current-visit tests).
    assert "APPEAR AS THEIR OWN DOCUMENT" not in system_msg
    assert "MRI OF THE LUMBAR SPINE" not in system_msg


@pytest.mark.parametrize("studies", [None, [], [{"title": "-", "date": "-"}], [{"title": ""}]])
def test_nothing_listable_leaves_the_system_message_unchanged(monkeypatch, studies):
    # A record with no standalone studies, or rows whose titles never got extracted, must send the
    # prompt exactly as before - not an empty heading the model has to interpret.
    (system_msg,) = _system_messages(monkeypatch, _row(category="12"), standalone_studies=studies)
    assert system_msg == se.build_preamble("12") + "CATEGORY PROMPT"


@pytest.mark.parametrize("category", ["1", "2"])
def test_a_treating_report_is_told_which_encounter_it_is(monkeypatch, category):
    # WHEN a category-1 or 2 row is summarized, THE SYSTEM SHALL name the document's own date in the
    # system message, so a recap of an earlier visit can be identified by date rather than guessed
    # at. That earlier visit has its own sub-document here and is summarized in its own right.
    (system_msg,) = _system_messages(monkeypatch, _row(category=category, date="03/09/2023"))

    assert system_msg.startswith(se.build_preamble(category) + "CATEGORY PROMPT")
    assert "THIS DOCUMENT IS DATED 03/09/2023" in system_msg
    assert "has its own document in this record" in system_msg


def _audit_document_date(monkeypatch, row):
    """The `document_date` the AUDIT call receives for ``row`` (None when it is withheld)."""
    seen = {}

    def fake_verify(model, source, summary, title=None, document_date=None):
        seen["document_date"] = document_date
        return _NO_ISSUES

    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", fake_verify)
    se.summarize_row("/x.pdf", row, prompt="P", verify=True)
    return seen["document_date"]


@pytest.mark.parametrize("category", ["3", "5", "9", "12", "13", "100"])
def test_the_audit_is_not_given_a_document_date_outside_the_current_visit_categories(
    monkeypatch, category
):
    """The audit must be gated exactly like generation, because the date is the SWITCH for house
    rule 6.

    Rule 6 tells the audit that content the source attributes to an earlier date "does not belong in
    this summary", and it "applies ONLY when a document date is given". `summarize_row` passed the date
    to the audit for every category while giving the generation block only to categories 1 and 2 - so
    the audit enforced on medico-legal evaluations and depositions the rule the generator was
    deliberately forbidden to state, for the reason the sibling test states: it would "risk dropping
    wanted content".

    The rewrite was then accepted: `prior_visit` is not in `_CORRECTION_ONLY_ISSUES`, so
    `_drops_required_headings` returns False, `verified_text` is stored, and `effective_text()` prefers
    it over the raw body.
    """
    assert _audit_document_date(monkeypatch, _row(category=category, date="03/09/2023")) is None, (
        "arming house rule 6 here can delete the injury history the category prompt requires"
    )


@pytest.mark.parametrize("category", ["1", "2"])
def test_the_audit_still_gets_the_document_date_for_a_treating_report(monkeypatch, category):
    """The gate must not withhold it where rule 6 is wanted - categories 1 and 2 are the two that
    recap an earlier visit before reporting the current one, and that recap is what rule 6 removes."""
    assert _audit_document_date(monkeypatch, _row(category=category, date="03/09/2023")) == (
        "03/09/2023"
    )


@pytest.mark.parametrize("category", ["3", "5", "9", "12", "13", "100"])
def test_other_categories_are_not_given_a_document_date(monkeypatch, category):
    # A medico-legal evaluation is REQUIRED to carry the injury history, and a diagnostic study has
    # no prior visit to confuse, so the rule would only cost tokens and risk dropping wanted content.
    (system_msg,) = _system_messages(monkeypatch, _row(category=category, date="03/09/2023"))
    assert "THIS DOCUMENT IS DATED" not in system_msg


@pytest.mark.parametrize("date", [None, "", "   ", "-"])
def test_no_document_date_means_no_date_block(monkeypatch, date):
    # Segmentation could not read a date. Asserting an empty or "-" date would be worse than saying
    # nothing: the model would have to interpret a broken instruction.
    (system_msg,) = _system_messages(monkeypatch, _row(category="1", date=date))
    assert system_msg == se.build_preamble("1") + "CATEGORY PROMPT"


def test_the_document_date_is_handed_to_the_verify_pass(monkeypatch):
    # House rule 6 (no previous-visit content) is only checkable against the document's own date, so
    # the audit needs it too - the source text alone cannot say which of its dates is this one.
    seen = {}
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)

    def fake_verify(model, source, summary, title=None, document_date=None):
        seen["date"] = document_date
        return {"fixed_text": summary, "fixed_title": title, "issues": []}

    monkeypatch.setattr(se, "verify_summary", fake_verify)
    se.summarize_row("/x.pdf", _row(date="03/09/2023"), prompt="P", verify=True)
    assert seen["date"] == "03/09/2023"


def test_the_preamble_carries_the_house_rules_the_editors_asked_for():
    # Six rules requested 2026-07-30 off a live export. Pinned by their operative words rather than
    # whole sentences, so the wording can be tuned without breaking the test, but a rule cannot be
    # deleted silently.
    preamble = se.build_preamble("1")  # an exam category, which receives every content block
    assert "Do NOT report the patient's height or weight" in preamble  # item 1
    assert "drop quality (sharp, dull, aching" in preamble  # item 5
    assert "Range of motion" in preamble  # item 4
    assert "ordinary sentence case" in preamble  # item 6
    # Acronyms must stay exempt, or the rule renders MRI as "Mri".
    assert "MRI, CT, EMG" in preamble


def test_the_vitals_rule_covers_height_and_weight_only():
    # Adrian scoped this to height and weight on 2026-07-30 and reserved the other vitals for a later
    # call, so a broader ban would be taking a decision that is not ours. Pinned as an ABSENCE, which
    # is the only way to stop the rule quietly creeping back to "all vital signs".
    preamble = se.build_preamble("1")
    assert "height and weight ONLY" in preamble
    assert "other vital signs are left to your judgement" in preamble
    # BMI is 52 numbered DIAGNOSES in the human corpus, never a vital sign; sweeping it up would
    # delete diagnoses.
    assert "BMI is not restricted" in preamble


def test_the_range_of_motion_rule_names_itself_as_the_one_inference_exception():
    # The preamble opens with "Do NOT infer, assume, extrapolate". Comparing a measurement against a
    # textbook normal range IS inference, so the rule has to say so explicitly - an unacknowledged
    # contradiction between a shared rule and a specific one is the defect class that produced #53
    # and E-08, where the model was told opposite things and picked one at random.
    preamble = se.build_preamble("1")
    assert "Do NOT infer" in preamble
    assert "ONE exception to the no-inference rule" in preamble


def test_the_preamble_is_assembled_per_category():
    """T4: the shared block reached 4,927 chars, 81% of the system message for a one-line laboratory
    summary - so category 14 was being instructed about depositions, embedded reviews, range of motion
    and pain scales, none of which can apply to it. Each block now goes only where it can bind."""
    lab = se.build_preamble("14")
    treating = se.build_preamble("1")
    deposition = se.build_preamble("9")

    # A laboratory result has no examination, no pain scale and no joint.
    for absent in ("Range of motion", "For pain, give frequency", "height or weight"):
        assert absent not in lab, absent
    # But it MUST keep the verdict rule, or a normal result is dropped and the summary is empty.
    assert "verdict IS the content" in lab
    # A treating report is the mirror image.
    assert "Range of motion" in treating
    assert "For pain, give frequency" in treating
    assert "verdict IS the content" not in treating
    # Only the two medico-legal categories are told about an embedded records review.
    assert "review of earlier medical records" in se.build_preamble("13")
    assert "review of earlier medical records" not in treating
    # Every category keeps the factuality rules, the point-scope rule and the style rules.
    for preamble in (lab, treating, deposition):
        assert "Do NOT infer" in preamble
        assert "Include a point ONLY if" in preamble
        assert "ordinary sentence case" in preamble
        assert "Bold ONLY the short point/section labels" in preamble
    # The paragraph rule and the deposition rule are mutually exclusive, never both. The deposition
    # wording changed on 2026-08-06 from one line per page to groups of three, so this asserts the
    # CURRENT rule - if it ever asserted both, the two would be contradicting each other in one
    # preamble, which is the defect the category-9 prompt already had.
    assert "ONE continuous paragraph" in treating
    assert "GROUPS OF THREE" not in treating
    assert "GROUPS OF THREE" in deposition
    assert "ONE continuous paragraph" not in deposition
    # And the point of the exercise: the laboratory prompt is materially shorter.
    assert len(lab) < len(treating) * 0.7


def test_a_block_reference_never_dangles_after_assembly():
    # Two blocks used to open by pointing at a neighbour ("That rule does NOT apply...", "The
    # single-paragraph rule does NOT apply..."). Under per-category assembly the neighbour can be
    # absent, which would leave the model reading an exception to a rule it was never given.
    for cat in ("1", "3", "9", "14", "100"):
        preamble = se.build_preamble(cat)
        assert "That rule does NOT apply" not in preamble
        assert "The single-paragraph rule does NOT apply" not in preamble


def test_an_unknown_category_receives_every_block():
    # An admin can create a category at any time via POST /admin/categories. A new id must not be
    # silently under-instructed, so the default is INCLUDE - only a KNOWN id has blocks withheld.
    unknown = se.build_preamble("777")
    for block in (
        "Do NOT infer",
        "Report positive and abnormal findings",
        "verdict IS the content",
        "review of earlier medical records",
        "height or weight",
        "For pain, give frequency",
        "Range of motion",
        "ONE continuous paragraph",
        "ordinary sentence case",
    ):
        assert block in unknown, block
    # ...except the deposition format, which contradicts the paragraph rule.
    assert "page by page" not in unknown


def test_the_instruction_is_the_last_thing_in_a_multimodal_payload(monkeypatch):
    """G-03: Google's guidance is context first, instruction last. The instruction used to sit
    BETWEEN the page images and the OCR text, i.e. in the middle of the payload."""
    captured = {}

    def fake_generate(model, system_msg, contents, temperature, max_output_tokens=None):
        if system_msg != se.TITLE_PROMPT:
            captured["contents"] = contents
        return _fake_generate(model, system_msg, contents, temperature)

    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR BODY", _clean(pages)),
    )
    monkeypatch.setattr(se, "_page_image_parts", lambda path, start, end: ["IMG1", "IMG2"])
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setattr(se.get_settings(), "summary_multimodal", True, raising=False)

    se.summarize_row("/x.pdf", _row(), prompt="P")

    parts = captured["contents"]
    assert parts[:2] == ["IMG1", "IMG2"]  # images first
    assert "OCR BODY" in parts[2].text  # then the text
    assert parts[-1].text == se._MULTIMODAL_INSTRUCTION  # instruction LAST, on its own
    # It must no longer claim the text is still to come.
    assert "follows" not in se._MULTIMODAL_INSTRUCTION
    assert "OCR text above" in se._MULTIMODAL_INSTRUCTION


def test_the_caps_transform_runs_on_the_body_but_never_the_title(monkeypatch):
    # T6: capitalisation is mechanical, so a transform is right every time where a prompt rule is not.
    # The title is exempt - it is an ALL CAPS header by design in 812 of 813 measured human entries.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "_generate",
        lambda model, system_msg, contents, temperature, max_output_tokens=None: (
            ("JANE SMITH, M.D. MRI OF THE LEFT KNEE", False)
            if system_msg == se.TITLE_PROMPT
            else ("**Diagnoses**: CARPAL TUNNEL SYNDROME of the right wrist.", False)
        ),
    )
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert "Carpal tunnel syndrome" in out["summaryText"]
    assert "CARPAL TUNNEL SYNDROME" not in out["summaryText"]
    # The title keeps every capital.
    assert "MRI OF THE LEFT KNEE" in out["summaryTitle"]
    # The stored model input is the fine-tuning pair and must stay byte-exact.
    assert out["sourceText"] == "OCR"


def test_the_caps_transform_also_cleans_the_audits_rewrite(monkeypatch):
    # effective_text() delivers verified_text, so a capital reintroduced by the audit would ship.
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("OCR", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "**Employer**: CEDAR RIDGE LOGISTICS, INC.",
            "fixed_title": None,
            "issues": [{"type": "capitalization", "detail": "employer in capitals"}],
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)
    assert "Cedar Ridge Logistics, Inc." in out["verifiedText"]
    assert "CEDAR RIDGE LOGISTICS" not in out["verifiedText"]


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
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    assert se.summarize_row("/x.pdf", _row(), prompt="P")["truncated"] is False


# The heading guard (plan 2026-07-31 task 1). Measured on the current build: 7 of 16 audited summaries
# lost their bold point headings, 5 of them every heading, against 7.3% before the audit's house rules
# landed. The audit was reading `**Body part being treated**:` - required structure - as a stray
# capitalised header and folding it into prose, storing its own reason as "Summary contains capitalized
# headers". The prompt now forbids that, but a prompt is a request; the guard is the guarantee.
_LABELLED = "**Body part being treated**: Lower back. **Treatment provided**: Shockwave therapy."
_PROSE = "The lower back was treated with shockwave therapy."


def _labelled_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
    """Like _fake_generate, but the body carries two bold point headings for the guard to protect."""
    text = "Progress Note - Dr Smith" if system_msg == se.TITLE_PROMPT else _LABELLED
    return text, False


def _stub_verify(monkeypatch, fixed_text, issues, fixed_title=None):
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _labelled_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": fixed_text,
            "fixed_title": fixed_title if fixed_title is not None else title,
            "issues": issues,
        },
    )


def test_a_cosmetic_rewrite_that_drops_headings_is_rejected(monkeypatch, caplog):
    """WHEN the audit returns fewer bold headings and every issue is correction-only, THE SYSTEM SHALL
    keep the raw body, still store the issues, and warn naming the page range and issue types."""
    _stub_verify(monkeypatch, _PROSE, [{"type": "capitalization", "detail": "capitalized headers"}])

    with caplog.at_level("WARNING"):
        out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    # verifiedText None means effective_text() falls back to the raw body - the labels survive.
    assert out["verifiedText"] is None
    assert "**Body part being treated**" in out["summaryText"]
    # The reviewer still sees what was flagged; the guard is not a silent swallow.
    assert out["verifyIssues"] == [{"type": "capitalization", "detail": "capitalized headers"}]
    assert "1-2" in caplog.text
    assert "capitalization" in caplog.text


def test_a_substantive_rewrite_that_drops_headings_is_accepted(monkeypatch):
    """WHEN an issue lies outside the correction-only pair, THE SYSTEM SHALL store the audited body.

    An unsupported claim can legitimately take a whole point - and its heading - with it. Blocking
    that would suppress exactly the faithfulness fix this pass exists for.
    """
    _stub_verify(
        monkeypatch,
        _PROSE,
        [
            {"type": "capitalization", "detail": "capitalized headers"},
            {"type": "unsupported", "detail": "a fabricated diagnosis"},
        ],
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is not None
    assert "shockwave therapy" in out["verifiedText"].lower()


def test_a_renamed_heading_passes_the_guard_untouched(monkeypatch):
    """WHEN the audit RENAMES a heading without reducing the count, THE SYSTEM SHALL store its body.

    The distinction the whole design turns on: correcting a wrong heading is the behaviour being asked
    for. A guard that compared heading TEXT instead of counts would block precisely that.
    """
    renamed = "**Body part treated**: Lower back. **Treatment given**: Shockwave therapy."
    _stub_verify(monkeypatch, renamed, [{"type": "capitalization", "detail": "heading case"}])

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is not None
    assert "**Body part treated**" in out["verifiedText"]


def test_a_vitals_fix_that_empties_a_point_may_drop_its_heading(monkeypatch):
    """House rule 1 removes height and weight, so a vitals fix can legitimately empty a point and take
    its heading. `vitals` is excluded from the correction-only set for exactly this reason."""
    _stub_verify(
        monkeypatch,
        "**Body part being treated**: Lower back.",
        [{"type": "vitals", "detail": "height and weight"}],
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is not None
    assert "Treatment provided" not in out["verifiedText"]


def test_a_rewrite_that_keeps_every_heading_is_accepted(monkeypatch):
    """Regression guard on today's behaviour: no heading lost, so the guard must not fire."""
    fixed = "**Body part being treated**: Lower back. **Treatment provided**: Shockwave to L4-L5."
    _stub_verify(monkeypatch, fixed, [{"type": "capitalization", "detail": "word case"}])

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is not None
    assert "L4-L5" in out["verifiedText"]


def test_a_rejected_body_still_stores_a_corrected_title(monkeypatch):
    """WHEN the body rewrite is rejected but the title was corrected, THE SYSTEM SHALL store the title.

    effective_title() and effective_text() fall back independently, so a wrong date or laterality in
    the title must still be fixed - it is the first thing a client reads.
    """
    _stub_verify(
        monkeypatch,
        _PROSE,
        [{"type": "capitalization", "detail": "capitalized headers"}],
        fixed_title="CORRECTED HEADER",
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is None  # body rejected
    assert out["verifiedTitle"] == "CORRECTED HEADER (Pages 1-2)"  # title still corrected


def test_the_guard_never_fires_on_a_body_that_had_no_headings(monkeypatch):
    """A prose-format category has nothing to lose, so the guard must stay out of the way entirely."""
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", _fake_generate)  # returns "Summary body", no bold
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Summary body, re-cased.",
            "fixed_title": title,
            "issues": [{"type": "capitalization", "detail": "word case"}],
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedText"] is not None
    assert "re-cased" in out["verifiedText"]


def test_the_real_row_252_shape_is_rejected(monkeypatch):
    """Replay of the live defect: two headings to zero, two capitalization issues. This is the exact
    shape that reached the tester as a format inconsistency between four copies of one form."""
    _stub_verify(
        monkeypatch,
        _PROSE,
        [
            {"type": "capitalization", "detail": "Summary contains capitalized headers."},
            {"type": "capitalization", "detail": "facility name in capitals"},
        ],
    )

    out = se.summarize_row(
        "/x.pdf", _row(category="5", start=252, end=258), prompt="P", verify=True
    )

    assert out["verifiedText"] is None
    assert out["summaryText"].count("**") == 4  # both headings intact in the stored raw body


# "Only positives" widened to absences, refusals and inconclusives (plan 2026-07-31 task 3). The
# boundary that must not move is the diagnostic verdict: half of human imaging entries state a normal
# impression and a third contain nothing else, so telling categories 3 and 14 to omit a normal result
# would empty them - the exact regression PR #55 was written to fix.
def test_the_widened_omission_reaches_an_examination_category():
    preamble = se.build_preamble("1")
    assert "REFUSED" in preamble
    assert "INCONCLUSIVE" in preamble
    assert "no known allergies" in preamble


@pytest.mark.parametrize("category", ["3", "14"])
def test_a_verdict_category_is_never_told_to_omit_its_verdict(category):
    """WHEN the category is 3 or 14, THE SYSTEM SHALL ask for the impression even when it is normal."""
    preamble = se.build_preamble(category)
    assert "the verdict IS the content" in preamble
    # The widened omission must not reach it at all - for these documents the verdict is the content.
    assert "REFUSED" not in preamble
    assert "INCONCLUSIVE" not in preamble


def test_an_unknown_category_receives_both_blocks_without_them_contradicting():
    """WHEN build_preamble is called with an id in neither set, THE SYSTEM SHALL emit both blocks and
    the carve-out that reconciles them.

    Unknown ids default to INCLUDE, so a category an admin creates at runtime gets both the widened
    omission and the verdict rule. Without the carve-out sentence it would be told to omit an
    inconclusive result by one block and to report it by the next.
    """
    preamble = se.build_preamble("999")
    assert "INCONCLUSIVE" in preamble  # widened omission present
    assert "the verdict IS the content" in preamble  # verdict rule present
    # The reconciling sentence, and it must come before the verdict rule it defers to.
    assert "that rule wins" in preamble
    assert preamble.index("that rule wins") < preamble.index("the verdict IS the content")


# --- the generated title must be a header line, not a paragraph -------------------------------


def test_over_long_generated_title_falls_back_to_the_row_title():
    """Measured on the box 2026-08-14: for one row the title model returned ~620 characters - a
    paragraph, not a header line - and the decorated value overflowed summaries.title (varchar 512),
    killing a 124-row job at row 109 with an unclassified DataError. Identical on 2.5-pro and
    3.5-flash, so this is the title call, not the body model."""
    assert se._usable_title("X" * 620, "PROGRESS NOTE") == "PROGRESS NOTE"


def test_blank_generated_title_falls_back_to_the_row_title():
    # Mirrors summary_verify's existing rule that a blank fixed_title falls back to the original.
    assert se._usable_title("   ", "MRI OF THE LUMBAR SPINE") == "MRI OF THE LUMBAR SPINE"


def test_a_normal_header_line_is_kept():
    good = "JANE SMITH, M.D. VALLEY IMAGING. MRI OF THE CERVICAL SPINE WITHOUT CONTRAST."
    assert se._usable_title(good, "-") == good


def test_a_title_at_the_limit_is_kept_and_one_past_it_is_not():
    at_limit = "A" * se.MAX_GENERATED_TITLE
    assert se._usable_title(at_limit, "FALLBACK") == at_limit
    assert se._usable_title("A" * (se.MAX_GENERATED_TITLE + 1), "FALLBACK") == "FALLBACK"


def test_when_both_are_unusable_the_result_is_the_placeholder():
    # summaries.title is NOT NULL, so there has to be something; "-" is the codebase's own
    # "no value" convention (see ROW_FIELDS defaults).
    assert se._usable_title("X" * 620, "") == "-"


# --------------------------------------------------------------------------------------------------
# Body-model fallback. The body left 2.5-pro for AVAILABILITY, not quality: on 2026-08-13 Vertex
# refused it for this project outright, 0 of 8, and every summarize job failed. `generate_with_retry`
# already rides out transient 429s, so these cover the case where its whole budget is spent and the
# 429 is still coming - the row is answered by a lesser model rather than lost.


class _Rejected(Exception):
    """A 429 as `is_rate_limited` sees it: the status code alone, no SDK type needed."""

    code = 429


class _BadRequest(Exception):
    code = 400


def _fallback_fixtures(monkeypatch, *, fail_for, fallback="gemini-3.5-flash"):
    """Patch OCR + audit, and make `_generate` raise 429 for `fail_for` only. Returns the call log."""
    calls = []

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        calls.append({"model": model, "is_title": system_msg == se.TITLE_PROMPT})
        if model == fail_for and system_msg != se.TITLE_PROMPT:
            raise _Rejected("429 RESOURCE_EXHAUSTED")
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )
    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setenv("SUMMARY_BODY_FALLBACK_MODEL", fallback)
    get_settings.cache_clear()
    return calls


def test_body_falls_back_when_the_configured_model_is_refused(monkeypatch):
    """A spent retry budget on 429 answers the row with the fallback instead of failing it."""
    calls = _fallback_fixtures(monkeypatch, fail_for="gemini-2.5-pro")
    try:
        out = se.summarize_row("/x.pdf", _row(), model="gemini-2.5-pro", prompt="P")
    finally:
        get_settings.cache_clear()

    body_models = [c["model"] for c in calls if not c["is_title"]]
    assert body_models == ["gemini-2.5-pro", "gemini-3.5-flash"], (
        "fallback, not race, and once only"
    )
    # Provenance must name what ANSWERED, not what was asked for - job-level provenance cannot express
    # this, because models.py resolves the three models once at job creation on purpose.
    assert out["model"] == "gemini-3.5-flash"
    assert out["bodyFallbackFrom"] == "gemini-2.5-pro"
    assert "Summary body" in out["summaryText"]


def test_body_does_not_fall_back_on_a_non_429(monkeypatch):
    """Only capacity refusals fall back. A 400 is our bug and must surface, not be papered over."""
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        if system_msg != se.TITLE_PROMPT:
            raise _BadRequest("400 INVALID_ARGUMENT")
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setenv("SUMMARY_BODY_FALLBACK_MODEL", "gemini-3.5-flash")
    get_settings.cache_clear()
    try:
        row = _row()
        with pytest.raises(_BadRequest):
            se.summarize_row("/x.pdf", row, model="gemini-2.5-pro", prompt="P")
    finally:
        get_settings.cache_clear()


def test_body_does_not_fall_back_to_the_model_that_just_failed(monkeypatch):
    """When the body already IS the fallback there is nowhere below it: raise rather than retry it."""
    calls = _fallback_fixtures(monkeypatch, fail_for="gemini-3.5-flash")
    try:
        row = _row()
        with pytest.raises(_Rejected):
            se.summarize_row("/x.pdf", row, model="gemini-3.5-flash", prompt="P")
    finally:
        get_settings.cache_clear()
    assert [c["model"] for c in calls if not c["is_title"]] == ["gemini-3.5-flash"], (
        "no second call"
    )


def test_body_fallback_can_be_disabled(monkeypatch):
    """ "none" means fail the row, i.e. the pre-existing behaviour.

    Disabling needs an explicit token rather than an empty string: an UNSET key is also "", and that
    has to resolve to the default, so "" cannot carry both meanings.
    """
    calls = _fallback_fixtures(monkeypatch, fail_for="gemini-2.5-pro", fallback="none")
    try:
        row = _row()
        with pytest.raises(_Rejected):
            se.summarize_row("/x.pdf", row, model="gemini-2.5-pro", prompt="P")
    finally:
        get_settings.cache_clear()
    assert [c["model"] for c in calls if not c["is_title"]] == ["gemini-2.5-pro"]


def test_body_falls_back_on_a_spent_daily_quota_too(monkeypatch):
    """The second, less obvious path in - and it is deliberate.

    `generate_with_retry` re-raises a PerDay / free_tier 429 IMMEDIATELY rather than retrying it,
    because backoff cannot refill a daily allowance. But it is still a 429, so it reaches the fallback,
    and answering the row on a model with a different allowance is the behaviour we want. Pinned
    because it is easy to read the DSQ case as the only one and "correct" this away.
    """

    class _DailyQuota(Exception):
        code = 429

        def __str__(self):
            return "429 RESOURCE_EXHAUSTED: quota metric PerDay exceeded"

    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("raw OCR text", _clean(pages)),
    )

    def fake_generate(model, system_msg, user_text, temperature, max_output_tokens=None):
        if model == "gemini-2.5-pro" and system_msg != se.TITLE_PROMPT:
            raise _DailyQuota()
        return _fake_generate(model, system_msg, user_text, temperature)

    monkeypatch.setattr(se, "_generate", fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)
    monkeypatch.setenv("SUMMARY_BODY_FALLBACK_MODEL", "gemini-3.5-flash")
    get_settings.cache_clear()
    try:
        out = se.summarize_row("/x.pdf", _row(), model="gemini-2.5-pro", prompt="P")
    finally:
        get_settings.cache_clear()
    assert out["model"] == "gemini-3.5-flash"
    assert out["bodyFallbackFrom"] == "gemini-2.5-pro"
    # Both predicates hold for this exception, which is why it reaches the handler at all.
    assert is_rate_limited(_DailyQuota())
    assert is_daily_quota(_DailyQuota())


def test_a_successful_body_reports_no_fallback(monkeypatch):
    """bodyFallbackFrom is None on the happy path, so a caller can trust it as the downgrade signal."""
    _fallback_fixtures(monkeypatch, fail_for="nothing-fails")
    try:
        out = se.summarize_row("/x.pdf", _row(), model="gemini-2.5-pro", prompt="P")
    finally:
        get_settings.cache_clear()
    assert out["bodyFallbackFrom"] is None
    assert out["model"] == "gemini-2.5-pro"


# --- C9: an unreadable page is STATED in the deliverable rather than vanishing from it -------------


def _boom(*_a, **_kw):
    """Any call here is a test failure. Pins that a path is NOT taken - an unreadable row must reach
    no model, and a row reusing stored OCR must run no extraction."""
    raise AssertionError("this call must not happen")


def _errored(pages, text=""):
    """Stub ocr.extract_pages_with_report: `pages` failed to extract, and the row produced `text`."""
    return lambda path, p, mark_pages=False, **_kw: (text, _clean(p, errored=pages))


def _notice_fixtures(monkeypatch, errored, text=""):
    """A row whose pages errored, with every model seam wired to fail if it is reached."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored(errored, text=text))
    monkeypatch.setattr(se, "_generate", _boom)
    monkeypatch.setattr(se, "verify_summary", _boom)


def test_page_phrase_reads_naturally_for_one_page_and_for_many():
    assert se.page_phrase([7]) == "page 7"
    assert se.page_phrase([7, 8]) == "pages 7 and 8"
    assert se.page_phrase([11, 7, 8]) == "pages 7, 8 and 11"  # sorted into reading order
    assert se.page_phrase([]) == ""


def test_a_row_whose_pages_could_not_be_read_is_delivered_with_a_notice(monkeypatch):
    """WHEN a row's pages fail extraction, THE SYSTEM SHALL return a notice instead of raising.

    That row used to raise EmptyExtractionError, get no Summary, and so vanish from the report
    altogether - the reader was never told the pages existed.
    """
    _notice_fixtures(monkeypatch, errored=[1, 2])

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert out["noticeOnly"] is True
    assert out["unreadablePages"] == [1, 2]
    assert out["summaryText"] == se.unreadable_notice([1, 2])
    assert "unintelligible" in out["summaryText"].lower()
    assert "pages 1 and 2" in out["summaryText"]


def test_the_notice_names_only_the_pages_that_actually_failed(monkeypatch):
    """A row can lose one page of several. The notice must name that page, not the row's whole range,
    which is all EmptyExtractionError could ever say."""
    _notice_fixtures(monkeypatch, errored=[2])
    out = se.summarize_row("/x.pdf", _row(start=1, end=4), prompt="P")
    assert out["unreadablePages"] == [2]
    assert "page 2" in out["summaryText"]
    assert "1-4" not in out["summaryText"]


def test_a_notice_row_keeps_the_rows_title_date_and_page_range(monkeypatch):
    """Decision 3: the notice sits in the row's OWN entry, so it keeps the identifying metadata every
    other entry carries."""
    _notice_fixtures(monkeypatch, errored=[1, 2])
    out = se.summarize_row("/x.pdf", _row(title="MRI OF THE KNEE", date="2026-03-04"), prompt="P")
    assert out["summaryTitle"] == "MRI OF THE KNEE (Pages 1-2)"
    assert out["summaryDate"] == "2026-03-04"


@pytest.mark.parametrize("missing", ["", None, "-", "   "])
def test_a_notice_row_without_a_title_degrades_to_the_page_range(monkeypatch, missing):
    """Where segmentation could not read a header either, the entry names its PAGES rather than
    rendering the bare "-" sentinel.

    In the title proper, not the usual "(Pages X-Y)" suffix: `_export_title_and_text` strips that
    suffix from every entry, so a notice row with no header would otherwise reach the deliverable
    identified by nothing at all.
    """
    _notice_fixtures(monkeypatch, errored=[1, 2])
    out = se.summarize_row("/x.pdf", _row(title=missing), prompt="P")
    assert out["summaryTitle"] == "Pages 1-2"
    assert "-" != out["summaryTitle"]


def test_a_notice_row_keeps_the_rows_review_markers(monkeypatch):
    """The decorations are shared with the summary path, so a flagged or diagnostic row still reads
    the same way in the app; the export strips both either way."""
    _notice_fixtures(monkeypatch, errored=[1, 2])
    out = se.summarize_row("/x.pdf", _row(category="3", flag="x", title="CT"), prompt="P")
    assert out["summaryTitle"] == "[ManualCheck] CT [Diagnostic Study] (Pages 1-2)"


def test_a_notice_row_is_attributed_to_no_model(monkeypatch):
    """`model` NULL is what keeps the pro-vs-flash quality cohort clean: no model wrote this body, and
    the `unreadable` flag rather than a `model` sentinel is what records the fact."""
    _notice_fixtures(monkeypatch, errored=[1, 2])
    out = se.summarize_row("/x.pdf", _row(), prompt="P")
    assert out["model"] is None
    assert out["titleModel"] is None
    assert out["auditModel"] is None
    assert out["promptFingerprint"] is None
    assert out["auditFingerprint"] is None
    assert out["verified"] is False
    assert out["verifiedText"] is None
    assert out["truncated"] is False
    # None, not "": storing "" would record an empty extraction as a successful one.
    assert out["sourceText"] is None


def test_a_row_that_read_cleanly_but_holds_no_words_still_says_nothing(monkeypatch):
    """Decision 1: only genuine extraction FAILURES are announced.

    A film, a photograph or a separator sheet reads fine and simply carries no text. It raises exactly
    as it did before, so the deliverable gains no notice about a page there is nothing to explain.
    """
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("   ", _clean(pages, blank=pages)),
    )
    monkeypatch.setattr(se, "_generate", _boom)
    row = _row()
    with pytest.raises(EmptyExtractionError):
        se.summarize_row("/x.pdf", row, prompt="P")


def test_a_partially_unreadable_row_is_summarized_and_names_the_lost_pages(monkeypatch):
    """WHEN some pages fail but the rest produce text, THE SYSTEM SHALL summarize what it could read
    AND state what it could not. A ten-page row that lost one page used to deliver a summary of nine
    with nothing said."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([2], text="readable text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(start=1, end=4), prompt="P")

    assert out["noticeOnly"] is False  # a real summary, not a notice
    assert out["unreadablePages"] == [2]
    assert out["model"] is not None  # a model DID write this body
    assert "Summary body" in out["summaryText"]
    assert out["summaryText"].endswith(se.partial_unreadable_notice([2]))


def test_the_partial_notice_is_never_shown_to_the_faithfulness_audit(monkeypatch):
    """The audit checks the body against its SOURCE TEXT, and this sentence is by definition not in
    that source. Showing it invites the audit to "correct" an unsupported claim, or to record a
    faithfulness issue against wording we wrote ourselves - so it is appended after the pass."""
    audited = {}

    monkeypatch.setattr(se, "extract_pages_with_report", _errored([2], text="readable text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)

    def fake_verify(model, source, summary, title=None, document_date=None):
        audited["body"] = summary
        return {"fixed_text": summary, "fixed_title": title, "issues": [], "ok": True}

    monkeypatch.setattr(se, "verify_summary", fake_verify)

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert "unintelligible" not in audited["body"].lower()
    assert "unintelligible" in out["summaryText"].lower()


def test_the_partial_notice_survives_a_body_the_audit_corrected(monkeypatch):
    """effective_text() PREFERS verified_text, so the notice has to reach that body too or a row the
    audit rewrote would silently lose it."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([2], text="readable text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Corrected body",
            "fixed_title": title,
            "issues": [{"type": "unsupported", "detail": "x"}],
            "ok": True,
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert "Corrected body" in out["verifiedText"]
    assert out["verifiedText"].endswith(se.partial_unreadable_notice([2]))


def test_a_clean_row_carries_no_notice_and_no_unreadable_pages(monkeypatch):
    """The happy path is untouched: nothing is appended and the list stays empty, so an ordinary
    deliverable reads exactly as it did."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="readable text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert out["unreadablePages"] == []
    assert out["noticeOnly"] is False
    assert "unintelligible" not in out["summaryText"].lower()


def test_stored_ocr_reuse_takes_the_unreadable_pages_from_the_row(monkeypatch):
    """A row reusing the duplicate check's OCR cannot re-ask what failed without undoing the reuse
    (~45 minutes on a 1500-page record), so the caller supplies it from `page_texts.extract_ok` -
    summarize_engine is DB-free and cannot read that itself."""
    monkeypatch.setattr(se, "extract_pages_with_report", _boom)  # reuse means NO extraction
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row(
        "/x.pdf", _row(source_text="STORED OCR", unreadable_pages=[2]), prompt="P"
    )

    assert out["unreadablePages"] == [2]
    assert out["summaryText"].endswith(se.partial_unreadable_notice([2]))


def test_a_fresh_extraction_overrides_the_rows_unreadable_seed(monkeypatch):
    """The seed records what a PREVIOUS stage failed on, and an errored page is often a transient
    timeout that reads fine on the next attempt. Announcing a page as unintelligible when THIS run
    read it is worse than saying nothing, so what just happened wins."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="read fine this time"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(unreadable_pages=[1, 2]), prompt="P")

    assert out["unreadablePages"] == []
    assert "unintelligible" not in out["summaryText"].lower()


def test_the_notice_text_is_deterministic_and_distinct_per_case(monkeypatch):
    """Built in code, never model-generated: a model asked to describe a page it cannot read is the
    exact shape that invents content, and this text ships in a medical-legal deliverable."""
    # Page 1 is inside the default row's 1-2 range: _clean reports only pages the caller asked for,
    # exactly as the real extractor does, so an out-of-range page would report nothing failed.
    _notice_fixtures(monkeypatch, errored=[1])
    first = se.summarize_row("/x.pdf", _row(), prompt="P")["summaryText"]
    second = se.summarize_row("/x.pdf", _row(), prompt="P")["summaryText"]
    assert first == second == se.unreadable_notice([1])
    # The two notices tell the reader to expect different things, so neither can stand in for the
    # other: one replaces a summary, the other qualifies one.
    assert se.unreadable_notice([3]) != se.partial_unreadable_notice([3])
    assert "not covered by this summary" in se.partial_unreadable_notice([3])


# --- the embedded records-review tag (#159) -------------------------------------------------------
#
# An embedded review is split off as its own row and EXCLUDED, which is what the senior reviewer
# wants. But an excluded row produces no Summary, so those pages vanished from the deliverable with
# nothing said. His answer was a tag rather than inclusion, and these pin the three ways it could go
# wrong: the audit rewriting it, the verified body losing it, and it firing on an ordinary row.


def test_an_embedded_review_is_named_in_the_hosting_evaluations_summary(monkeypatch):
    """WHEN a row is seeded with embedded-review pages, THE SYSTEM SHALL name them in its body.

    The page range is the whole point: the reader is told those pages exist and where, which is what
    "vanished with nothing said" cost them.
    """
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="evaluation text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(embedded_review_pages=[46, 47, 48, 65]), prompt="P")

    assert "Pages 46-65 contain an embedded review of medical records" in out["summaryText"]
    assert "not summarized here" in out["summaryText"]
    assert out["embeddedReviewPages"] == [46, 47, 48, 65]


def test_the_embedded_review_tag_is_never_shown_to_the_faithfulness_audit(monkeypatch):
    """The audit checks the body against the SOURCE TEXT, and this sentence is by definition absent
    from it - so an audit that saw it could "correct" an unsupported claim or flag the row. Same
    reason the partial-unreadable notice is appended after the verify pass."""
    seen = {}

    def capture(model, source, summary, **kwargs):
        seen["summary"] = summary
        return _NO_ISSUES

    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="evaluation text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", capture)

    se.summarize_row("/x.pdf", _row(embedded_review_pages=[46, 65]), prompt="P")

    assert "embedded review" not in seen["summary"].lower()


def test_the_embedded_review_tag_survives_a_body_the_audit_corrected(monkeypatch):
    """`effective_text()` prefers the verified body, so a tag applied only to the raw one disappears
    the moment the audit changes anything. Applied to both, or it is lost exactly when the row was
    interesting enough to rewrite."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="evaluation text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda *a, **k: {"fixed_text": "A corrected body.", "issues": ["date"], "ok": False},
    )

    out = se.summarize_row("/x.pdf", _row(embedded_review_pages=[46, 65]), prompt="P")

    assert "embedded review of medical records" in out["verifiedText"]
    assert "embedded review of medical records" in out["summaryText"]


def test_an_ordinary_row_carries_no_embedded_review_tag(monkeypatch):
    """The happy path is untouched. Most rows have no excluded review beside them and must read
    exactly as they did."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([], text="ordinary text"))
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(se, "verify_summary", lambda *a, **k: _NO_ISSUES)

    out = se.summarize_row("/x.pdf", _row(), prompt="P")

    assert out["embeddedReviewPages"] == []
    assert "embedded review" not in out["summaryText"].lower()


def test_a_notice_only_row_never_carries_an_embedded_review_tag(monkeypatch):
    """Nothing was summarized, so there is no body for the tag to qualify - and "not summarized
    here" beneath "there was no text to summarize" would say the same thing twice about different
    pages."""
    monkeypatch.setattr(se, "extract_pages_with_report", _errored([1, 2]))
    monkeypatch.setattr(se, "_generate", _fake_generate)

    out = se.summarize_row("/x.pdf", _row(embedded_review_pages=[46, 65]), prompt="P")

    assert out["noticeOnly"] is True
    assert out["embeddedReviewPages"] == []
    assert "embedded review" not in out["summaryText"].lower()


def test_page_range_reads_as_a_single_page_when_only_one_is_involved():
    """A one-page review must not render as "Pages 45-45"."""
    assert se.embedded_review_notice([45]).startswith("Page 45 contains")
    assert se.embedded_review_notice([46, 65]).startswith("Pages 46-65 contain")
    assert se.embedded_review_notice([]) == ""


# `presentable_title` is the shared strip used by every path that produces a delivered document. It
# lives beside `_row_tags`, which applies the markers, because the two must agree - and they did not:
# the bundle export took the decorated title raw. None of the three markers appears in any of the
# eight human-written deliverables this output is measured against.
@pytest.mark.parametrize(
    ("decorated", "expected"),
    [
        (
            "[ManualCheck] MRI OF THE CERVICAL SPINE [Diagnostic Study] (Pages 12-19)",
            "MRI OF THE CERVICAL SPINE",
        ),
        ("WORK STATUS REPORT (Pages 3-3)", "WORK STATUS REPORT"),
        ("[ManualCheck] OFFICE VISIT", "OFFICE VISIT"),
        ("MRI LUMBAR SPINE [Diagnostic Study]", "MRI LUMBAR SPINE"),
        # the web view renders ranges with an en dash, so the suffix pattern accepts both
        ("CT CHEST (Pages 4–6)", "CT CHEST"),
        ("A TITLE CARRYING NO MARKERS", "A TITLE CARRYING NO MARKERS"),
        ("", ""),
        (None, ""),
    ],
)
def test_presentable_title_removes_every_internal_marker(decorated, expected):
    assert se.presentable_title(decorated) == expected


def test_presentable_title_keeps_a_page_reference_that_is_not_the_suffix():
    """The pattern is anchored at the END, so a page reference inside the title survives."""
    assert se.presentable_title("REVIEW OF RECORDS (Pages 1-9) ADDENDUM") == (
        "REVIEW OF RECORDS (Pages 1-9) ADDENDUM"
    )


# --- the two title paths that could still overflow varchar(512) -------------------------------


def _decorated_len(title: str) -> int:
    """Widest decoration the engine adds to a stored title."""
    return len(f"[ManualCheck] {title} [Diagnostic Study] (Pages 9999-9999)")


def test_an_over_long_row_title_cannot_overflow_the_column(monkeypatch):
    """DEMONSTRATES the bug, and asserts the PROPERTY rather than the new constant, so it fails on
    origin/main for the real reason: the decorated title is longer than the column.

    `_usable_title` bounds the value from the TITLE call - that is its whole reason for existing,
    after an over-long title "exceeded summaries.title (varchar 512), Postgres refused the row, and
    the per-row commit killed a 124-row job at row 109". Its FALLBACK branch had no length test.
    `row["title"]` is a review_rows.title varchar(512) stored verbatim, so a 500-character
    segmentation title plus the decoration is over the limit again - and the worker's persist sits
    OUTSIDE the per-row try/except, so that DataError kills the whole job rather than one row.
    """
    kept = se._usable_title("unusable prose " * 40, "A" * 500)
    assert _decorated_len(kept) <= 512


def test_a_row_title_that_fits_is_not_truncated():
    """GUARDS against cutting a normal header: only an over-long one is bounded."""
    normal = "SAMPLE, M.D. ACME CLINIC. PROGRESS REPORT."
    assert se._usable_title("   ", normal) == normal


def test_a_generated_title_at_its_limit_still_fits_the_column():
    """The two bounds have to be consistent, or one of them is decoration."""
    widest = "A" * se.MAX_GENERATED_TITLE
    assert se._usable_title(widest, "FALLBACK") == widest
    assert _decorated_len(widest) <= 512


def test_an_over_long_audited_title_is_not_stored(monkeypatch):
    """DEMONSTRATES the second hole, through summarize_row - which is the only place it shows.

    `_usable_title` was never APPLIED to the audit's title, so a unit test on it cannot show this.
    `verify_summary`'s schema declares a plain {"type": "string"} with no maxLength - and Gemini
    ignores maxLength anyway - and the result is written to verified_title, the sibling varchar(512)
    the original guard never covered. On origin/main this stores a ~600-character title.

    Rejected rather than truncated: an unusable correction resolves to the current title, so no
    verifiedTitle is stored - the same remedy a rejected BODY rewrite gets.
    """
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("Source text", _clean(pages)),
    )
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Summary body",
            "fixed_title": "X" * 600,
            "issues": [{"type": "unsupported", "detail": "d"}],
            "ok": True,
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedTitle"] is None or _decorated_len(out["verifiedTitle"]) <= 512
    # The issues are still reported, so the reviewer sees the audit ran.
    assert out["verifyIssues"]


def test_a_usable_audited_title_still_comes_through(monkeypatch):
    """GUARDS the other direction: bounding the audit must not discard a real correction."""
    monkeypatch.setattr(se, "_generate", _fake_generate)
    monkeypatch.setattr(
        se,
        "extract_pages_with_report",
        lambda path, pages, mark_pages=False, **_kw: ("Source text", _clean(pages)),
    )
    corrected = "PROGRESS NOTE - DR SMITH. 09/25/2023."
    monkeypatch.setattr(
        se,
        "verify_summary",
        lambda model, source, summary, title=None, document_date=None: {
            "fixed_text": "Summary body",
            "fixed_title": corrected,
            "issues": [{"type": "wrong_date", "detail": "d"}],
            "ok": True,
        },
    )

    out = se.summarize_row("/x.pdf", _row(), prompt="P", verify=True)

    assert out["verifiedTitle"] is not None
    assert corrected in out["verifiedTitle"]
