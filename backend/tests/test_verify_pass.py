"""Which boundaries the verify pass spends a model call on.

`suspect_indices` had no test at all, which is how `verify_suspect_cap = 200` came to be treated as
the bound on a net it never bounds: at roughly 88 boundaries per record the cap never binds, so the
"wide net" is every boundary and the trigger heuristic below it is inert.

Measured on 28 reviewer-corrected records (2,456 boundaries, 239 reviewer merges), which is what the
narrow net is for: every boundary gives 57.4% precision at 49.0% recall, the triggered set alone
gives 60.2% at 45.6% from 31% fewer calls.
"""

from app.config import get_settings
from app.services import verify_pass
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


def test_verify_and_merge_can_pin_the_net_instead_of_inheriting_the_box(monkeypatch):
    """DEMONSTRATES the #209 prerequisite: without this, flipping a live setting silently narrows
    the net under a harness whose job is to hold everything but the prompt constant.

    `VERIFY_TRIGGERED_ONLY` sits in the compose env block precisely so it can be flipped on a box
    and measured without a rebuild. But `verify_and_merge` called `suspect_indices(rows)` with no
    argument, so the boundary A/B - which runs this function on BOTH arms - would have inherited the
    flip and compared two narrow-net arms while reporting numbers gathered on the wide one. Nothing
    in the run would have looked wrong.

    Three cases, because the useful property is not "the argument is accepted" but "the caller wins
    over the box, in both directions, and omitting it still defers".
    """
    checked = []

    def spy(pdf_path, prev, row):
        checked.append(int(row["start"]))
        return False  # refute nothing, so the rows come back untouched

    monkeypatch.setattr(verify_pass, "_same_document", spy)
    monkeypatch.setattr(get_settings(), "verify_triggered_only", True)  # the box is narrow

    checked.clear()
    verify_pass.verify_and_merge("x.pdf", _ROWS, triggered_only=False)
    assert sorted(checked) == [11, 21, 41], "the caller asked for the wide net and must get it"

    checked.clear()
    verify_pass.verify_and_merge("x.pdf", _ROWS, triggered_only=True)
    assert sorted(checked) == [11, 41], "and the narrow net when it asks for that"

    checked.clear()
    verify_pass.verify_and_merge("x.pdf", _ROWS)
    assert sorted(checked) == [11, 41], "omitted still defers to the box - production's case"


# --------------------------------------------------------------------------------------------
# What happens to a REFUTED boundary.
#
# Every test above stops at which boundaries get checked. The spy at `test_verify_and_merge_can
# _pin_the_net...` returns False unconditionally, so the block that acts on a verdict has never
# been executed by any test in this suite - not one assertion covers a merge, a suggestion, or
# either stats key. These pin it before it is refactored.
# --------------------------------------------------------------------------------------------


def _flagged(start, end, category="1", date="01/01/2026", flag="-"):
    """A row carrying the ``flag`` key, which the auto-merge path reads and ``_row`` omits."""
    row = _row(start, end, category, date)
    row["flag"] = flag
    return row


def _refuting(*starts):
    """A `_same_document` stand-in that refutes exactly the boundaries whose row starts at ``starts``."""

    def spy(pdf_path, prev, row):
        return int(row["start"]) in starts

    return spy


def test_a_refuted_boundary_becomes_a_suggestion_and_keeps_every_row(monkeypatch):
    """WHEN auto is off, THE SYSTEM SHALL flag the refuted row and drop nothing.

    This is production's default. The reviewer, not the model, decides whether the merge happens,
    so a refuted boundary has to survive as a marked row rather than be applied.
    """
    rows = [_flagged(1, 10), _flagged(11, 20), _flagged(21, 30, "2", "02/01/2026")]
    monkeypatch.setattr(verify_pass, "_same_document", _refuting(11))

    out, stats = verify_pass.verify_and_merge("x.pdf", rows, auto=False, triggered_only=False)

    assert len(out) == 3, "nothing is merged away when the reviewer has not asked for it"
    assert out[1]["suggest_merge"] is True
    assert "suggest_merge" not in out[0] and "suggest_merge" not in out[2]
    assert stats == {"suspects": 2, "suggested": 1}
    assert "suggest_merge" not in rows[1], "the caller's own rows must come back untouched"


def test_auto_merge_absorbs_the_refuted_row_and_keeps_the_pages_tiled(monkeypatch):
    """WHEN auto is on, THE SYSTEM SHALL extend the predecessor over the refuted row and drop it.

    Tiling is the load-bearing property: the rows partition the document, so the survivor has to
    take on the absorbed row's ``end`` or the pages between them belong to no row at all.
    """
    rows = [_flagged(1, 10), _flagged(11, 20), _flagged(21, 30, "2", "02/01/2026")]
    monkeypatch.setattr(verify_pass, "_same_document", _refuting(11))

    out, stats = verify_pass.verify_and_merge("x.pdf", rows, auto=True, triggered_only=False)

    assert len(out) == 2, "the refuted row is absorbed, not marked"
    assert (out[0]["start"], out[0]["end"]) == (1, 20), "the survivor covers the absorbed pages"
    assert (out[1]["start"], out[1]["end"]) == (21, 30), "and the tiling continues unbroken"
    assert stats == {"suspects": 2, "merged_away": 1}, (
        "the stats key differs from the auto=False one"
    )
    assert rows[0]["end"] == 10, "the caller's own rows must come back untouched"


def test_an_absorbed_row_promotes_its_flag_onto_the_survivor(monkeypatch):
    """WHEN an absorbed row is flagged, THE SYSTEM SHALL carry that flag onto the survivor.

    The flag marks a row for attention. Absorbing a flagged row into an unflagged one without
    carrying the flag would silently discard the only signal that the row needed looking at.

    Whitespace and case are normalised, so the comparison is pinned with a value that only matches
    after both are applied.
    """
    rows = [_flagged(1, 10, flag="-"), _flagged(11, 20, flag=" X ")]
    monkeypatch.setattr(verify_pass, "_same_document", _refuting(11))

    out, _ = verify_pass.verify_and_merge("x.pdf", rows, auto=True, triggered_only=False)

    assert out[0]["flag"] == "x", "' X ' is stripped and lowered before it is compared"


def test_an_unflagged_absorbed_row_leaves_the_survivors_flag_alone(monkeypatch):
    """WHEN an absorbed row is unflagged, THE SYSTEM SHALL NOT clear a flag the survivor already has.

    The promotion is one-way. Copying the absorbed row's flag unconditionally would clear the
    survivor's, which is the same silent loss as not promoting at all.
    """
    rows = [_flagged(1, 10, flag="-"), _flagged(11, 20, flag="x"), _flagged(21, 30, flag="-")]
    monkeypatch.setattr(verify_pass, "_same_document", _refuting(21))

    out, _ = verify_pass.verify_and_merge("x.pdf", rows, auto=True, triggered_only=False)

    assert out[1]["flag"] == "x", "the survivor keeps its own flag"
    assert out[1]["end"] == 30, "and still absorbs the row"


def test_consecutive_refutations_all_collapse_into_one_survivor(monkeypatch):
    """WHEN adjacent boundaries are all refuted, THE SYSTEM SHALL collapse them into a single row.

    The survivor is re-read from the output as it grows, not indexed out of the input, which is what
    makes a run of refuted fragments chain onto one row instead of each absorbing only its immediate
    predecessor. Getting that wrong leaves the middle fragment as a row covering pages the survivor
    now also claims - overlapping rows rather than a tiling.
    """
    rows = [_flagged(1, 5), _flagged(6, 6), _flagged(7, 7), _flagged(8, 20, "9", "-")]
    monkeypatch.setattr(verify_pass, "_same_document", _refuting(6, 7))

    out, stats = verify_pass.verify_and_merge("x.pdf", rows, auto=True, triggered_only=False)

    assert [(r["start"], r["end"]) for r in out] == [(1, 7), (8, 20)]
    assert stats == {"suspects": 3, "merged_away": 2}
