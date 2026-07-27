"""Unit tests for the isolated DOI extraction service (app.services.summary_doi).

The vision call is mocked; these tests pin the reply-parsing, page cap, fail-safe, and the
summary DOI-prefix rewrite used by the backfill.
"""

import types as pytypes

import pytest

from app.services import summary_doi as sd


def test_clean_parses_pads_and_handles_none():
    assert sd._clean("09/25/2023") == "09/25/2023"
    assert sd._clean("The date of injury is 5/7/2018.") == "05/07/2018"
    assert sd._clean("05/07/2018, 06/01/2019") == "05/07/2018, 06/01/2019"
    assert sd._clean("-") == "-"
    assert sd._clean("none stated") == "-"
    assert sd._clean("") == "-"


def test_clean_dedups_preserving_order():
    assert sd._clean("09/25/2023 (see 09/25/2023)") == "09/25/2023"


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
    assert sd.extract_injury_date("/x.pdf", 1, 3) == "09/25/2023"


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


def test_apply_prefix_adds_when_absent():
    assert sd.apply_doi_prefix("Body text.", "09/25/2023") == "**DOI**:09/25/2023, Body text."


def test_apply_prefix_removes_when_dash():
    assert sd.apply_doi_prefix("**DOI**:09/25/2023, Body text.", "-") == "Body text."


def test_apply_prefix_replaces_existing():
    assert (
        sd.apply_doi_prefix("**DOI**:01/01/2000, Body.", "09/25/2023")
        == "**DOI**:09/25/2023, Body."
    )


def test_apply_prefix_strips_multi_doi_prefix():
    assert sd.apply_doi_prefix("**DOI**:06/04/2024, 01/03/2025, Body.", "-") == "Body."


def test_apply_prefix_leaves_non_prefixed_body_when_dash():
    assert sd.apply_doi_prefix("No prefix here.", "-") == "No prefix here."


def test_doi_prefix_returns_the_stored_prefix_with_every_stated_date():
    assert sd.doi_prefix("**DOI**:05/08/2022, Body.") == "**DOI**:05/08/2022,"
    # Two stated dates must both survive - the export re-applies exactly this string.
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
