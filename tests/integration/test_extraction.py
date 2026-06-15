"""Integration tests for the patient-name/DOB and law-firm extraction routes."""

from mrr_ai import state
from mrr_ai.blueprints import extraction as ext


def test_get_patient_name_and_dob(client, make_pdf, tmp_path, monkeypatch, fake_openai):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=20)
    monkeypatch.setattr(ext, "extract_text_from_selected_pages", lambda path, pages: "text")
    monkeypatch.setattr(
        ext, "client", fake_openai('{"name": "John Synthetic", "dob": "01/02/1990"}')
    )

    resp = client.post("/getpatientnameanddob")

    assert resp.status_code == 200
    assert resp.get_json() == {"name": "John Synthetic", "dob": "01/02/1990"}


def test_get_lawfirm(client, make_pdf, tmp_path, monkeypatch, fake_openai):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=10)
    monkeypatch.setattr(ext, "extract_text_from_selected_pages", lambda path, pages: "text")
    monkeypatch.setattr(ext, "client", fake_openai('{"lawfirm": "Synthetic LLP"}'))

    resp = client.post("/getlawfirm")

    assert resp.status_code == 200
    assert resp.get_json()["lawfirm"] == "Synthetic LLP"
