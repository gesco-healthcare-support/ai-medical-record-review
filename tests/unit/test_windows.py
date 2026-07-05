"""Unit tests for byte-budgeted overlapping window packing (synthetic PDFs only)."""

import pytest

from mrr_ai.services.windows import byte_budgeted_windows, next_window_start, page_raw_sizes


def _one_page_bytes(make_pdf, tmp_path):
    path = make_pdf(tmp_path / "one.pdf", pages=1)
    return page_raw_sizes(path, 1)[0]


def test_light_pdf_packs_into_one_window(make_pdf, tmp_path):
    path = make_pdf(tmp_path / "small.pdf", pages=5)
    assert byte_budgeted_windows(path, 5, overlap=30, budget_bytes=10**7) == [(1, 5)]


def test_budget_splits_with_overlap(make_pdf, tmp_path):
    one = _one_page_bytes(make_pdf, tmp_path)
    path = make_pdf(tmp_path / "six.pdf", pages=6)
    # Budget fits ~3 pages -> windows of 3, overlap capped at window//3 = 1 -> step 2.
    windows = byte_budgeted_windows(path, 6, overlap=30, budget_bytes=int(one * 3.5))
    assert windows[0][0] == 1 and windows[-1][1] == 6
    for (s1, e1), (s2, _e2) in zip(windows, windows[1:], strict=False):
        assert s2 <= e1, f"windows must overlap: {windows}"
        assert s2 > s1, f"windows must advance: {windows}"


def test_oversized_page_fails_fast(make_pdf, tmp_path):
    one = _one_page_bytes(make_pdf, tmp_path)
    path = make_pdf(tmp_path / "one.pdf", pages=1)
    with pytest.raises(RuntimeError, match="larger than"):
        byte_budgeted_windows(path, 1, overlap=30, budget_bytes=one // 2)


def test_overlap_cap_prevents_crawl_and_keeps_large_overlap():
    assert next_window_start(292, 333, 30) == 320  # dense window: step >= ~2/3 window
    assert next_window_start(1, 102, 30) == 73  # large window: full overlap preserved


def test_overlap_validator():
    with pytest.raises(ValueError):
        byte_budgeted_windows("unused.pdf", 1, overlap=0, budget_bytes=10**6)
