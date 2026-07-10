"""Integration tests for the review API (engine + OpenAI mocked; jobs run on real threads,
polled with a timeout so the tests exercise the same path the UI does)."""

import time

from mrr_ai import state
from mrr_ai.blueprints import review_api
from mrr_ai.services import jobs


def _poll(client, url, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(url).get_json()
        if snap.get("state") in ("done", "error"):
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job did not finish: {snap}")


def _rows():
    return [
        dict(
            start=1,
            end=2,
            category="8",
            title="Operative Report",
            date="01/02/2020",
            injury_date="-",
            flag="-",
        ),
        dict(start=3, end=5, category="9", title="Deposition", date="-", injury_date="-", flag="x"),
    ]


def _fresh_jobs():
    jobs.clear()


def test_segment_job_returns_rows(client, make_pdf, tmp_path, monkeypatch):
    _fresh_jobs()
    state.pdf_filepath = make_pdf(tmp_path / "case.pdf", pages=5)

    def fake_run(path, n, progress=None):
        progress("segmenting", 1, 1)
        return _rows()

    monkeypatch.setattr(review_api, "run_segmentation", fake_run)

    assert client.post("/api/segment/start").status_code == 200
    snap = _poll(client, "/api/segment/status")
    assert snap["state"] == "done"
    assert [r["start"] for r in snap["rows"]] == [1, 3]
    assert "8" in snap["categories"] and "100" in snap["categories"]


def test_segment_requires_upload(client):
    _fresh_jobs()
    state.pdf_filepath = None
    assert client.post("/api/segment/start").status_code == 400


def test_segment_engine_error_lands_in_status(client, make_pdf, tmp_path, monkeypatch):
    _fresh_jobs()
    state.pdf_filepath = make_pdf(tmp_path / "case.pdf", pages=2)

    def boom(path, n, progress=None):
        raise RuntimeError("vertex unavailable")

    monkeypatch.setattr(review_api, "run_segmentation", boom)
    client.post("/api/segment/start")
    snap = _poll(client, "/api/segment/status")
    assert snap["state"] == "error" and "vertex unavailable" in snap["error"]


def test_summarize_validates_rows(client, make_pdf, tmp_path):
    _fresh_jobs()
    state.pdf_filepath = make_pdf(tmp_path / "case.pdf", pages=5)

    bad_overlap = [dict(_rows()[0], end=4), dict(_rows()[1], start=2)]
    resp = client.post("/api/summarize/start", json={"rows": bad_overlap})
    assert resp.status_code == 400 and "overlaps" in resp.get_json()["error"]

    bad_pages = [dict(_rows()[0], end=99)]
    resp = client.post("/api/summarize/start", json={"rows": bad_pages})
    assert resp.status_code == 400

    bad_category = [dict(_rows()[0], category="42")]
    resp = client.post("/api/summarize/start", json={"rows": bad_category})
    assert resp.status_code == 400 and "category" in resp.get_json()["error"]


def test_summarize_job_returns_rowwise_summaries(client, make_pdf, tmp_path, monkeypatch):
    _fresh_jobs()
    state.pdf_filepath = make_pdf(tmp_path / "case.pdf", pages=5)
    state.all_data = []

    def fake_summarize_row(pdf_path, row, model=None, prompt=None):
        return {
            "summaryDate": row["date"],
            "summaryTitle": f"T{row['start']} (Pages {row['start']}-{row['end']})",
            "manualCheck": "",
            "summaryText": f"summary {row['start']}",
        }

    monkeypatch.setattr(review_api, "summarize_row", fake_summarize_row)

    resp = client.post("/api/summarize/start", json={"rows": _rows()})
    assert resp.status_code == 200
    snap = _poll(client, "/api/summarize/status")
    assert snap["state"] == "done"
    assert [s["summaryText"] for s in snap["summaries"]] == ["summary 1", "summary 3"]
    # the legacy Word export reads state.all_data - the job must have filled it
    assert len(state.all_data) == 2


def test_pdf_endpoint_serves_uploaded_file(client, make_pdf, tmp_path):
    state.pdf_filepath = make_pdf(tmp_path / "case.pdf", pages=1)
    resp = client.get("/api/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    resp.close()


def test_pdf_endpoint_404_without_upload(client):
    state.pdf_filepath = None
    assert client.get("/api/pdf").status_code == 404
