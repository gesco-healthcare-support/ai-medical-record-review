"""Unit tests for the boundary verification merge pass (oracle mocked)."""

from mrr_ai.services import verify_pass
from mrr_ai.services.verify_pass import suspect_starts, verify_and_merge


def _row(start, end, category="1", date="01/01/2024", flag="-"):
    return dict(
        start=start, end=end, category=category, date=date, injury_date="-", flag=flag, title="t"
    )


def test_suspects_same_cat_date_and_short_rows():
    rows = [
        _row(1, 5),
        _row(6, 10, category="1", date="01/01/2024"),  # same cat+date as row 1 -> suspect
        _row(11, 12, category="3", date="02/02/2024"),  # 2 pages -> suspect
        _row(13, 20, category="9", date="-"),  # long, different -> clean
    ]
    assert suspect_starts(rows) == [6, 11]


def test_refuted_boundary_merges_and_keeps_flags(monkeypatch):
    rows = [_row(1, 5), _row(6, 7, flag="x"), _row(8, 20, category="9", date="-")]
    monkeypatch.setattr(verify_pass, "_adjacent_says_new", lambda pdf, page: page != 6)
    merged, stats = verify_and_merge("case.pdf", rows)
    assert [(r["start"], r["end"]) for r in merged] == [(1, 7), (8, 20)]
    assert merged[0]["flag"] == "x"  # absorbed row's review flag survives
    assert stats == dict(suspects=1, merged_away=1)


def test_confirmed_boundary_survives(monkeypatch):
    rows = [_row(1, 5), _row(6, 7)]
    monkeypatch.setattr(verify_pass, "_adjacent_says_new", lambda pdf, page: True)
    merged, stats = verify_and_merge("case.pdf", rows)
    assert len(merged) == 2 and stats["merged_away"] == 0


def test_chained_fragments_collapse(monkeypatch):
    rows = [_row(1, 5), _row(6, 6), _row(7, 7), _row(8, 20, category="9", date="-")]
    monkeypatch.setattr(verify_pass, "_adjacent_says_new", lambda pdf, page: page not in (6, 7))
    merged, _stats = verify_and_merge("case.pdf", rows)
    assert [(r["start"], r["end"]) for r in merged] == [(1, 7), (8, 20)]


def test_first_row_never_merges(monkeypatch):
    rows = [_row(1, 1), _row(2, 20, category="9", date="-")]
    monkeypatch.setattr(verify_pass, "_adjacent_says_new", lambda pdf, page: False)
    merged, _stats = verify_and_merge("case.pdf", rows)
    assert merged[0]["start"] == 1  # a leading fragment stays a row (nothing before it)


def test_oracle_failure_keeps_boundary(monkeypatch):
    # The recall-safe contract: an unverifiable suspect KEEPS its boundary (a wrong
    # merge hides a document; a wrong split is a one-click human fix).
    monkeypatch.setattr(verify_pass, "_page_png", lambda pdf, page, dpi=120: b"png")

    def boom(*args, **kwargs):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(verify_pass, "generate_with_retry", boom)
    assert verify_pass._adjacent_says_new("case.pdf", 6) is True
