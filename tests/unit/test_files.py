"""Unit tests for the small file/date helpers."""

from datetime import datetime

from mrr_ai.services.files import allowed_file, count_lines_in_file, is_valid_date, parse_date


def test_allowed_file_accepts_only_pdf_case_insensitive():
    assert allowed_file("report.pdf") is True
    assert allowed_file("report.PDF") is True
    assert allowed_file("archive.a.b.pdf") is True
    assert allowed_file("notes.txt") is False
    assert allowed_file("noextension") is False


def test_parse_date_valid_and_fallback():
    parsed = parse_date("01/02/2020")
    assert (parsed.year, parsed.month, parsed.day) == (2020, 1, 2)
    assert parse_date("not-a-date") == datetime.min


def test_is_valid_date():
    assert is_valid_date("01/02/2020") is True
    assert is_valid_date("13/40/2020") is False
    assert is_valid_date("notadate") is False
    # "-" is the explicit "no date" sentinel and is treated as valid.
    assert is_valid_date("-") is True
    assert is_valid_date("  -  ") is True


def test_count_lines_in_file(tmp_path):
    target = tmp_path / "lines.txt"
    target.write_text("a\nb\nc\n", encoding="utf-8")
    assert count_lines_in_file(str(target)) == 3


def test_count_lines_in_file_missing_returns_zero(tmp_path):
    assert count_lines_in_file(str(tmp_path / "does-not-exist.txt")) == 0
