"""Unit tests for the difflib fuzzy title-to-category matcher."""

from mrr_ai.groups import groups
from mrr_ai.services.categorization import categorize_documents, normalize, similarity


def test_normalize_strips_punctuation_and_lowercases():
    assert normalize("Hello, World!") == "hello world"
    assert normalize("  X-Ray (MRI)  ") == "xray mri"


def test_similarity_identical_is_one():
    assert similarity("abc", "abc") == 1.0
    assert similarity("abc", "xyz") < 0.5


def test_exact_title_matches_its_group():
    # "Operative Report" lives in group 8, "Deposition" in group 9 (groups.py).
    assert categorize_documents("Operative Report", groups) == "8"
    assert categorize_documents("Deposition", groups) == "9"


def test_unmatched_title_falls_back_to_100():
    assert categorize_documents("totally unrelated gibberish zzqq", groups) == "100"


def test_non_string_title_does_not_raise_and_falls_back():
    assert categorize_documents(None, groups) == "100"
    assert categorize_documents(123, groups) == "100"


def test_threshold_controls_match():
    categories = {"A": ["hello world"]}
    assert categorize_documents("hello world", categories) == "A"
    # A partial title is below a strict threshold -> default bucket.
    assert categorize_documents("hello", categories, threshold=0.9) == "100"
