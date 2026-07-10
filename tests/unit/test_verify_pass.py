"""Unit tests for the boundary verification pass (rich same-document oracle mocked)."""

from mrr_ai.services import verify_pass
from mrr_ai.services.verify_pass import suspect_indices, verify_and_merge


def _row(start, end, category="1", date="01/01/2024", flag="-"):
    return dict(
        start=start, end=end, category=category, date=date, injury_date="-", flag=flag, title="t"
    )


class _Response:
    def __init__(self, text):
        self.text = text


def _wire_oracle(monkeypatch, answer="NO", ocr_text="", captured=None):
    """Stub the oracle's evidence pipeline: fake page images, fake OCR, canned verdict."""
    monkeypatch.setattr(verify_pass, "_page_image", lambda pdf, page, dpi=120: f"img-{page}")
    monkeypatch.setattr(verify_pass, "_png_bytes", lambda image: b"png")
    monkeypatch.setattr(verify_pass, "extract_text_from_image", lambda image: ocr_text)

    def fake_generate(client, model, contents, config):
        if captured is not None:
            captured.append(contents)
        return _Response(answer)

    monkeypatch.setattr(verify_pass, "generate_with_retry", fake_generate)


# --- suspect selection (wide net + cap) ------------------------------------------------


def test_wide_net_flags_every_adjacent_boundary():
    # Confident over-splits carry no computable signature (measured 2026-07-09), so
    # below the cap EVERY adjacent pair gets a verify call - not just the old
    # short-fragment / same-category+date triggers.
    rows = [
        _row(1, 5),
        _row(6, 10),  # same cat+date as row 0 (old trigger)
        _row(11, 20, category="3", date="02/02/2024"),  # long + different (old: clean)
        _row(21, 40, category="9", date="-"),  # long + different (old: clean)
    ]
    assert suspect_indices(rows) == [1, 2, 3]


def test_cap_keeps_measured_triggers_first():
    rows = [
        _row(1, 10, category="1", date="01/01/2024"),
        _row(11, 20, category="2", date="02/02/2024"),  # i=1 plain
        _row(21, 30, category="3", date="03/03/2024"),  # i=2 plain
        _row(31, 32, category="4", date="04/04/2024"),  # i=3 short fragment (trigger)
        _row(33, 42, category="5", date="05/05/2024"),  # i=4 plain
        _row(43, 52, category="5", date="05/05/2024"),  # i=5 same cat+date (trigger)
        _row(53, 62, category="6", date="06/06/2024"),  # i=6 plain
        _row(63, 72, category="7", date="07/07/2024"),  # i=7 plain
        _row(73, 82, category="8", date="08/08/2024"),  # i=8 plain
    ]
    out = suspect_indices(rows, cap=4)
    assert len(out) == 4
    assert {3, 5} <= set(out)  # both measured triggers survive the cap
    assert out == [1, 2, 3, 5]  # remaining slots fill in page order, ascending


def test_default_cap_covers_experiment_scale_cases():
    # The labeled cases have 51-67 docs; the default cap (200) must leave them fully
    # checked so the measured 5/17/14 suggest-mode catches are reproduced unchanged.
    rows = [
        _row(i * 10 + 1, i * 10 + 10, category=str(i % 9), date=f"{(i % 28) + 1:02d}/01/2024")
        for i in range(66)
    ]
    assert suspect_indices(rows) == list(range(1, 66))


# --- verify_and_merge (suggest by default, recall-safe) --------------------------------


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
    assert stats == {"suspects": 2, "suggested": 1}  # wide net: both boundaries checked


def test_auto_mode_merges_and_keeps_flags(monkeypatch):
    rows = [_row(1, 5), _row(6, 7, flag="x"), _row(8, 20, category="9", date="-")]
    _mock_same(monkeypatch, {6})
    merged, stats = verify_and_merge("case.pdf", rows, auto=True)
    assert [(r["start"], r["end"]) for r in merged] == [(1, 7), (8, 20)]
    assert merged[0]["flag"] == "x"  # absorbed row's review flag survives
    assert stats == {"suspects": 2, "merged_away": 1}


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


# --- the text-aware oracle -------------------------------------------------------------


def test_text_evidence_joins_the_prompt(monkeypatch):
    captured = []
    _wire_oracle(monkeypatch, answer="YES", ocr_text="continued... Page 3 of 5", captured=captured)
    assert verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7)) is True
    prompt = captured[0][0]
    assert "Page 3 of 5" in prompt  # boundary OCR text reached the model


def test_text_off_switch_restores_image_only_oracle(monkeypatch):
    captured = []
    _wire_oracle(monkeypatch, ocr_text="SHOULD-NOT-APPEAR", captured=captured)
    monkeypatch.setattr(verify_pass, "VERIFY_USE_TEXT", False)
    verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7))
    assert "SHOULD-NOT-APPEAR" not in captured[0][0]


def test_ocr_failure_degrades_to_image_only(monkeypatch):
    # Text is enrichment, not a gate: a broken Tesseract must not veto the check.
    captured = []
    _wire_oracle(monkeypatch, captured=captured)

    def boom(image):
        raise RuntimeError("tesseract missing")

    monkeypatch.setattr(verify_pass, "extract_text_from_image", boom)
    assert verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7)) is False
    assert len(captured) == 1  # the model was still consulted, image-only


def test_oracle_failure_keeps_boundary(monkeypatch):
    # The recall-safe contract: an unverifiable suspect KEEPS its boundary (a wrong
    # merge hides a document; a wrong split is a one-click human fix).
    _wire_oracle(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(verify_pass, "generate_with_retry", boom)
    assert verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7)) is False


def test_rasterize_failure_keeps_boundary(monkeypatch):
    # Evidence gathering itself failing (bad page, Poppler error) must also resolve to
    # "keep", not crash the whole verify pass mid-document.
    def boom(pdf, page, dpi=120):
        raise RuntimeError("poppler failed")

    monkeypatch.setattr(verify_pass, "_page_image", boom)
    assert verify_pass._same_document("case.pdf", _row(1, 5), _row(6, 7)) is False
