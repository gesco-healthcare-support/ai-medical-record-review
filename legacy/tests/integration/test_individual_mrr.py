"""Integration tests for the individual-MRR folder creation + multi-file upload."""

import io

from mrr_ai import state
from mrr_ai.blueprints import individual_mrr as indiv


def test_create_patient_folder(client, tmp_path, monkeypatch):
    monkeypatch.setattr(indiv, "UPLOAD_BASE_DIR", str(tmp_path))

    resp = client.post(
        "/create_patient_folder_indiv_mrr",
        json={"folder_name": "case", "patient_name": "Pat Synthetic"},
    )

    assert resp.status_code == 200
    assert (tmp_path / "case").is_dir()
    assert state.patientNameGlobal == "Pat Synthetic"


def test_create_patient_folder_missing_name_returns_400(client, tmp_path, monkeypatch):
    monkeypatch.setattr(indiv, "UPLOAD_BASE_DIR", str(tmp_path))
    resp = client.post("/create_patient_folder_indiv_mrr", json={"folder_name": ""})
    assert resp.status_code == 400


def test_upload_files_saves_to_folder(client, tmp_path, monkeypatch, pdf_bytes):
    monkeypatch.setattr(indiv, "UPLOAD_BASE_DIR", str(tmp_path))
    (tmp_path / "case").mkdir()
    data = {
        "folder_name": "case",
        "pdfs": [
            (io.BytesIO(pdf_bytes(1)), "a.pdf"),
            (io.BytesIO(pdf_bytes(1)), "b.pdf"),
        ],
    }

    resp = client.post("/upload_files", data=data, content_type="multipart/form-data")

    assert resp.status_code == 200
    assert set(resp.get_json()["saved_files"]) == {"a.pdf", "b.pdf"}


def test_upload_files_missing_folder_name_returns_400(client):
    resp = client.post("/upload_files", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_files_nonexistent_folder_returns_400(client, tmp_path, monkeypatch):
    monkeypatch.setattr(indiv, "UPLOAD_BASE_DIR", str(tmp_path))
    resp = client.post(
        "/upload_files",
        data={"folder_name": "missing"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
