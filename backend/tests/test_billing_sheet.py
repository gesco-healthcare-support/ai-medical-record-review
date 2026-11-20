"""The office's billing sheet: services/billing_sheet.py, the billing fields typed at upload
(POST /api/documents/combine), the doctors list (GET /api/documents/combine/doctors) and the sheet
itself (GET/PUT /api/documents/{id}/billing, GET /api/documents/{id}/billing-sheet).

Synthetic data only: blank PDF pages, made-up page text for the deposition counter, made-up names.
Uploads go to a per-test tmp dir; no model, no OCR.
"""

from datetime import date

import pymupdf
import pytest
from rq import Queue
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, PageText
from app.services import billing_sheet, received_files
from app.services.reporting import DOCTOR_FONTS, DOCTOR_NAMES, DOCTORS
from app.worker.queues import OCR_QUEUE, get_redis
from tests.conftest import lanes
from tests.test_received_files import _FIELDS, _combine, _document, _login, _user


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture(autouse=True)
def _no_reading(monkeypatch):
    """No OCR and no model in a test: a staged file's first page reads as nothing."""
    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", lambda *a, **k: "")

    def off(stage):
        raise ConnectionError("no model in tests")

    monkeypatch.setattr(received_files, "provider_for_stage", off)


@pytest.fixture(autouse=True)
def _empty_queues():
    queue = Queue(OCR_QUEUE, connection=get_redis())
    queue.empty()
    yield
    queue.empty()
    lanes("segment").empty()


def _testimony(lines: int = 6) -> str:
    """A transcript page of questions and answers, as OCR reads one: numbered lines."""
    out = []
    for n in range(1, lines + 1):
        out.append(f"{n} {'Q.' if n % 2 else 'A.'} Synthetic line number {n}.")
    return "\n".join(out)


_PROSE = "A synthetic report page.\nIt has ordinary lines.\nNone of them is a question."


# --- services/billing_sheet.py: what is counted --------------------------------------------------


def test_a_record_from_one_pdf_counts_every_page():
    assert billing_sheet.counted_pages(None, 4) == [1, 2, 3, 4]
    assert billing_sheet.counted_pages([], 2) == [1, 2]


def test_the_internal_email_and_the_envelope_are_not_counted():
    """DEMONSTRATES the office's answer: everything is counted but the email thread (a guide for
    the reviewer, not part of the records) and the envelope."""
    parts = [
        {"kind": "advocacy_letter", "start": 1, "end": 2},
        {"kind": "records", "start": 3, "end": 5},
        {"kind": "email", "start": 6, "end": 7},
        {"kind": "envelope", "start": 8, "end": 8},
    ]

    assert billing_sheet.counted_pages(parts, 8) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("text", "testimony"),
    [
        (_testimony(6), True),
        (_testimony(4), True),
        (_testimony(3), False),
        ("Q. One question.\nA. One answer.\nQ. Another.\nA. Yes.", True),
        (_PROSE, False),
        ("", False),
    ],
)
def test_a_page_of_testimony_has_four_question_or_answer_lines(text, testimony):
    assert billing_sheet.is_testimony(text) is testimony


def _texts(spec: str) -> dict[int, str]:
    """Page texts from a string: ``Q`` a page of testimony, ``.`` a page of prose, page 1 first."""
    return {i: (_testimony() if c == "Q" else _PROSE) for i, c in enumerate(spec, start=1)}


def test_testimony_runs_from_the_first_question_to_the_last():
    """DEMONSTRATES: the cover, appearances and index before and the certificate after are left
    out; colloquy pages inside the transcript are counted."""
    texts = _texts("....QQQ.QQQ...")

    assert billing_sheet.testimony_runs(texts, texts) == [(5, 11)]


def test_two_transcripts_apart_by_more_than_the_gap_are_two_runs():
    gap = "." * (billing_sheet._RUN_GAP + 1)
    texts = _texts("QQQQQ" + gap + "QQQQQ")

    assert billing_sheet.testimony_runs(texts, texts) == [(1, 5), (13, 17)]


def test_a_gap_of_the_allowed_size_stays_one_transcript():
    texts = _texts("QQQ" + "." * billing_sheet._RUN_GAP + "QQ")

    assert billing_sheet.testimony_runs(texts, texts) == [(1, 11)]


def test_a_few_quoted_questions_are_not_a_transcript():
    """GUARD: a report quoting a deposition (fewer than five pages of questions) is not counted."""
    texts = _texts("..QQQQ..")

    assert billing_sheet.testimony_runs(texts, texts) == []


def test_the_office_count_and_the_deposition_pages():
    parts = [
        {"kind": "advocacy_letter", "start": 1, "end": 2},
        {"kind": "records", "start": 3, "end": 14},
        {"kind": "email", "start": 15, "end": 15},
    ]
    texts = _texts("....QQQQQQ.....")

    counts = billing_sheet.count_pages(parts, 15, texts, lambda first, last: False)

    assert counts == billing_sheet.Counts(office=14, depo=6)


def test_a_condensed_transcript_counts_four_pages_a_sheet_in_both_counts():
    """DEMONSTRATES the office's rule: a condensed sheet prints four transcript pages and counts
    four, in the office count as well as the deposition pages."""
    texts = _texts("..QQQQQ..")
    asked = []

    def condensed(first, last):
        asked.append((first, last))
        return True

    counts = billing_sheet.count_pages(None, 9, texts, condensed)

    assert asked == [(3, 7)]
    assert counts == billing_sheet.Counts(office=9 + 5 * 3, depo=20)


def test_testimony_inside_the_email_is_not_counted():
    parts = [
        {"kind": "records", "start": 1, "end": 3},
        {"kind": "email", "start": 4, "end": 9},
    ]
    texts = _texts("...QQQQQQ")

    counts = billing_sheet.count_pages(parts, 9, texts, lambda first, last: False)

    assert counts == billing_sheet.Counts(office=3, depo=0)


def test_unread_pages_are_counted():
    assert billing_sheet.unread_pages([1, 2, 3], {1: "", 3: "x"}) == 1


# --- services/billing_sheet.py: what is printed ----------------------------------------------------


def _doc(**fields) -> Document:
    base = {
        "id": "00000000-0000-0000-0000-000000000000",
        "user_id": 1,
        "original_filename": "x.pdf",
        "stored_path": "x.pdf",
        "patient_first_name": "Pat",
        "patient_last_name": "Sample",
        "patient_id": "A1D5",
        "doctor": DOCTORS[0],
        "visit_type": "QME",
        "appointment_date": date(2026, 12, 3),
        "review_source": "edata",
        "interpreter": True,
        "records_from": "insurance",
        "received_on": date(2026, 11, 19),
    }
    return Document(**{**base, **fields})


def test_the_sheet_carries_the_typed_fields_and_the_counts():
    sheet = billing_sheet.sheet_for(_doc(declared_pages=40), billing_sheet.Counts(42, 7))

    assert sheet.dos == "12/03/2026"
    assert sheet.patient_name == "Sample, Pat"
    assert sheet.date_received == "11/19/2026"
    assert (sheet.declared_pages, sheet.office_count, sheet.depo_pages) == (40, 42, 7)


def test_a_count_typed_on_the_sheet_wins_over_the_worked_out_one():
    sheet = billing_sheet.sheet_for(
        _doc(office_count=50, depo_pages=0), billing_sheet.Counts(42, 7)
    )

    assert (sheet.office_count, sheet.depo_pages) == (50, 0)


def test_while_the_pages_are_read_the_counts_are_blank():
    sheet = billing_sheet.sheet_for(_doc(), None)

    assert (sheet.office_count, sheet.depo_pages) == (None, None)


def test_a_supplemental_report_has_supp_for_its_date_of_service():
    """DEMONSTRATES the office's answer: a supplemental report has no appointment; DOS says SUPP."""
    sheet = billing_sheet.sheet_for(_doc(visit_type="SUPP", appointment_date=None), None)

    assert sheet.dos == "SUPP"
    assert sheet.visit_type == "SUPP"


def _rendered_text(sheet) -> tuple[int, str]:
    with pymupdf.open(stream=billing_sheet.render(sheet), filetype="pdf") as pdf:
        return pdf.page_count, pdf[0].get_text()


def test_the_sheet_is_one_page_with_its_fields_written_on_it():
    sheet = billing_sheet.sheet_for(
        _doc(claim_numbers="ADJ0000001", declared_pages=40, discrepancy_reason="Two missing"),
        billing_sheet.Counts(42, 7),
    )

    pages, text = _rendered_text(sheet)

    assert pages == 1
    for written in (
        DOCTOR_NAMES[DOCTORS[0]],
        "12/03/2026",
        "Sample, Pat",
        "A1D5",
        "ADJ0000001",
        "QME",
        "40",
        "42",
        "7",
        "None",
        "Two missing",
        "11/19/2026",
        "E-DATA",
        "IN-HOUSE",
        "LEONARDO",
        "Billing Code:",
    ):
        assert written in text, written


def test_every_doctor_on_the_list_has_the_name_the_sheet_prints():
    # GUARD: one list, two halves - a doctor added for the header's font must not print a blank
    # (or a surname) at the top of the billing sheet, nor a name be kept for a doctor removed.
    assert set(DOCTOR_NAMES) == set(DOCTOR_FONTS)
    for surname, name in DOCTOR_NAMES.items():
        assert surname.upper() in name, surname
        assert name == name.upper(), surname


@pytest.mark.parametrize("surname", DOCTORS)
def test_the_sheet_is_headed_by_the_doctors_full_name_at_full_size(surname):
    # DEMONSTRATES: the office's form names its doctor in full at the top (2026-11-20).
    sheet = billing_sheet.Sheet(doctor=surname)

    with pymupdf.open(stream=billing_sheet.render(sheet), filetype="pdf") as pdf:
        spans = [
            span
            for block in pdf[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
            if span["text"].strip() == DOCTOR_NAMES[surname]
        ]

    assert len(spans) == 1
    assert round(spans[0]["size"]) == 22


def test_a_doctor_no_longer_on_the_list_prints_as_stored():
    assert billing_sheet.doctor_name("Retired") == "Dr. Retired"
    assert billing_sheet.doctor_name("  ") == ""
    assert billing_sheet.doctor_name(None) == ""


def test_a_price_prints_its_amount_in_bold_and_what_it_includes_in_italics():
    # The office's form: "$2,015.00" bold, "-includes 200 pages of records" in italics.
    with pymupdf.open(stream=billing_sheet.render(billing_sheet.Sheet()), filetype="pdf") as pdf:
        fonts = {
            span["text"].strip(): span["font"]
            for block in pdf[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]
        }

    assert "Bold" in fonts["$2,015.00"]
    assert "Oblique" in fonts["-includes 200 pages of records"]
    assert "Bold" in fonts["$503.75"]


def test_a_long_value_is_made_to_fit_its_space():
    sheet = billing_sheet.sheet_for(_doc(claim_numbers="ADJ0000001, " * 30), None)

    _, text = _rendered_text(sheet)

    assert "ADJ0000001" in text
    assert "..." in text


def test_a_blank_sheet_still_renders():
    pages, text = _rendered_text(billing_sheet.Sheet())

    assert pages == 1
    assert "Patient Name:" in text


# --- POST /api/documents/combine: the billing fields ----------------------------------------------


_BILLING = {
    "visit_type": "AME",
    "doctor": DOCTORS[1],
    "review_source": "leonardo",
    "interpreter": "yes",
    "claim_numbers": "ADJ0000001, ADJ0000002",
    "previous_visit": "2026-06-14",
    "declared_pages": "6",
}


async def test_the_billing_fields_typed_at_upload_are_stored(client):
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client, **_BILLING)

    assert resp.status_code == 201, resp.text
    document = _document(resp.json()["id"])
    assert document.visit_type == "AME"
    assert document.doctor == DOCTORS[1]
    assert document.review_source == "leonardo"
    assert document.interpreter is True
    assert document.claim_numbers == "ADJ0000001, ADJ0000002"
    assert document.previous_visit == date(2026, 6, 14)
    assert document.declared_pages == 6
    assert document.appointment_date == date(2026, 12, 1)


async def test_a_supplemental_report_needs_no_appointment_date(client):
    """DEMONSTRATES: SUPP has no appointment - the date is not asked for, and one sent is dropped."""
    _, email = _user()
    await _login(client, email)

    without = await _combine(client, visit_type="SUPP", appointment_date=None)
    with_one = await _combine(client, visit_type="SUPP")

    assert without.status_code == 201, without.text
    assert with_one.status_code == 201, with_one.text
    assert _document(without.json()["id"]).appointment_date is None
    assert _document(with_one.json()["id"]).appointment_date is None


async def test_the_optional_billing_fields_may_be_left_blank(client):
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client)

    assert resp.status_code == 201, resp.text
    document = _document(resp.json()["id"])
    assert document.doctor is None
    assert document.claim_numbers is None
    assert document.previous_visit is None
    assert document.declared_pages is None
    assert document.interpreter is False


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"visit_type": None}, "Choose the type of visit."),
        ({"visit_type": "visit"}, "Choose the type of visit."),
        ({"review_source": None}, "Choose E-DATA, IN-HOUSE or LEONARDO."),
        ({"review_source": "outside"}, "Choose E-DATA, IN-HOUSE or LEONARDO."),
        ({"interpreter": None}, "Choose whether an interpreter is needed."),
        ({"interpreter": "maybe"}, "Choose whether an interpreter is needed."),
        ({"doctor": "Nobody"}, "Choose the doctor from the list."),
        ({"previous_visit": "06/14/2026"}, "Enter the previous office visit as a date."),
        ({"declared_pages": "six"}, "Enter the pages the declaration states as a whole number."),
        ({"declared_pages": "-3"}, "Enter the pages the declaration states as a whole number."),
        ({"claim_numbers": "A" * 256}, "The claim numbers are at most 255 characters."),
    ],
)
async def test_a_billing_field_the_sheet_cannot_hold_is_refused(client, overrides, detail):
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client, **overrides)

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    with get_sessionmaker()() as session:
        assert session.scalars(select(Document).where(Document.user_id == user_id)).all() == []


async def test_the_upload_dialog_is_offered_the_headers_doctors(client):
    _, email = _user()
    await _login(client, email)

    resp = await client.get("/api/documents/combine/doctors")

    assert resp.status_code == 200
    assert resp.json() == {"doctors": list(DOCTORS)}


# --- GET/PUT /api/documents/{id}/billing, GET .../billing-sheet ----------------------------------


def _read_pages(doc_id: str, spec: str) -> None:
    """Store page text as the upload's reading would: ``Q`` testimony, ``.`` prose."""
    with get_sessionmaker()() as session:
        for page, char in enumerate(spec, start=1):
            session.add(
                PageText(
                    document_id=doc_id, page=page, text=_testimony() if char == "Q" else _PROSE
                )
            )
        session.commit()


# Three received files: the email (1 page), the letter (2 pages), the records (3 pages).
_SPEC = "." + ".." + "..."


async def _record(client, **overrides) -> str:
    _, email = _user()
    await _login(client, email)
    resp = await _combine(client, **{**_BILLING, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_the_sheet_waits_for_the_pages_to_be_read(client):
    doc_id = await _record(client)

    got = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert got["counted"] is None
    assert got["pages_counted"] == 5
    assert got["pages_unread"] == 5
    assert got["doctors"] == list(DOCTORS)


async def test_once_read_the_sheet_has_its_counts(client):
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)

    got = (await client.get(f"/api/documents/{doc_id}/billing")).json()

    assert got["counted"] == {"office": 5, "depo": 0}
    assert got["pages_unread"] == 0
    assert got["visit_type"] == "AME"
    assert got["appointment_date"] == "2026-12-01"
    assert got["interpreter"] == "yes"
    assert got["declared_pages"] == 6
    assert got["office_count"] == ""
    assert got["records_from"] == "applicant"
    assert got["received_on"] == "2026-11-03"


async def test_the_sheet_is_edited_and_the_change_audited_by_name(client):
    doc_id = await _record(client)
    payload = {
        **{key: "" for key in ("doctor", "claim_numbers", "previous_visit")},
        "visit_type": "QME",
        "appointment_date": "2026-12-09",
        "review_source": "in_house",
        "interpreter": "no",
        "declared_pages": "5",
        "discrepancy_reason": "One page more than declared",
        "records_from": "defense",
        "office_count": "",
        "depo_pages": "3",
    }

    resp = await client.put(f"/api/documents/{doc_id}/billing", json=payload)

    assert resp.status_code == 200, resp.text
    document = _document(doc_id)
    assert document.visit_type == "QME"
    assert document.appointment_date == date(2026, 12, 9)
    assert document.doctor is None
    assert document.interpreter is False
    assert document.discrepancy_reason == "One page more than declared"
    assert document.records_from == "defense"
    assert (document.office_count, document.depo_pages) == (None, 3)
    assert resp.json()["depo_pages"] == 3
    with get_sessionmaker()() as session:
        details = session.scalars(
            select(AuditLog.detail).where(
                AuditLog.document_id == doc_id, AuditLog.action == "billing.edit"
            )
        ).all()
    assert details == [
        "changed=appointment_date,claim_numbers,declared_pages,depo_pages,discrepancy_reason,"
        "doctor,interpreter,previous_visit,records_from,review_source,visit_type"
    ]


async def test_supp_on_the_sheet_drops_the_appointment_date(client):
    doc_id = await _record(client)

    resp = await client.put(
        f"/api/documents/{doc_id}/billing",
        json={"visit_type": "SUPP", "appointment_date": "2026-12-01"},
    )

    assert resp.status_code == 200, resp.text
    assert _document(doc_id).appointment_date is None
    assert resp.json()["appointment_date"] == ""


async def test_a_doctor_stored_before_the_list_changed_is_kept(client):
    doc_id = await _record(client)
    with get_sessionmaker()() as session:
        session.get(Document, doc_id).doctor = "Retired"
        session.commit()

    kept = await client.put(f"/api/documents/{doc_id}/billing", json={"doctor": "Retired"})
    other = await client.put(f"/api/documents/{doc_id}/billing", json={"doctor": "Nobody"})

    assert kept.status_code == 200, kept.text
    assert other.status_code == 400
    assert other.json()["detail"] == "Choose the doctor from the list."


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [
        ("office_count", "many", "Enter the office count as a whole number."),
        ("depo_pages", "1.5", "Enter the deposition pages as a whole number."),
        (
            "discrepancy_reason",
            "x" * 256,
            "The reason for the discrepancy is at most 255 characters.",
        ),
        ("records_from", "attorney", "Choose who sent the records."),
        ("appointment_date", "12/01/2026", "Enter the appointment date."),
    ],
)
async def test_an_edit_the_sheet_cannot_hold_is_refused_and_nothing_changes(
    client, field, value, detail
):
    doc_id = await _record(client)

    resp = await client.put(
        f"/api/documents/{doc_id}/billing", json={"visit_type": "IME", field: value}
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert _document(doc_id).visit_type == "AME"


async def test_the_sheet_cannot_be_downloaded_while_pages_are_read(client):
    doc_id = await _record(client)

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "The record's pages are still being read. The billing sheet can be downloaded once they are."
    )


async def test_both_counts_typed_let_the_sheet_download_before_the_reading_ends(client):
    doc_id = await _record(client)
    await client.put(
        f"/api/documents/{doc_id}/billing",
        json={"visit_type": "AME", "office_count": "9", "depo_pages": "0"},
    )

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 200, resp.text


async def test_the_sheet_downloads_as_a_pdf_named_after_the_record(client):
    doc_id = await _record(client)
    _read_pages(doc_id, _SPEC)

    resp = await client.get(f"/api/documents/{doc_id}/billing-sheet")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["cache-control"] == "no-store"
    # The name has spaces, so it goes in the RFC 5987 form, as Starlette's own file downloads do.
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=utf-8''Synthetic_Patient-801234-11032026%20Billing%20Sheet.pdf"
    )
    with pymupdf.open(stream=resp.content, filetype="pdf") as pdf:
        text = pdf[0].get_text()
    assert "Synthetic, Patient" in text
    assert "12/01/2026" in text
    with get_sessionmaker()() as session:
        actions = session.scalars(
            select(AuditLog.action).where(AuditLog.document_id == doc_id)
        ).all()
    assert "download_billing" in actions
    assert _document(doc_id).downloaded_at is None


async def test_another_account_cannot_read_or_change_the_sheet(client):
    doc_id = await _record(client)
    _, other = _user()
    await _login(client, other)

    assert (await client.get(f"/api/documents/{doc_id}/billing")).status_code == 404
    assert (
        await client.put(f"/api/documents/{doc_id}/billing", json={"visit_type": "IME"})
    ).status_code == 404
    assert (await client.get(f"/api/documents/{doc_id}/billing-sheet")).status_code == 404


def test_the_fields_the_upload_requires_are_in_the_helper():
    """GUARD for the other test module: its upload form carries every required billing field."""
    for field in ("visit_type", "review_source", "interpreter"):
        assert _FIELDS[field]
