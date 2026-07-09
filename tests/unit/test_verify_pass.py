"""Unit tests for the boundary verification pass (rich same-document oracle mocked)."""

from mrr_ai.services import verify_pass
from mrr_ai.services.verify_pass import suspect_indices, verify_and_merge


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
    assert suspect_indices(rows) == [1, 2]


def _mock_same(monkeypatch, same_starts):
    monkeypatch.setattr(
        verify_pass, "_same_document", lambda pdf, prev, row: row["start"] in same_starts
    )


def test_same_document_verdict_becomes_suggestion_by_default(monkeypatch):
    # Default mode NEVER drops a row: at scale the oracle wrongly merged ~3% of real
    # boundaries, and an automatic merge hides a document (the worst error class).
    rows = [_row(1, 5), _row(6, 7, flag="x"), _row(8, 20, category="9", date="-")]
    _mock_same(monkeypatch, {6})
    out, stats = verify_and_merge("case.pdf", rows)
    assert [(r["start"], r["end"]) for r in out] == [(1, 5), (6, 7), (8, 20)]
    assert out[1].get("suggest_merge") is True and "suggest_merge" not in out[0]
    assert stats == {"suspects": 1, "suggested": 1}


def test_auto_mode_merges_and_keeps_flags(monkeypatch):
    rows = [_row(1, 5), _row(6, 7, flag="x"), _row(8, 20, category="9", date="-")]
    _mock_same(monkeypatch, {6})
    merged, stats = verify_and_merge("case.pdf", rows, auto=True)
    assert [(r["start"], r["end"]) for r in merged] == [(1, 7), (8, 20)]
    assert merged[0]["flag"] == "x"  # absorbed row's review flag survives
    assert stats == {"suspects": 1, "merged_away": 1}


def test_separate_document_verdict_survives_unmarked(monkeypatch):
    rows = [_row(1, 5), _row(6, 7)]
    _mock_same(monkeypatch, set())
    out, stats = verify_and_merge("case.pdf", rows)
    assert len(out) == 2 and stats["suggested"] == 0
    assert all("suggest_merge" not in r for r in out)


def test_auto_mode_chained_fragments_collapse(monkeypatch):
    rows = [_row(1, 5), _row(6, 6), _row(7, 7), _row(8, 20, category="9", date="-")]
    _mock_same(monkeypatch, {6, 7})
    merged, _stats = verify_and_merge("case.pdf", rows, auto=True)
    assert [(r["start"], r["end"]) for r in merged] == [(1, 7), (8, 20)]


def test_first_row_never_merges_or_suggests(monkeypatch):
    rows = [_row(1, 1), _row(2, 20, category="9", date="-")]
    _mock_same(monkeypatch, {1, 2})
    out, _stats = verify_and_merge("case.pdf", rows)
    assert out[0]["start"] == 1 and "suggest_merge" not in out[0]


def test_oracle_failure_keeps_boundary(monkeypatch):
    # The recall-safe contract: an unverifiable suspect KEEPS its boundary (a wrong
    # merge hides a document; a wrong split is a one-click human fix).
    monkeypatch.setattr(verify_pass, "_page_png", lambda pdf, page, dpi=120: b"png")

    def boom(*args, **kwargs):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(verify_pass, "generate_with_retry", boom)
    assert verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7)) is False
