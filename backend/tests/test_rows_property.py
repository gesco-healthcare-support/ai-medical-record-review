"""Property-based tests for app.services.rows.validate_rows.

validate_rows shares the range + overlap + gaps-allowed rule with the client rowErrors
(frontend/lib/review-rows.ts). Documented differences (NOT full parity): the server coerces
non-integers via int() while the client rejects them; the server returns the FIRST error while the
client collects all; and the server additionally checks category membership. These tests state the
INVARIANTS and let Hypothesis generate hundreds of inputs, shrinking any failure to a minimal case.

The DB-backed category check (catalog.get_category_ids) is stubbed to a fixed set so every
generated row carries a known-valid category - this isolates the range / overlap / integer rules,
which are the logic under test. The category rule is exercised by the API tests, not here.
"""

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.services.rows import validate_rows

VALID_CATEGORY = "1"
# A constant, function-scoped monkeypatch stub is safe to reuse across Hypothesis examples (it
# never varies per example), so suppress the function_scoped_fixture health check on each test.
_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])

# Letters only -> int() always raises, so these never parse as a page number.
_non_integer = st.one_of(st.none(), st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1))


@pytest.fixture(autouse=True)
def _stub_categories(monkeypatch):
    monkeypatch.setattr(
        "app.services.catalog.get_category_ids",
        lambda session, active_only=True: {VALID_CATEGORY},
    )


@st.composite
def valid_rowset(draw):
    """Ascending, non-overlapping integer page ranges in [1, total]; gaps allowed (skipped pages)."""
    total = draw(st.integers(min_value=1, max_value=300))
    rows = []
    cursor = 1
    for _ in range(draw(st.integers(min_value=0, max_value=8))):
        start = cursor + draw(st.integers(min_value=0, max_value=5))  # optional leading gap
        if start > total:
            break
        end = start + draw(st.integers(min_value=0, max_value=min(total - start, 40)))
        rows.append({"start": start, "end": end, "category": VALID_CATEGORY})
        cursor = end + 1
    return rows, total


@_SETTINGS
@given(valid_rowset())
def test_valid_rowset_returns_none(data):
    """Invariant 1: a legal set of rows (ascending, non-overlapping, in range) validates clean."""
    rows, total = data
    assume(rows)  # the empty case is a distinct rule ("no rows to summarize")
    assert validate_rows(None, rows, total) is None


@_SETTINGS
@given(
    total=st.integers(min_value=10, max_value=300),
    first_len=st.integers(min_value=0, max_value=40),
    gap=st.integers(min_value=1, max_value=20),
    second_len=st.integers(min_value=0, max_value=40),
)
def test_gap_between_documents_is_allowed(total, first_len, gap, second_len):
    """Invariant 4: a deliberate gap (skipped junk pages) between two docs is NOT an error."""
    first_end = 1 + first_len
    start2 = first_end + gap + 1  # strictly after the gap => start2 > first_end (no overlap)
    end2 = start2 + second_len
    assume(end2 <= total)
    rows = [
        {"start": 1, "end": first_end, "category": VALID_CATEGORY},
        {"start": start2, "end": end2, "category": VALID_CATEGORY},
    ]
    assert validate_rows(None, rows, total) is None


@_SETTINGS
@given(
    total=st.integers(min_value=4, max_value=300),
    first_end=st.integers(min_value=2, max_value=60),
    start2=st.integers(min_value=1, max_value=60),
)
def test_overlap_is_rejected(total, first_end, start2):
    """Invariant 2: a row starting at or before the previous row's end is rejected as an overlap."""
    assume(first_end <= total)
    assume(1 <= start2 <= first_end)  # start2 <= previous_end triggers the overlap rule
    end2 = first_end  # range-valid so the range check passes and the overlap check is reached
    rows = [
        {"start": 1, "end": first_end, "category": VALID_CATEGORY},
        {"start": start2, "end": end2, "category": VALID_CATEGORY},
    ]
    result = validate_rows(None, rows, total)
    assert result is not None and "overlaps" in result


@_SETTINGS
@given(
    total=st.integers(min_value=1, max_value=300),
    bad=_non_integer,
    which=st.sampled_from(["start", "end"]),
)
def test_non_integer_pages_flagged(total, bad, which):
    """Invariant 3: a non-integer start/end is rejected with the integer-parse error."""
    row = {"start": 1, "end": 1, "category": VALID_CATEGORY}
    row[which] = bad
    result = validate_rows(None, [row], total)
    assert result is not None and "integers" in result


@_SETTINGS
@given(
    total=st.integers(min_value=1, max_value=200),
    mode=st.sampled_from(["start_below_one", "end_above_total", "start_after_end"]),
)
def test_out_of_range_row_is_rejected(total, mode):
    """Invariant 5: a page range outside [1, total], or with start > end, is rejected."""
    if mode == "start_below_one":
        row = {"start": 0, "end": min(2, total), "category": VALID_CATEGORY}
    elif mode == "end_above_total":
        row = {"start": 1, "end": total + 1, "category": VALID_CATEGORY}
    else:
        row = {"start": 2, "end": 1, "category": VALID_CATEGORY}
    result = validate_rows(None, [row], total)
    assert result is not None and "1 <= start <= end" in result


@_SETTINGS
@given(base=st.integers(min_value=1, max_value=40))
def test_fractional_page_is_rejected(base):
    """Invariant 6: a fractional float page (e.g. 3.5) is rejected, not silently truncated - so the
    server agrees with the client's Number.isInteger check instead of quietly accepting int(3.5)==3."""
    row = {"start": float(base) + 0.5, "end": base + 2, "category": VALID_CATEGORY}
    result = validate_rows(None, [row], base + 5)
    assert result is not None and "integers" in result
