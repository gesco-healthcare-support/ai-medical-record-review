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
    monkeypatch.setattr(
        segment_engine, "_categorize", lambda pdf, row, page_text_fn=None: time.sleep(1.5)
    )
    rows = segment_engine.run_segmentation("x.pdf", 10)
    # merge inserts a single coverage row; a stalled categorize defaults it to the catch-all + review.
    assert rows
    assert all(r["category"] == DEFAULT_ID and r["flag"] == "x" for r in rows)


def test_compose_passes_through_the_settings_it_claims_to_control():
    """A key in .env reaches a container ONLY if docker-compose.yml names it: compose uses .env for
    ${VAR} substitution inside the file, not as an env_file. On 2026-07-31 VERTEX_MAX_RPM=20 and
    SEGMENT_WINDOW_WORKERS=1 were added to the server .env, the containers were recreated, and both
    still reported the code defaults 60 and 3 - the pacing fix was silently inert.

    It then happened AGAIN the same day: #66 shipped the three DUPE_* thresholds and advertised them in
    its own body as the env-tunable way to loosen the new duplicate gate, past the warning comment
    already sitting in the compose file. #67 added them. Twice is a pattern, so the list below is the
    check that has to grow whenever config.py gains a setting ops will tune.

    It happened a THIRD time on 2026-08-12: SUMMARY_MODEL, GENAI_HTTP_TIMEOUT_MS,
    SEGMENT_THINKING_BUDGET and WINDOW_BUDGET_MB were all advertised and all inert, and with Vertex
    refusing gemini-2.5-pro outright, repointing the summary model needed a code change and a deploy
    instead of one line. The hardcoded list below had not been extended either time, which is the
    point: a list someone must remember to grow cannot guard the thing being forgotten.

    So this is now DERIVED from .env.example rather than hardcoded. .env.example is the contract with
    ops - anything advertised there is a promise that setting it does something - so every key in it
    must be named in compose, and the test grows by itself."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    advertised = {
        m.group(1)
        for m in re.finditer(
            r"^([A-Z][A-Z0-9_]*)=", (root / ".env.example").read_text(encoding="utf-8"), re.M
        )
    }

    # TESSERACT_CMD must stay OUT: it is a Windows HOST path, and injecting it would point the
    # containers' pytesseract at a nonexistent binary and break OCR everywhere. It is the only key
    # advertised to ops that must NOT reach a container; anything else added here needs a reason.
    exempt = {"TESSERACT_CMD"}

    # A derived check fails OPEN if the parse breaks - an empty set would pass every assertion below
    # and report nothing. Floor it so a regex regression is a failure, not silent green.
    assert len(advertised) >= 20, f"only parsed {len(advertised)} keys from .env.example"

    for key in sorted(advertised - exempt):
        assert f"{key}: ${{{key}" in compose, (
            f"{key} is advertised in .env.example but docker-compose.yml does not name it, "
            "so setting it does nothing inside a container"
        )
    assert "TESSERACT_CMD: ${TESSERACT_CMD" not in compose
