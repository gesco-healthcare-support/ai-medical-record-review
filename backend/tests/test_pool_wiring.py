"""Pool-timeout wiring (pipeline forever-hang fix).

Each of the segmentation-side pools abandons a stalled drain per its own policy - verify keeps the
boundary splits, the window pool is terminal (lost coverage), categorize defaults the row to review
- so no as_completed() ever waits unbounded. (The summarize pool's pause-on-timeout lives in
test_jobs.py, which has the DB job scaffolding.)
"""

import time

import pytest

from app.config import get_settings
from app.errors import PipelineTimeoutError
from app.services import segment_engine, verify_pass
from app.services.taxonomy import DEFAULT_ID


def _tiny_pool_timeout(monkeypatch):
    """Shrink the size-aware pool budget to ~1s so a stalled worker trips it fast in a test."""
    settings = get_settings()
    monkeypatch.setattr(settings, "job_timeout", 1)
    monkeypatch.setattr(settings, "job_timeout_per_page", 0.0)
    monkeypatch.setattr(settings, "future_timeout_margin_seconds", 0)


def test_verify_timeout_keeps_splits(monkeypatch):
    monkeypatch.setattr(verify_pass, "_same_document", lambda *a, **k: time.sleep(1.0) or True)
    rows = [
        {"start": 1, "end": 2, "category": "1", "date": "2020-01-01", "flag": "-"},
        {"start": 3, "end": 3, "category": "1", "date": "2020-01-01", "flag": "-"},
        {"start": 4, "end": 5, "category": "2", "date": "2020-02-02", "flag": "-"},
    ]
    out, stats = verify_pass.verify_and_merge("x.pdf", rows, pool_timeout=0.3)
    # Nothing verified within the budget -> nothing refuted -> every split kept, no merge suggested.
    assert len(out) == 3
    assert not any(r.get("suggest_merge") for r in out)


def test_window_pool_timeout_is_terminal(monkeypatch):
    _tiny_pool_timeout(monkeypatch)
    monkeypatch.setattr(segment_engine, "get_genai_client", lambda: object())
    monkeypatch.setattr(segment_engine, "byte_budgeted_windows", lambda *a, **k: [(1, 5), (6, 10)])
    monkeypatch.setattr(segment_engine, "_window_rows", lambda *a, **k: time.sleep(1.5) or [])
    with pytest.raises(PipelineTimeoutError):
        segment_engine.run_segmentation("x.pdf", 10)


def test_categorize_pool_timeout_defaults_rows(monkeypatch):
    _tiny_pool_timeout(monkeypatch)
    monkeypatch.setattr(segment_engine, "get_genai_client", lambda: object())
    monkeypatch.setattr(segment_engine, "byte_budgeted_windows", lambda *a, **k: [(1, 10)])
    monkeypatch.setattr(segment_engine, "_window_rows", lambda *a, **k: [])  # windows yield no rows
    monkeypatch.setattr(segment_engine, "_categorize", lambda pdf, row: time.sleep(1.5))
    rows = segment_engine.run_segmentation("x.pdf", 10)
    # merge inserts a single coverage row; a stalled categorize defaults it to the catch-all + review.
    assert rows
    assert all(r["category"] == DEFAULT_ID and r["flag"] == "x" for r in rows)
