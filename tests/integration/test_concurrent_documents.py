"""T8: the concurrent-documents story through the REAL api + job queue.

Engines are stubbed (a barrier proves true overlap; no Gemini). The scenario mirrors
the user flow: start work on document A, immediately start document B, come back to A.
"""

import io
import threading
import time

_ROWS = [
    {
        "start": 1,
        "end": 5,
        "category": "1",
        "title": "DOC A",
        "date": "01/15/2024",
        "injury_date": "-",
        "flag": "-",
        "suggest_merge": False,
    },
    {
        "start": 6,
        "end": 10,
        "category": "3",
        "title": "DOC B",
        "date": "02/20/2024",
        "injury_date": "-",
        "flag": "x",
        "suggest_merge": False,
    },
]


def _upload(client, pdf_bytes, name):
    response = client.post(
        "/api/documents",
        data={"pdf": (io.BytesIO(pdf_bytes(10)), name)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.get_json()["id"]


def _wait_status(client, document_id, wanted, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/documents/{document_id}/status").get_json()
        if payload["status"] == wanted:
            return True
        time.sleep(0.02)
    return False


def test_two_documents_overlap_and_finish(client, pdf_bytes, monkeypatch):
    overlap = threading.Barrier(2, timeout=15)  # trips only if BOTH jobs run at once

    def fake_segmentation(pdf_path, total_pages, progress=None):
        overlap.wait()
        return [dict(row) for row in _ROWS]

    def fake_summarize(pdf_path, row, model=None, prompt=None):
        return {
            "summaryTitle": f"SUMMARY {row['start']}",
            "summaryDate": row["date"],
            "summaryText": "synthetic",
            "manualCheck": False,
        }

    monkeypatch.setattr("mrr_ai.services.segment_engine.run_segmentation", fake_segmentation)
    monkeypatch.setattr("mrr_ai.services.summarize_engine.summarize_row", fake_summarize)

    # Start A, then B immediately - the barrier only releases if they truly overlap.
    doc_a = _upload(client, pdf_bytes, "case-a.pdf")
    doc_b = _upload(client, pdf_bytes, "case-b.pdf")
    assert client.post(f"/api/documents/{doc_a}/segment/start").status_code == 200
    assert client.post(f"/api/documents/{doc_b}/segment/start").status_code == 200
    assert _wait_status(client, doc_a, "reviewing")
    assert _wait_status(client, doc_b, "reviewing")

    # "Switch back to A": correct its rows and summarize while B stays reviewable.
    edited = [dict(_ROWS[0], end=4), dict(_ROWS[1], start=5)]
    assert client.put(f"/api/documents/{doc_a}/rows", json={"rows": edited}).status_code == 200
    assert (
        client.post(f"/api/documents/{doc_a}/summarize/start", json={"rows": edited}).status_code
        == 200
    )
    assert _wait_status(client, doc_a, "done")

    # A has summaries; B's rows are intact and untouched by A's run.
    summaries = client.get(f"/api/documents/{doc_a}/summaries").get_json()
    assert [item["summaryTitle"] for item in summaries] == ["SUMMARY 1", "SUMMARY 5"]
    rows_b = client.get(f"/api/documents/{doc_b}").get_json()["rows"]
    assert [row["start"] for row in rows_b] == [1, 6]

    listing = client.get("/api/documents").get_json()
    statuses = {doc["id"]: doc["status"] for doc in listing}
    assert statuses[doc_a] == "done"
    assert statuses[doc_b] == "reviewing"
