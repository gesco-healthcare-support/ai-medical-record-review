"""Integration tests for the diag/op + deposition extraction and page-range merge."""

from mrr_ai import state
from mrr_ai.blueprints import reports as rep


def test_get_diag_op_rep_builds_combined_pdf(client, make_pdf, tmp_path, home_tmp):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=5)
    csv = tmp_path / "pages.csv"
    csv.write_text("1,2,8,01/02/2020,-,-\n", encoding="utf-8")  # category 8 -> included
    state.txt_filepath = str(csv)
    state.main_filename = "summary"

    resp = client.post("/getDiagOpRep", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    assert resp.get_json()["summaryText"] == "success"
    combined = home_tmp / "MRRs" / "summary_diag_and_op_reports" / "summary_diag_and_op_reports.pdf"
    assert combined.exists()


def test_get_depo_rep_counts_depositions(client, make_pdf, tmp_path, home_tmp):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=5)
    csv = tmp_path / "pages.csv"
    csv.write_text("1,3,9,-,-,-\n", encoding="utf-8")  # category 9 -> deposition
    state.txt_filepath = str(csv)
    state.main_filename = "summary"

    resp = client.post("/getDepoRep", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    assert resp.get_json()["summaryText"] == "Total Depositions: 1\n"


def test_compute_page_ranges(client, make_pdf, tmp_path, home_tmp, monkeypatch):
    base = tmp_path / "uploads"
    folder = base / "case"
    folder.mkdir(parents=True)
    make_pdf(folder / "a.pdf", pages=2)
    make_pdf(folder / "b.pdf", pages=3)
    monkeypatch.setattr(rep, "UPLOAD_BASE_DIR", str(base))

    resp = client.post("/compute_page_ranges", json={"folder_name": "case"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["page_ranges"] == ["1-2", "3-5"]


def test_compute_page_ranges_missing_folder_name(client):
    resp = client.post("/compute_page_ranges", json={})
    assert resp.status_code == 400


def test_compute_page_ranges_nonexistent_folder(client, tmp_path, monkeypatch):
    monkeypatch.setattr(rep, "UPLOAD_BASE_DIR", str(tmp_path))
    resp = client.post("/compute_page_ranges", json={"folder_name": "missing"})
    assert resp.status_code == 400
