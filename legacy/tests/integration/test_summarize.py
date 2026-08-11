"""Integration tests for the OpenAI summarization routes (OpenAI + OCR mocked)."""

from mrr_ai import state
from mrr_ai.blueprints import summarize as summ


def test_summarize_happy_path(client, make_pdf, tmp_path, monkeypatch, fake_openai):
    monkeypatch.chdir(tmp_path)  # all_data_temp.txt side-effect lands in the temp dir
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=3)
    csv = tmp_path / "pages.txt"
    csv.write_text("1,2,8,01/02/2020,-,-\n", encoding="utf-8")
    state.txt_filepath = str(csv)
    monkeypatch.setattr(summ, "extract_text_from_selected_pages", lambda path, pages: "extracted")
    monkeypatch.setattr(summ, "client", fake_openai("MOCK OUTPUT"))

    resp = client.post("/summarize", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    big_text = resp.get_json()["big_text"]
    assert "01/02/2020" in big_text
    assert "MOCK OUTPUT" in big_text
    assert len(state.all_data) == 1


def test_summarize_diagnostic_and_manual_flags(
    client, make_pdf, tmp_path, monkeypatch, fake_openai
):
    monkeypatch.chdir(tmp_path)
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=3)
    csv = tmp_path / "pages.txt"
    # Category 3 (diagnostic), a real injury date, and the manual-check flag "x".
    csv.write_text("1,2,3,01/02/2020,05/06/2019,x\n", encoding="utf-8")
    state.txt_filepath = str(csv)
    monkeypatch.setattr(summ, "extract_text_from_selected_pages", lambda path, pages: "extracted")
    monkeypatch.setattr(summ, "client", fake_openai("MOCK"))

    resp = client.post("/summarize", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    big_text = resp.get_json()["big_text"]
    assert "[Diagnostic Study]" in big_text
    assert "[ManualCheck]" in big_text
    assert "**DOI**:05/06/2019" in big_text


def test_summarize_missing_csv_returns_error(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.txt_filepath = str(tmp_path / "nope.txt")

    resp = client.post("/summarize", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    assert "page-range file not found" in resp.get_json()["big_text"]


def test_summarize_skips_malformed_lines(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    csv = tmp_path / "pages.txt"
    csv.write_text("1,2,8\nbad line\n", encoding="utf-8")  # wrong column counts -> skipped
    state.txt_filepath = str(csv)

    resp = client.post("/summarize", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    assert resp.get_json()["big_text"] == ""
    assert state.all_data == []


def test_summarize_indiv_record(client, tmp_path, monkeypatch, fake_openai):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "patient"
    folder.mkdir()
    state.indiv_mrr_folder_path = str(folder)
    monkeypatch.setattr(summ, "extract_text_from_all_pages", lambda path: "extracted")
    monkeypatch.setattr(summ, "client", fake_openai("INDIV OUTPUT"))
    records = [
        {
            "filename": "doc.pdf",
            "category": "8",
            "encounter_date": "01/02/2020",
            "injury_date": "-",
            "manual_review": "-",
            "pages": "1-2",
        }
    ]

    resp = client.post(
        "/summarize_indiv_record", json={"folder_name": "patient", "records": records}
    )

    assert resp.status_code == 200
    assert resp.data == b"S"
    assert len(state.all_data) == 1


def test_summarize_indiv_record_no_records_returns_400(client):
    resp = client.post("/summarize_indiv_record", json={"records": []})
    assert resp.status_code == 400
