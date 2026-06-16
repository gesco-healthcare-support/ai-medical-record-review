"""Integrity tests for the B5 category catalog."""

from mrr_ai.taxonomy import ALLOWED_IDS, CATEGORIES, DEFAULT_ID


def test_allowed_ids_match_categories():
    assert tuple(CATEGORIES.keys()) == ALLOWED_IDS
    assert DEFAULT_ID in CATEGORIES


def test_phantom_group_6_is_omitted():
    # Group 6 is undefined in the source taxonomy; it must not be a classification target.
    assert "6" not in CATEGORIES


def test_ids_are_within_the_known_range():
    valid = {str(i) for i in range(1, 15)} | {"100"}
    assert set(CATEGORIES) <= valid


def test_every_category_has_semantic_text():
    for cid, category in CATEGORIES.items():
        assert category.id == cid
        assert category.name.strip()
        assert category.description.strip()
        assert category.examples  # at least one example
        assert category.corpus.strip()
