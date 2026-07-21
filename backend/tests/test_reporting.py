"""P3a: MRR Word-document assembly (python-docx; no DB, no network)."""

import io

from app.services.reporting import DOCX_MIMETYPE, build_mrr_document


def test_build_mrr_document_saves():
    entries = [
        {"summaryDate": "01/02/2020", "summaryTitle": "Report A", "summaryText": "text A"},
        {"summaryDate": "03/04/2019", "summaryTitle": "Report B", "summaryText": "text B"},
    ]
    doc = build_mrr_document(
        entries,
        num_pages=42,
        patient_name="Synthetic Patient",
        patient_dob="-",
        qme_or_ame="QME",
        lawfirm="Example Law Firm",
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    assert buffer.tell() > 0
    assert DOCX_MIMETYPE.endswith("wordprocessingml.document")


def test_build_mrr_document_blank_qme_ame_does_not_crash():
    # A blank QME/AME field must not crash (an empty paragraph has no runs -> guarded with " ").
    doc = build_mrr_document(
        [], num_pages=1, patient_name="", patient_dob="", qme_or_ame="", lawfirm=""
    )
    buffer = io.BytesIO()
    doc.save(buffer)
    assert buffer.tell() > 0
