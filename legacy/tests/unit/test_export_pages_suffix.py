"""Regression tests for the export title's trailing "(pages X-Y)" suffix stripper.

The regex was hardened against ReDoS (Sonar S5852): possessive quantifiers and no
leading whitespace class keep re.search linear-time. These tests pin the stripping
behavior and guard the linear-time property so a future edit cannot reintroduce the
super-linear backtracking.
"""

import time

from mrr_ai.blueprints.documents_api import _PAGES_SUFFIX


def _strip(title):
    """Mirror the export call site: substitute the suffix away, then rstrip."""
    return _PAGES_SUFFIX.sub("", title).rstrip()


def test_strips_ascii_hyphen_range():
    assert _strip("Progress Report (pages 12-15)") == "Progress Report"


def test_strips_en_dash_range():
    assert _strip("Progress Report (pages 12–15)") == "Progress Report"


def test_case_insensitive_and_inner_spacing():
    assert _strip("Imaging note  (Pages 3 - 4)") == "Imaging note"


def test_leaves_plain_title_untouched():
    assert _strip("MRI of the lumbar spine") == "MRI of the lumbar spine"


def test_only_strips_a_trailing_suffix():
    # A page marker that is not at the end of the title must be preserved.
    assert _strip("Foo (pages 7-9) addendum") == "Foo (pages 7-9) addendum"


def test_linear_time_on_adversarial_input():
    # Near-match with a long whitespace run and no closing paren. With a leading
    # whitespace class this was O(n^2) under re.search; the hardened form is O(n).
    evil = "note (pages 1" + " " * 200_000
    start = time.perf_counter()
    assert _PAGES_SUFFIX.search(evil) is None
    assert time.perf_counter() - start < 1.0
