"""The patient's ID: read from the upload's file name, typed by the uploader when it is not there,
and used to count the patient's other records (2026-11-12).

Every name and number here is synthetic. The real file names carry the patient's name, the
office's account number for the patient (the ID) and the date the file was made, in no fixed order.
"""

import importlib.util
import io
import random
from pathlib import Path

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, User
from app.services import patient_ids
from tests.conftest import unique_test_email

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "b7e3c19a4d52_documents_patient_id.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("patient_id_migration", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- reading the ID from a file name ----------------------------------------------------------

# (file name, the ID it carries or None). The four shapes the IDs take, in several orders.
_NAMES = [
    ("Jane Example-801234-11032026.pdf", "801234"),
    ("Jane Example-AB1234-11032026.pdf", "AB1234"),
    ("Jane Example-K12345-11032026.pdf", "K12345"),
    ("Jane Example-1234567-11032026.pdf", "1234567"),
    ("801234 Jane Example 11-03-2026.pdf", "801234"),
    ("ab1234_Example_Jane.pdf", "AB1234"),
    ("Example, Jane 11.3.26 801234.PDF", "801234"),
    ("Jane Example 801234 801234.pdf", "801234"),
    # No ID: a name, a year, a page count, the file date in its three spellings.
    ("Jane Example.pdf", None),
    ("Jane Example records 2026.pdf", None),
    ("Jane Example-11032026.pdf", None),
    ("Jane Example 110326.pdf", None),
    ("Jane Example 11-03-2026 p 12345.pdf", None),
    ("", None),
    # Two different candidates: the uploader is asked, the ID is never guessed.
    ("Jane Example-801234-AB1234.pdf", None),
]


@pytest.mark.parametrize(("name", "expected"), _NAMES)
def test_the_id_is_read_from_the_file_name_in_any_order(name, expected):
    """DEMONSTRATES: the one ID in the name, whatever its place; None when there is none or two."""
    assert patient_ids.from_filename(name) == expected


def test_six_digits_that_read_as_a_date_are_the_file_date_not_an_id():
    """GUARD: 110326 is 11/03/26; 801234 can never be a date (there is no month 80)."""
    assert patient_ids.from_filename("Jane Example 110326.pdf") is None
    assert patient_ids.from_filename("Jane Example 801234.pdf") == "801234"
    assert patient_ids.from_filename("Jane Example 139999.pdf") == "139999"


def test_a_combined_record_takes_the_id_its_pieces_agree_on():
    """DEMONSTRATES `agreed`: pieces without an ID are ignored; two different IDs give none."""
    assert patient_ids.agreed(["a-801234.pdf", "b-801234.pdf", "cover.pdf"]) == "801234"
    assert patient_ids.agreed(["a-801234.pdf", "b-AB1234.pdf"]) is None
    assert patient_ids.agreed(["cover.pdf", "letter.pdf"]) is None


def test_a_typed_id_is_stored_letters_and_digits_upper_case():
    """DEMONSTRATES `clean`: spaces and dashes go, letters go upper-case, an empty box is None."""
    assert patient_ids.clean(" ab-1234 ") == "AB1234"
    assert patient_ids.clean("80 12 34") == "801234"
    assert patient_ids.clean("   ") is None
    assert patient_ids.clean(None) is None
    with pytest.raises(ValueError):
        patient_ids.clean("9" * (patient_ids.MAX_LENGTH + 1))


def test_the_migration_reads_file_names_exactly_as_the_app_does():
    """GUARD: the statement frozen in `b7e3c19a4d52`, run on Postgres, gives the service's answer
    for every case above (and for the same cases with a lower-case extension and padding).
    Synthetic rows, rolled back."""
    names = [name for name, _ in _NAMES] + [f"  {name[:-4]}.pdf " for name, _ in _NAMES if name]
    with get_sessionmaker()() as session:
        user = User(email=unique_test_email(), name="Parity", password="x", active=True)
        session.add(user)
        session.flush()
        ids = []
        for n, name in enumerate(names):
            doc_id = f"pidrule{n}-{user.id}"
            ids.append(doc_id)
            session.add(
                Document(
                    id=doc_id,
                    user_id=user.id,
                    original_filename=name,
                    stored_path="/nonexistent/synthetic.pdf",
                    sha256="0" * 64,
                    page_count=1,
                )
            )
        session.flush()

        _migration().fill_patient_ids(session.connection())
        session.expire_all()

        got = [session.get(Document, doc_id).patient_id for doc_id in ids]
        assert got == [patient_ids.from_filename(name) for name in names]
        session.rollback()


# --- the routes ---------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture
async def authed(client, seeded_user):
    email, password = seeded_user
    resp = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 204
    with get_sessionmaker()() as session:
        user_id = session.scalar(select(User.id).where(User.email == email))
    return client, user_id


def _pdf(pages: int = 1, width: int = 72) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _new_id() -> str:
    """A seven-digit ID no other test is using (tests run side by side on one database)."""
    return str(random.randrange(10**6, 10**7))


async def _upload(client, name: str, *, width: int = 72, **form):
    return await client.post(
        "/api/documents",
        files={"pdf": (name, _pdf(width=width), "application/pdf")},
        data=form,
    )


def _stored_id(doc_id: str) -> str | None:
    with get_sessionmaker()() as session:
        return session.get(Document, doc_id).patient_id


def _audit_details(doc_id: str) -> list[str | None]:
    with get_sessionmaker()() as session:
        return list(
            session.scalars(
                select(AuditLog.detail).where(
                    AuditLog.document_id == doc_id, AuditLog.action == "patient_id.edit"
                )
            ).all()
        )


async def test_an_upload_takes_the_id_from_its_file_name_and_counts_the_patients_records(authed):
    """DEMONSTRATES the request: the ID comes from the file name; a second record of the same
    patient says there is one other, and the record page says so too."""
    client, _ = authed
    pid = _new_id()
    first = await _upload(client, f"Jane Example-{pid}-11032026.pdf")
    assert first.status_code == 201, first.text
    assert first.json()["patient_id"] == pid
    assert first.json()["patient_records"] == 0

    second = await _upload(client, f"{pid} Jane Example.pdf", width=144)
    assert second.json()["patient_records"] == 1
    detail = (await client.get(f"/api/documents/{second.json()['id']}")).json()
    assert (detail["patient_id"], detail["patient_records"]) == (pid, 1)
    listed = (await client.get("/api/documents")).json()
    assert {d["patient_id"] for d in listed} == {pid}


async def test_a_typed_id_wins_over_the_file_name_and_is_cleaned(authed):
    """DEMONSTRATES: what the uploader typed is the ID, stored letters and digits, upper-case."""
    client, _ = authed
    resp = await _upload(client, "Jane Example-801234.pdf", patient_id=" ab-1234 ")
    assert resp.json()["patient_id"] == "AB1234"
    assert _stored_id(resp.json()["id"]) == "AB1234"


async def test_an_upload_with_no_id_anywhere_is_stored_without_one(authed):
    """GUARD: a script's upload of a file named without an ID still succeeds; the record simply
    has no ID until someone gives it."""
    client, _ = authed
    resp = await _upload(client, "scan.pdf")
    assert resp.status_code == 201, resp.text
    assert (resp.json()["patient_id"], resp.json()["patient_records"]) == ("", 0)
    assert _stored_id(resp.json()["id"]) is None


async def test_a_typed_id_too_long_is_refused_before_anything_is_stored(authed):
    """GUARD: a 400 the reviewer can read, and no record."""
    client, user_id = authed
    resp = await _upload(client, "scan.pdf", patient_id="9" * (patient_ids.MAX_LENGTH + 1))
    assert resp.status_code == 400
    # The literal sentence, not the route's constant: the reviewer reads it (errors-and-messages.md).
    assert resp.json()["detail"] == "A patient ID is at most 32 letters and digits."
    with get_sessionmaker()() as session:
        count = session.scalar(
            select(func.count()).select_from(Document).where(Document.user_id == user_id)
        )
    assert count == 0


async def test_a_combined_record_takes_the_typed_id_then_its_name_then_its_pieces(authed):
    """DEMONSTRATES the order on a combined upload: typed, the record's name, the pieces' names."""
    client, _ = authed
    pid, other = _new_id(), _new_id()

    async def combine(name: str, pieces: list[str], typed: str = "") -> str:
        resp = await client.post(
            "/api/documents/aggregate",
            files=[("pdfs", (p, _pdf(), "application/pdf")) for p in pieces],
            data={"name": name, "patient_id": typed},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["patient_id"]

    assert await combine("Jane Example", [f"a {pid}.pdf", f"b {pid}.pdf", "cover.pdf"]) == pid
    assert await combine(f"Jane Example {other}", [f"a {pid}.pdf"]) == other
    assert await combine(f"Jane Example {other}", [f"a {pid}.pdf"], typed=pid) == pid
    assert await combine("Jane Example", [f"a {pid}.pdf", f"b {other}.pdf"]) == ""


async def test_the_id_is_set_and_cleared_by_hand_and_audited_without_the_value(authed):
    """DEMONSTRATES the route: set, cleared, the audit says which and never the ID; an unchanged
    value writes no audit row."""
    client, _ = authed
    doc_id = (await _upload(client, "scan.pdf")).json()["id"]
    pid = _new_id()

    set_ = await client.put(f"/api/documents/{doc_id}/patient-id", json={"patient_id": pid})
    assert set_.status_code == 200, set_.text
    assert (set_.json()["patient_id"], set_.json()["patient_records"]) == (pid, 0)
    await client.put(f"/api/documents/{doc_id}/patient-id", json={"patient_id": pid})
    cleared = await client.put(f"/api/documents/{doc_id}/patient-id", json={"patient_id": ""})
    assert cleared.json()["patient_id"] == ""
    assert _stored_id(doc_id) is None
    assert _audit_details(doc_id) == ["set", "cleared"]


async def test_setting_a_long_id_by_hand_is_refused_and_keeps_the_old_one(authed):
    """GUARD: the 400, and the stored ID is untouched."""
    client, _ = authed
    pid = _new_id()
    doc_id = (await _upload(client, f"Jane Example {pid}.pdf")).json()["id"]
    resp = await client.put(
        f"/api/documents/{doc_id}/patient-id",
        json={"patient_id": "9" * (patient_ids.MAX_LENGTH + 1)},
    )
    assert resp.status_code == 400
    assert _stored_id(doc_id) == pid


async def test_records_in_another_reviewers_account_count_too(authed):
    """DEMONSTRATES: the patient's other records are counted in every account, since another
    reviewer may have done the earlier review."""
    client, _ = authed
    pid = _new_id()
    with get_sessionmaker()() as session:
        other = User(email=unique_test_email(), name="Other", password="x", active=True)
        session.add(other)
        session.flush()
        session.add(
            Document(
                id=f"pid-other-{other.id}",
                user_id=other.id,
                original_filename="synthetic.pdf",
                stored_path="/nonexistent/synthetic.pdf",
                sha256="0" * 64,
                page_count=1,
                patient_id=pid,
            )
        )
        session.commit()
    resp = await _upload(client, f"Jane Example {pid}.pdf")
    assert resp.json()["patient_records"] == 1


def test_the_migration_fills_the_id_from_stored_file_names():
    """DEMONSTRATES the backfill in `b7e3c19a4d52`: an ID in the name is stored; a name without
    one stays empty; an ID already stored is never overwritten. Synthetic rows, rolled back."""
    migration = _migration()
    pid = _new_id()
    with get_sessionmaker()() as session:
        user = User(email=unique_test_email(), name="Backfill", password="x", active=True)
        session.add(user)
        session.flush()

        def doc(n: int, name: str, stored: str | None = None) -> Document:
            return Document(
                id=f"pidfill{n}-{user.id}",
                user_id=user.id,
                original_filename=name,
                stored_path="/nonexistent/synthetic.pdf",
                sha256="0" * 64,
                page_count=1,
                patient_id=stored,
            )

        named, unnamed, typed = (
            doc(1, f"Jane Example-{pid}-11032026.pdf"),
            doc(2, "scan.pdf"),
            doc(3, "Jane Example-801234.pdf", stored="AB1234"),
        )
        session.add_all([named, unnamed, typed])
        session.flush()

        migration.fill_patient_ids(session.connection())
        session.expire_all()

        assert session.get(Document, named.id).patient_id == pid
        assert session.get(Document, unnamed.id).patient_id is None
        assert session.get(Document, typed.id).patient_id == "AB1234"
        session.rollback()
