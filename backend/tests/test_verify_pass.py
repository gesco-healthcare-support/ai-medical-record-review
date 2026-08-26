"""Which boundaries the verify pass spends a model call on.

`suspect_indices` had no test at all, which is how `verify_suspect_cap = 200` came to be treated as
the bound on a net it never bounds: at roughly 88 boundaries per record the cap never binds, so the
"wide net" is every boundary and the trigger heuristic below it is inert.

Measured on 28 reviewer-corrected records (2,456 boundaries, 239 reviewer merges), which is what the
narrow net is for: every boundary gives 57.4% precision at 49.0% recall, the triggered set alone
gives 60.2% at 45.6% from 31% fewer calls.
"""

from app.config import get_settings
from app.services.verify_pass import SHORT_ROW_PAGES, suspect_indices


def _row(start, end, category="1", date="-"):
    return {"start": start, "end": end, "category": category, "date": date, "title": "-"}


# Row 1 shares category AND a real date with row 0  -> triggered (same_cat_date)
# Row 2 is a long row with a different date          -> NOT triggered
# Row 3 is a single page                             -> triggered (short)
_ROWS = [
    _row(1, 10, "1", "01/01/2026"),
    _row(11, 20, "1", "01/01/2026"),
    _row(21, 40, "2", "02/01/2026"),
    _row(41, 41, "3", "03/01/2026"),
]


def test_the_wide_net_is_every_adjacent_boundary():
    """WHEN triggered_only is off, THE SYSTEM SHALL make every adjacent boundary a candidate."""
    assert suspect_indices(_ROWS, cap=1000, triggered_only=False) == [1, 2, 3]


def test_the_narrow_net_keeps_only_the_triggered_rows():
    """WHEN triggered_only is on, THE SYSTEM SHALL drop boundaries the heuristic did not select.

    Row 2 is the one dropped: a 20-page row whose category and date both differ from its
    predecessor. Rows 1 and 3 survive on the two triggers respectively.
    """
    assert suspect_indices(_ROWS, cap=1000, triggered_only=True) == [1, 3]


def test_a_short_row_is_triggered_at_the_boundary_of_the_constant():
    """The `short` trigger is <= SHORT_ROW_PAGES, so a row of exactly that length still counts.

    Pinned because the constant is the lever anyone re-tuning this pass will reach for first, and an
    off-by-one there silently changes which boundaries get checked.
    """
    exactly = [_row(1, 10, "1", "01/01/2026"), _row(11, 10 + SHORT_ROW_PAGES, "9", "09/09/2026")]
    one_over = [_row(1, 10, "1", "01/01/2026"), _row(11, 11 + SHORT_ROW_PAGES, "9", "09/09/2026")]

    assert suspect_indices(exactly, cap=1000, triggered_only=True) == [1]
    assert suspect_indices(one_over, cap=1000, triggered_only=True) == []


def test_a_shared_placeholder_date_does_not_trigger():
    """Two rows sharing category and the "-" placeholder are NOT the same_cat_date signal.

    The signal is "these two say they are the same document on the same day". An absent date on both
    says nothing, and treating it as agreement would fire the trigger on every undated pair - which
    on this corpus is most of them.
    """
    undated = [_row(1, 10, "1", "-"), _row(11, 20, "1", "-")]
    assert suspect_indices(undated, cap=1000, triggered_only=True) == []


def test_the_cap_still_truncates_and_triggered_rows_keep_priority():
    """WHEN the cap bites, THE SYSTEM SHALL keep triggered rows over untriggered ones.

    Independent of triggered_only, and the reason the wide net was defensible in the first place.
    """
    assert suspect_indices(_ROWS, cap=2, triggered_only=False) == [1, 3]
    assert suspect_indices(_ROWS, cap=0, triggered_only=False) == []


def test_the_default_comes_from_settings_and_is_off():
    """The capability ships inert: it changes what a reviewer is shown, so it is turned on
    deliberately on a box rather than by upgrading."""
    assert get_settings().verify_triggered_only is False
    assert suspect_indices(_ROWS, cap=1000) == [1, 2, 3]
