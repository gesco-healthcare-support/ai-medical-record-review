"""When a record's report is due (models.Document.due_on) and the list that orders records by it
(GET /api/documents: `visit_type`, `due_on`).

The office's answer (2026-11-25): a record is due on its appointment date; a supplemental report
and late meds have no appointment and are due 40 days after the records were received.

Synthetic data only: blank PDF pages, made-up names. No model, no OCR.
"""

from datetime import date

import pytest

from app.config import get_settings
from app.models import REPORT_DAYS_AFTER_RECEIVED, VISITS_WITHOUT_APPOINTMENT, Document
from app.services import billing_sheet, received_files
from tests.test_received_files import _combine, _login, _pdf, _user


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


def _doc(**fields) -> Document:
    return Document(
        id="00000000-0000-0000-0000-000000000000",
        user_id=1,
        original_filename="x.pdf",
        stored_path="x.pdf",
        **fields,
    )


# --- models.Document.due_on --------------------------------------------------------------------


def test_a_visit_is_due_on_its_appointment_date():
    record = _doc(
        visit_type="QME", appointment_date=date(2026, 12, 25), received_on=date(2026, 12, 15)
    )

    assert record.due_on() == date(2026, 12, 25)


@pytest.mark.parametrize("visit", ["SUPP", "LATE"])
def test_a_visit_with_no_appointment_is_due_40_days_after_the_records_came_in(visit):
    """DEMONSTRATES: 40 days after the day received, whatever appointment date is stored."""
    record = _doc(
        visit_type=visit, appointment_date=date(2026, 11, 30), received_on=date(2026, 12, 15)
    )

    assert record.due_on() == date(2027, 1, 24)


def test_without_the_dates_it_needs_a_record_has_no_deadline():
    assert _doc().due_on() is None  # uploaded as one PDF
    assert _doc(visit_type="QME").due_on() is None
    assert _doc(visit_type="LATE", appointment_date=date(2026, 12, 1)).due_on() is None


def test_the_rule_agrees_with_the_billing_sheet():
    """GUARD: the visits with no appointment are the billing sheet's; the 40 days the office's."""
    assert billing_sheet.NO_APPOINTMENT == VISITS_WITHOUT_APPOINTMENT
    assert set(VISITS_WITHOUT_APPOINTMENT) <= set(billing_sheet.VISIT_TYPES)
    assert REPORT_DAYS_AFTER_RECEIVED == 40


# --- GET /api/documents ------------------------------------------------------------------------


async def test_the_list_says_when_each_record_is_due(client):
    """DEMONSTRATES: each listed record carries its type of visit and the day it is due."""
    _, email = _user()
    await _login(client, email)
    visit = await _combine(client, visit_type="QME", appointment_date="2026-12-01")
    late = await _combine(client, visit_type="LATE", appointment_date=None)
    supp = await _combine(client, visit_type="SUPP", appointment_date=None)
    for resp in (visit, late, supp):
        assert resp.status_code == 201, resp.text

    # A record put together from received files waits in the team queue (test_team_queue.py).
    listed = {doc["id"]: doc for doc in (await client.get("/api/documents/queue")).json()}

    # received 2026-11-03 (tests/test_received_files.py _FIELDS); + 40 days = 2026-12-13
    assert (listed[visit.json()["id"]]["visit_type"], listed[visit.json()["id"]]["due_on"]) == (
        "QME",
        "2026-12-01",
    )
    assert (listed[late.json()["id"]]["visit_type"], listed[late.json()["id"]]["due_on"]) == (
        "LATE",
        "2026-12-13",
    )
    assert listed[supp.json()["id"]]["due_on"] == "2026-12-13"


async def test_a_record_uploaded_as_one_pdf_has_no_deadline(client):
    _, email = _user()
    await _login(client, email)
    resp = await client.post(
        "/api/documents", files={"pdf": ("record.pdf", _pdf(), "application/pdf")}
    )
    assert resp.status_code == 201, resp.text

    (listed,) = (await client.get("/api/documents")).json()

    assert (listed["visit_type"], listed["due_on"]) == ("", "")
