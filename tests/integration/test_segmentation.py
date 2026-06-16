"""Integration tests for the Gemini segmentation routes (Gemini fully mocked)."""

import json
from types import SimpleNamespace

from mrr_ai import state
from mrr_ai.blueprints import segmentation as seg
from mrr_ai.services import pdf as pdf_service
from mrr_ai.services.classification import Classification


def _patch_gemini(monkeypatch, fake_client):
    """Stub the upload/poll calls and the genai client used inside the route."""
    monkeypatch.setattr(
        seg,
        "upload_to_gemini",
        lambda path, mime_type=None: SimpleNamespace(name="files/n", display_name="d", uri="u"),
    )
    monkeypatch.setattr(seg, "wait_for_files_active", lambda files: None)
    monkeypatch.setattr(seg, "genai_client", fake_client)


def test_get_pages_small_pdf(client, make_pdf, tmp_path, monkeypatch, fake_genai):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)
    payload = json.dumps(
        [
            {
                "id": "Doc1",
                "s": 1,
                "e": 2,
                "t": "Operative Report",
                "d": "01/02/2020",
                "i": "-",
                "m": "-",
            }
        ]
    )
    _patch_gemini(monkeypatch, fake_genai(payload))

    resp = client.post("/getPages", json={})

    assert resp.status_code == 200
    assert resp.get_json()["pages"] == "1,2,8,01/02/2020,-,-"


def test_get_pages_non_integer_delimiter_defaults(
    client, make_pdf, tmp_path, monkeypatch, fake_genai
):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)
    payload = json.dumps([{"s": 1, "e": 2, "t": "Deposition", "d": "-", "i": "-", "m": "-"}])
    _patch_gemini(monkeypatch, fake_genai(payload))

    resp = client.post("/getPages", json={"pageDelimiter": "not-a-number"})

    assert resp.status_code == 200
    assert resp.get_json()["pages"] == "1,2,9,-,-,-"


def test_get_pages_large_pdf_batches_with_offset(
    client, make_pdf, tmp_path, monkeypatch, fake_genai
):
    monkeypatch.setattr(pdf_service, "UPLOAD_FOLDER", str(tmp_path / "chunks"))
    state.pdf_filepath = make_pdf(tmp_path / "big.pdf", pages=101)  # > 100 -> batch branch
    payload = json.dumps([{"s": 1, "e": 2, "t": "Deposition", "d": "-", "i": "-", "m": "-"}])
    _patch_gemini(monkeypatch, fake_genai(payload))

    resp = client.post("/getPages", json={"pageDelimiter": 100})

    assert resp.status_code == 200
    # Two chunks (100 + 1); the second chunk's pages are offset by the page delimiter.
    assert resp.get_json()["pages"].split("\n") == ["1,2,9,-,-,-", "101,102,9,-,-,-"]


def test_get_pages_handles_gemini_error(client, make_pdf, tmp_path, monkeypatch, fake_genai):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)
    _patch_gemini(monkeypatch, fake_genai(RuntimeError("gemini unavailable")))

    resp = client.post("/getPages", json={})

    assert resp.status_code == 200
    assert "gemini unavailable" in resp.get_json()["pages"]


def test_get_pages_low_confidence_escalates_and_flags(
    client, make_pdf, tmp_path, monkeypatch, fake_genai
):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)
    payload = json.dumps(
        [{"s": 1, "e": 2, "t": "Ambiguous Document", "d": "-", "i": "-", "m": "-"}]
    )
    _patch_gemini(monkeypatch, fake_genai(payload))

    ocr_calls = []
    monkeypatch.setattr(
        seg,
        "extract_text_from_selected_pages",
        lambda path, pages: ocr_calls.append(pages) or "first page text",
    )
    # Stays low-confidence even after escalation -> row gets flagged for review.
    monkeypatch.setattr(
        seg, "classify", lambda title, page_text=None: Classification("3", "low", "disagree", True)
    )

    resp = client.post("/getPages", json={})

    assert resp.status_code == 200
    assert resp.get_json()["pages"] == "1,2,3,-,-,x"
    assert ocr_calls == [[1]]  # escalated by OCR-ing the sub-document's first page


def test_get_pages_escalation_can_resolve_and_clear_flag(
    client, make_pdf, tmp_path, monkeypatch, fake_genai
):
    state.pdf_filepath = make_pdf(tmp_path / "small.pdf", pages=2)
    payload = json.dumps(
        [{"s": 1, "e": 2, "t": "Ambiguous Document", "d": "-", "i": "-", "m": "-"}]
    )
    _patch_gemini(monkeypatch, fake_genai(payload))
    monkeypatch.setattr(seg, "extract_text_from_selected_pages", lambda path, pages: "page text")

    calls = {"n": 0}

    def fake_classify(title, page_text=None):
        calls["n"] += 1
        if page_text is None:  # title alone is inconclusive
            return Classification("100", "low", "disagree", True)
        return Classification("8", "high", "llm+embedding", False)  # escalation resolves it

    monkeypatch.setattr(seg, "classify", fake_classify)

    resp = client.post("/getPages", json={})

    assert resp.status_code == 200
    assert resp.get_json()["pages"] == "1,2,8,-,-,-"
    assert calls["n"] == 2  # title classify, then escalation classify


def test_segment_pdf_route_writes_chunks(client, make_pdf, tmp_path, home_tmp):
    state.pdf_filepath = make_pdf(tmp_path / "rec.pdf", pages=3)

    resp = client.post("/segmentPDF")

    assert resp.status_code == 200
    assert "finalyzed" in resp.get_json()["pages"]
    assert (home_tmp / "MRRs" / "rec_segmented").is_dir()
