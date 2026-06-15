"""Integration tests for the upload + CSV-check routes."""

import io

from mrr_ai import state


def _multipart(field, filename, data):
    return {field: (io.BytesIO(data), filename)}


def test_upload_pdf_sets_state(client, pdf_bytes):
    resp = client.post(
        "/upload",
        data=_multipart("pdf", "case.pdf", pdf_bytes(3)),
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["filepath"] == "case.pdf"
    assert body["num_pages"] == 3
    assert state.num_pages == 3
    assert state.main_filename == "case"


def test_upload_and_check_csv_all_valid(client):
    content = b"1,2,8,01/02/2020,-,-\n3,4,9,02/03/2020,-,-\n"
    resp = client.post(
        "/uploadAndCheckCSV",
        data=_multipart("txt", "pages.txt", content),
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert "All rows are valid" in resp.get_json()["errors_and_duplicates"]


def test_upload_and_check_csv_flags_bad_date(client):
    content = b"1,2,8,99/99/2020,-,-\n"
    resp = client.post(
        "/uploadAndCheckCSV",
        data=_multipart("txt", "pages.txt", content),
        content_type="multipart/form-data",
    )

    assert "errors or missing columns" in resp.get_json()["errors_and_duplicates"]


def test_upload_and_check_csv_flags_duplicates(client):
    content = b"1,2,8,01/02/2020,-,-\n5,6,8,01/02/2020,-,-\n"
    resp = client.post(
        "/uploadAndCheckCSV",
        data=_multipart("txt", "pages.txt", content),
        content_type="multipart/form-data",
    )

    assert "Duplicate rows detected" in resp.get_json()["errors_and_duplicates"]


def test_upload_pages_counts_lines(client):
    resp = client.post(
        "/uploadPages",
        data=_multipart("txt", "p.txt", b"a\nb\nc\n"),
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    assert resp.get_json()["line_count"] == 3
