"""Integration tests for the segmentation routes (the engine itself is unit-tested;
here it is stubbed so the route contract - the 6-column CSV lines - is what's under test)."""

from mrr_ai import state
from mrr_ai.blueprints import segmentation as seg


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


def test_get_pages_returns_csv_lines(client, make_pdf, tmp_path, monkeypatch):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=5)
    monkeypatch.setattr(seg, "run_segmentation", lambda path, n, progress=None: _rows())

    resp = client.post("/getPages", json={})

    assert resp.status_code == 200
    assert resp.get_json()["pages"].split("\n") == [
        "1,2,8,01/02/2020,-,-",
        "3,5,9,-,-,x",
    ]


def test_get_pages_passes_real_page_count(client, make_pdf, tmp_path, monkeypatch):
    state.pdf_filepath = make_pdf(tmp_path / "seven.pdf", pages=7)
    seen = {}

    def fake_run(path, n, progress=None):
        seen["n"] = n
        return _rows()

    monkeypatch.setattr(seg, "run_segmentation", fake_run)
    client.post("/getPages", json={"pageDelimiter": 100})
    assert seen["n"] == 7


def test_get_pages_reports_engine_error_in_contract(client, make_pdf, tmp_path, monkeypatch):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)

    def boom(path, n, progress=None):
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(seg, "run_segmentation", boom)
    resp = client.post("/getPages", json={})
    assert resp.status_code == 200
    assert "gemini unavailable" in resp.get_json()["pages"]


def test_segment_pdf_route_writes_chunks(client, make_pdf, tmp_path, home_tmp):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=3)

    resp = client.post("/segmentPDF")

    assert resp.status_code == 200
    assert "finalyzed" in resp.get_json()["pages"]
    assert (home_tmp / "MRRs" / "rec_segmented").is_dir()
