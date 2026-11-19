"""A record put together from the files it arrived in: POST /api/documents/combine, the raw download
(GET /api/documents/{id}/raw) and services/received_files.py.

Synthetic blank PDFs only; each file's pages get their own width so the joined order can be read
back from the stored PDF. Uploads go to a per-test tmp dir.
"""

import io
import os
from datetime import date

import pytest
from pypdf import PdfReader, PdfWriter
from rq import Queue
from sqlalchemy import select

from app.auth.password import MrrPasswordHelper
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AuditLog, Document, Job, User
from app.services import patient_ids, received_files
from app.worker.queues import OCR_QUEUE, get_redis, lane_name
from tests.conftest import lanes, unique_test_email

_PASSWORD = "Str0ng#pw1"


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    # Uploads are PHI at rest: keep them in a per-test tmp dir that pytest removes.
    monkeypatch.setattr(get_settings(), "upload_folder", str(tmp_path))


@pytest.fixture(autouse=True)
def _empty_queues():
    queue = Queue(OCR_QUEUE, connection=get_redis())
    queue.empty()
    yield
    queue.empty()
    lanes("segment").empty()


def _pdf(pages: int = 1, width: int = 72) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _widths(path: str) -> list[int]:
    return [round(float(page.mediabox.width)) for page in PdfReader(path).pages]


def _user(*, is_admin: bool = False) -> tuple[int, str]:
    email = unique_test_email()
    with get_sessionmaker()() as session:
        user = User(
            email=email,
            name="Synthetic",
            password=MrrPasswordHelper().hash(_PASSWORD),
            active=True,
            is_admin=is_admin,
        )
        session.add(user)
        session.commit()
        return user.id, email


async def _login(client, email: str) -> None:
    await client.post("/api/auth/logout")
    resp = await client.post("/api/auth/login", data={"username": email, "password": _PASSWORD})
    assert resp.status_code == 204


_FIELDS = {
    "last_name": "Synthetic",
    "first_name": "Patient",
    "patient_id": "801234",
    "date_received": "2026-11-03",
}

# Three files, as they arrived: the email (1 page), the letter (2 pages), the records (3 pages).
_FILES = [
    ("email", _pdf(1, width=101)),
    ("advocacy_letter", _pdf(2, width=102)),
    ("records", _pdf(3, width=103)),
]


async def _combine(client, files=_FILES, **overrides):
    fields = {**_FIELDS, **overrides}
    data = {key: value for key, value in fields.items() if value is not None}
    data["kinds"] = [kind for kind, _ in files]
    return await client.post(
        "/api/documents/combine",
        files=[
            ("pdfs", (f"part{i}.pdf", body, "application/pdf")) for i, (_, body) in enumerate(files)
        ],
        data=data,
    )


def _document(doc_id: str) -> Document:
    with get_sessionmaker()() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        session.expunge(document)
        return document


def _audit(doc_id: str, action: str) -> list[tuple[int, str | None]]:
    with get_sessionmaker()() as session:
        return [
            (row.user_id, row.detail)
            for row in session.scalars(
                select(AuditLog).where(AuditLog.document_id == doc_id, AuditLog.action == action)
            ).all()
        ]


def _documents_of(user_id: int) -> list[Document]:
    with get_sessionmaker()() as session:
        return list(session.scalars(select(Document).where(Document.user_id == user_id)).all())


# --- services/received_files.py ------------------------------------------------------------------


def test_the_record_is_named_as_the_office_names_its_files():
    """DEMONSTRATES: Last_First-ID-MMDDYYYY, and the patient ID reads back from it."""
    name = received_files.record_filename("Synthetic", "Patient", "801234", date(2026, 11, 3))
    assert name == "Synthetic_Patient-801234-11032026.pdf"
    assert patient_ids.from_filename(name) == "801234"


def test_a_name_of_several_words_keeps_every_word():
    name = received_files.record_filename("De La Testa", "Ana Maria", "AB1234", date(2026, 1, 9))
    assert name == "De_La_Testa_Ana_Maria-AB1234-01092026.pdf"
    assert patient_ids.from_filename(name) == "AB1234"


def test_characters_a_file_name_cannot_carry_separate_words():
    name = received_files.record_filename("O'Test-Sample/", "  Pat. ", "A12345", date(2026, 2, 1))
    assert name == "O'Test-Sample_Pat-A12345-02012026.pdf"


def test_combine_joins_the_files_in_the_order_given(tmp_path):
    """DEMONSTRATES: the pages land in the order the files were given, and each file's place is
    known."""
    paths = []
    for i, (kind, body) in enumerate(_FILES):
        path = tmp_path / f"in{i}.pdf"
        path.write_bytes(body)
        paths.append((str(path), kind))
    out = tmp_path / "out.pdf"

    parts = received_files.combine(paths, str(out))

    assert [part.as_dict() for part in parts] == [
        {"kind": "email", "start": 1, "end": 1},
        {"kind": "advocacy_letter", "start": 2, "end": 3},
        {"kind": "records", "start": 4, "end": 6},
    ]
    assert _widths(str(out)) == [101, 102, 102, 103, 103, 103]


@pytest.mark.parametrize("bad", [b"not a pdf", b""])
def test_an_unreadable_file_refuses_the_whole_record_and_names_its_place(tmp_path, bad):
    """GUARD: one unreadable file is never skipped quietly - the old split upload did that, so a
    record could miss one of the files it arrived in. Nothing is written."""
    good, broken = tmp_path / "good.pdf", tmp_path / "broken.pdf"
    good.write_bytes(_pdf())
    broken.write_bytes(bad)
    out = tmp_path / "out.pdf"

    with pytest.raises(received_files.UnreadableFile) as caught:
        received_files.combine([(str(good), "email"), (str(broken), "records")], str(out))

    assert caught.value.position == 2
    assert not out.exists()


def test_every_kind_has_a_label_and_the_order_starts_with_the_cover_sheet():
    assert set(received_files.KIND_LABELS) == set(received_files.KINDS)
    assert received_files.KINDS[0] == "cover"
    assert received_files.KINDS[-1] == "records"


# --- POST /api/documents/combine -----------------------------------------------------------------


async def test_the_files_become_one_record_named_and_headed_as_typed(client):
    """DEMONSTRATES the office's request: the files are joined in the order sent into one record,
    named Last_First-ID-MMDDYYYY, with the patient's name, ID and the date received filled in, and
    each file's kind and pages kept."""
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["page_count"] == 6
    assert [part["kind"] for part in body["parts"]] == ["email", "advocacy_letter", "records"]
    assert body["patient_id"] == "801234"
    document = _document(body["id"])
    assert document.user_id == user_id
    assert document.original_filename == "Synthetic_Patient-801234-11032026.pdf"
    assert (document.patient_last_name, document.patient_first_name) == ("Synthetic", "Patient")
    assert document.patient_id == "801234"
    assert document.received_on == date(2026, 11, 3)
    assert document.received_parts == [
        {"kind": "email", "start": 1, "end": 1},
        {"kind": "advocacy_letter", "start": 2, "end": 3},
        {"kind": "records", "start": 4, "end": 6},
    ]
    assert document.status == "uploaded"
    assert _widths(document.stored_path) == [101, 102, 102, 103, 103, 103]
    assert _audit(document.id, "combine_upload") == [(user_id, "3 files")]


async def test_the_order_sent_is_the_order_kept(client):
    """GUARD: the server joins in the order the uploader confirmed, never re-sorting by kind."""
    _, email = _user()
    await _login(client, email)
    files = [("records", _pdf(1, width=103)), ("email", _pdf(1, width=101))]

    resp = await _combine(client, files=files)

    assert resp.status_code == 201, resp.text
    assert _widths(_document(resp.json()["id"]).stored_path) == [103, 101]


async def test_a_combined_record_is_read_but_not_identified(client):
    """GUARD: like the single upload, the pages are queued for reading (no model) and nothing
    starts Identify - and unlike the old split upload, no classify job runs."""
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client)

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    queue = Queue(OCR_QUEUE, connection=get_redis())
    assert [job.args for job in queue.jobs] == [(doc_id,)]
    with get_sessionmaker()() as session:
        assert session.scalars(select(Job).where(Job.document_id == doc_id)).all() == []
    assert lanes("segment").count == 0


async def test_the_listing_says_when_the_records_came_and_from_which_files(client):
    _, email = _user()
    await _login(client, email)
    doc_id = (await _combine(client)).json()["id"]

    got = (await client.get(f"/api/documents/{doc_id}")).json()

    assert got["received_on"] == "2026-11-03"
    assert [part["kind"] for part in got["received_parts"]] == [
        "email",
        "advocacy_letter",
        "records",
    ]


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"last_name": " "}, "Enter the patient's last and first name."),
        ({"first_name": None}, "Enter the patient's last and first name."),
        ({"patient_id": ""}, "Enter the patient ID."),
        ({"patient_id": "-"}, "Enter the patient ID."),
        ({"date_received": ""}, "Enter the date the records were received."),
        ({"date_received": "11/03/2026"}, "Enter the date the records were received."),
    ],
)
async def test_the_name_the_id_and_the_date_received_are_required(client, overrides, detail):
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client, **overrides)

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert _documents_of(user_id) == []


async def test_each_file_needs_its_kind(client):
    user_id, email = _user()
    await _login(client, email)
    files = [("email", _pdf()), ("records", _pdf())]

    short = await client.post(
        "/api/documents/combine",
        files=[
            ("pdfs", (f"p{i}.pdf", body, "application/pdf")) for i, (_, body) in enumerate(files)
        ],
        data={**_FIELDS, "kinds": ["email"]},
    )
    unknown = await _combine(client, files=[("letter", _pdf())])

    for resp in (short, unknown):
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Choose what each file is."
    assert _documents_of(user_id) == []


async def test_no_files_is_a_400(client):
    _, email = _user()
    await _login(client, email)
    resp = await client.post("/api/documents/combine", data=_FIELDS)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no PDFs uploaded"


async def test_an_unreadable_file_uploads_nothing_and_says_which_one(client, tmp_path):
    """GUARD: no record, and no file of it - joined or temporary - is left behind."""
    user_id, email = _user()
    await _login(client, email)
    files = [("email", _pdf()), ("records", b"not a pdf"), ("records", _pdf())]

    resp = await _combine(client, files=files)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "File 2 is not a readable PDF. Nothing was uploaded."
    assert _documents_of(user_id) == []
    left = [name for _, _, names in os.walk(tmp_path) for name in names]
    assert left == []


async def test_an_admin_combines_into_a_reviewers_account(client):
    reviewer_id, _ = _user()
    admin_id, admin_email = _user(is_admin=True)
    await _login(client, admin_email)

    resp = await _combine(client, owner=str(reviewer_id))

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["id"]
    assert _document(doc_id).user_id == reviewer_id
    assert _audit(doc_id, "combine_upload") == [(admin_id, f"3 files, for user {reviewer_id}")]
    # Nothing waits in the reviewer's lane: Identify is the reviewer's to start.
    assert Queue(lane_name("segment", reviewer_id), connection=get_redis()).count == 0


async def test_a_reviewer_cannot_combine_into_someone_elses_account(client):
    other_id, _ = _user()
    _, email = _user()
    await _login(client, email)

    resp = await _combine(client, owner=str(other_id))

    assert resp.status_code == 403
    assert _documents_of(other_id) == []


# --- GET /api/documents/{id}/raw -----------------------------------------------------------------


async def test_the_raw_record_downloads_as_it_was_put_together(client):
    """DEMONSTRATES: the joined file comes back byte for byte as a download under the record's
    name, for the office's backup; it is not a download of the review."""
    user_id, email = _user()
    await _login(client, email)
    doc_id = (await _combine(client)).json()["id"]
    document = _document(doc_id)

    resp = await client.get(f"/api/documents/{doc_id}/raw")

    assert resp.status_code == 200
    with open(document.stored_path, "rb") as stored:
        assert resp.content == stored.read()
    assert resp.headers["content-type"] == "application/pdf"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "Synthetic_Patient-801234-11032026.pdf" in disposition
    assert resp.headers["cache-control"] == "no-store"
    assert _audit(doc_id, "download_raw") == [(user_id, None)]
    assert _document(doc_id).downloaded_at is None


async def test_a_record_uploaded_as_one_pdf_downloads_raw_too(client):
    _, email = _user()
    await _login(client, email)
    body = _pdf(2)
    resp = await client.post(
        "/api/documents",
        files={"pdf": ("Synthetic_Patient-801234-11032026", body, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text

    got = await client.get(f"/api/documents/{resp.json()['id']}/raw")

    assert got.status_code == 200
    assert got.content == body
    assert 'filename="Synthetic_Patient-801234-11032026.pdf"' in got.headers["content-disposition"]


async def test_someone_elses_raw_record_is_not_found(client):
    _, owner_email = _user()
    await _login(client, owner_email)
    doc_id = (await _combine(client)).json()["id"]
    _, stranger_email = _user()
    await _login(client, stranger_email)

    resp = await client.get(f"/api/documents/{doc_id}/raw")

    assert resp.status_code == 404
