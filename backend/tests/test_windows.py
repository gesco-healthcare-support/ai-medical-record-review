"""Window packing: the byte budget AND the page cap, and why both are needed.

The byte budget bounds REQUEST SIZE (Vertex inline requests cap near 20 MB after base64, and page
density varies 60-260 KB/page). The page cap bounds DURATION, which bytes cannot: measured on the
box 2026-08-12 against document 68cb2500, a byte-light record (~52KB/page) packed all 241 of its
pages into one 12.5 MB window, and that single call took 179s against a 120s deadline - so it could
never complete, and segmentation failed 6/6 times. The same measurement showed 160 pages at 54.5s.

Pure-Python: page_raw_sizes is patched so no PDF is read.
"""

import pytest

from app.services import windows as windows_mod
from app.services.windows import byte_budgeted_windows

MB = 1024 * 1024


def _fake_sizes(monkeypatch, sizes):
    monkeypatch.setattr(windows_mod, "page_raw_sizes", lambda pdf_path, n: sizes[:n])


def _covers_every_page(result, n):
    covered = {page for start, end in result for page in range(start, end + 1)}
    return covered == set(range(1, n + 1))


def test_page_cap_binds_when_pages_are_byte_light(monkeypatch):
    # 241 pages at 52KB each is ~12.2 MB, so the 12.5 MB byte budget does NOT bind - this is exactly
    # document 68cb2500, whose single 241-page window took 179s and could never finish.
    _fake_sizes(monkeypatch, [52 * 1024] * 241)
    result = byte_budgeted_windows("x.pdf", 241, 30, int(12.5 * MB), 160)
    assert max(end - start + 1 for start, end in result) <= 160
    assert len(result) > 1
    assert _covers_every_page(result, 241)


def test_cap_is_inert_when_the_byte_budget_binds_first(monkeypatch):
    # Image-heavy pages: 400KB each means ~31 pages fill 12.5 MB, far below any sane page cap. The
    # windows must be byte-for-byte what they were before the cap existed, because these are the
    # records that already work - the 793- and 2673-page scans that never failed.
    sizes = [400 * 1024] * 200
    _fake_sizes(monkeypatch, sizes)
    with_cap = byte_budgeted_windows("x.pdf", 200, 30, int(12.5 * MB), 160)
    _fake_sizes(monkeypatch, sizes)
    effectively_uncapped = byte_budgeted_windows("x.pdf", 200, 30, int(12.5 * MB), 10_000)
    assert with_cap == effectively_uncapped
    assert max(end - start + 1 for start, end in with_cap) < 160


def test_single_oversized_page_still_raises(monkeypatch):
    # The existing fail-fast must survive: a page bigger than the whole budget cannot be split, and
    # silently truncating it would lose coverage.
    _fake_sizes(monkeypatch, [20 * MB, 1024])
    with pytest.raises(RuntimeError, match="raise WINDOW_BUDGET_MB"):
        byte_budgeted_windows("x.pdf", 2, 30, int(12.5 * MB), 160)


def test_cap_of_one_still_covers_every_page(monkeypatch):
    # Degenerate but must not lose a page or loop forever.
    _fake_sizes(monkeypatch, [1024] * 5)
    result = byte_budgeted_windows("x.pdf", 5, 30, int(12.5 * MB), 1)
    assert all(start == end for start, end in result)
    assert _covers_every_page(result, 5)


def test_max_pages_below_one_is_rejected(monkeypatch):
    # Mirrors the existing overlap guard: a nonsensical bound is an operator error, not something to
    # silently reinterpret.
    _fake_sizes(monkeypatch, [1024] * 5)
    with pytest.raises(ValueError, match="max_pages"):
        byte_budgeted_windows("x.pdf", 5, 30, int(12.5 * MB), 0)
