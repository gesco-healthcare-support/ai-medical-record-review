"""Unit tests for the Gemini helpers: upload, readiness polling, and response parsing."""

from types import SimpleNamespace

import pytest

from mrr_ai.services import gemini as gemini_service
from mrr_ai.services.gemini import parse_segment_item, upload_to_gemini, wait_for_files_active


def test_parse_segment_item_full_record():
    item = {
        "id": "Doc1",
        "s": 1,
        "e": 5,
        "t": "Operative Report",
        "d": "01/02/2020",
        "i": "-",
        "m": "x",
    }
    assert parse_segment_item(item) == (1, 5, "Operative Report", "01/02/2020", "-", "x")


def test_parse_segment_item_title_alias_and_defaults():
    # "title" alias for "t"; missing d/i/m default to "-".
    item = {"s": 2, "e": 3, "title": "Progress Note"}
    assert parse_segment_item(item) == (2, 3, "Progress Note", "-", "-", "-")


def test_parse_segment_item_coerces_non_string_title():
    item = {"s": 1, "e": 1, "t": 123}
    assert parse_segment_item(item)[2] == "123"


def test_parse_segment_item_missing_page_raises():
    with pytest.raises(KeyError):
        parse_segment_item({"e": 2, "t": "x"})


def test_parse_segment_item_non_integer_page_raises():
    with pytest.raises(ValueError):
        parse_segment_item({"s": "abc", "e": 2, "t": "x"})


def test_upload_to_gemini_delegates_to_client(monkeypatch):
    uploaded = SimpleNamespace(display_name="d", uri="u", name="files/n")
    fake = SimpleNamespace(files=SimpleNamespace(upload=lambda file: uploaded))
    monkeypatch.setattr(gemini_service, "genai_client", fake)

    assert upload_to_gemini("/tmp/x.pdf") is uploaded


def test_wait_for_files_active_passes_when_active(monkeypatch):
    active = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"), name="files/n")
    fake = SimpleNamespace(files=SimpleNamespace(get=lambda name: active))
    monkeypatch.setattr(gemini_service, "genai_client", fake)

    # Should not raise.
    wait_for_files_active([SimpleNamespace(name="files/n")])


def test_wait_for_files_active_raises_when_not_active(monkeypatch):
    failed = SimpleNamespace(state=SimpleNamespace(name="FAILED"), name="files/n")
    fake = SimpleNamespace(files=SimpleNamespace(get=lambda name: failed))
    monkeypatch.setattr(gemini_service, "genai_client", fake)

    with pytest.raises(Exception, match="failed to process"):
        wait_for_files_active([SimpleNamespace(name="files/n")])
