"""Unit tests for the Gemini helpers: upload, readiness polling, and response parsing."""

import pytest

from mrr_ai.services.gemini import parse_segment_item


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
