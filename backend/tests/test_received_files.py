"""A record put together from the files it arrived in: each file staged and read
(POST/DELETE /api/documents/combine/files), the record joined (POST /api/documents/combine), the raw
download (GET /api/documents/{id}/raw) and services/received_files.py.

Synthetic PDFs only - blank pages, or a line of made-up text; each file's pages get their own width
so the joined order can be read back from the stored PDF. Uploads go to a per-test tmp dir, and OCR
is stubbed (no Tesseract in a test).
"""

import hashlib
import io
import logging
import os
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pymupdf
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
def _no_ocr(monkeypatch):
    """A scanned first page reads as nothing here: tests never run Tesseract."""
    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", lambda *a, **k: "")


@pytest.fixture(autouse=True)
def _model_off(monkeypatch):
    """No model call leaves a test: by default the model server does not answer, so the keyword
    rules guess (as on our model's box with the pod off). Tests of the model path stub it."""

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


def _pdf(pages: int = 1, width: int = 72) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _text_pdf(text: str, pages: int = 1) -> bytes:
    """A PDF whose first page carries ``text`` as real text (as an emailed letter does)."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=612, height=792)
        if number == 0:
            page.insert_textbox(pymupdf.Rect(36, 36, 576, 756), text)
    data = doc.tobytes()
    doc.close()
    return data


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
    "appointment_date": "2026-12-01",
    "records_from": "applicant",
    # The billing sheet's required fields (tests/test_billing_sheet.py covers them).
    "visit_type": "QME",
    "review_source": "in_house",
    "interpreter": "no",
}

# Three files, as they arrived: the email (1 page), the letter (2 pages), the records (3 pages).
_FILES = [
    ("email", _pdf(1, width=101)),
    ("advocacy_letter", _pdf(2, width=102)),
    ("records", _pdf(3, width=103)),
]


async def _stage(client, body: bytes, name: str = "part.pdf"):
    return await client.post(
        "/api/documents/combine/files", files={"pdf": (name, body, "application/pdf")}
    )


async def _combine(client, files=_FILES, **overrides):
    """Stage each file as the dialog does, then put the record together from the tokens."""
    tokens = []
    for i, (_, body) in enumerate(files):
        staged = await _stage(client, body, f"part{i}.pdf")
        assert staged.status_code == 201, staged.text
        tokens.append(staged.json()["token"])
    fields = {**_FIELDS, **overrides}
    data = {key: value for key, value in fields.items() if value is not None}
    data["kinds"] = [kind for kind, _ in files]
    data["tokens"] = tokens
    return await client.post("/api/documents/combine", data=data)


def _staged_files(tmp_path, user_id: int) -> list[str]:
    folder = Path(tmp_path) / str(user_id) / "staged"
    return sorted(p.name for p in folder.glob("*")) if folder.exists() else []


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


@pytest.mark.parametrize(
    ("text", "pages", "kind"),
    [
        ("PROOF OF SERVICE\nI served the documents listed below", 1, "proof_of_service"),
        ("Declaration of Service by mail", 1, "proof_of_service"),
        ("From: Sample Office\nSent: Monday\nTo: Intake\nSubject: Records received", 2, "email"),
        ("ATTENTION: MEDICAL PROVIDERS\nPreviously Sent - not billable", 1, "medical_index"),
        ("Index of Medical Records enclosed", 3, "medical_index"),
        (
            "DECLARATION OF CUSTODIAN OF RECORDS\nunder penalty of perjury, true copies",
            2,
            "declaration",
        ),
        ("Declaration pursuant to Labor Code 4062.3 regarding records", 1, "declaration"),
        (
            "Dear Dr. Sample,\nPlease find enclosed the records for the evaluation.",
            3,
            "advocacy_letter",
        ),
        (
            "Re: Panel QME evaluation of the applicant\nWe represent the defendant.",
            4,
            "advocacy_letter",
        ),
        ("ADVOCACY LETTER", 2, "advocacy_letter"),
        ("FIRST-CLASS MAIL  U.S. POSTAGE PAID", 1, "envelope"),
        ("Progress report, follow-up visit. Assessment and plan.", 12, "records"),
        ("", 1, "records"),
    ],
)
def test_the_first_page_says_what_kind_of_file_it_is(text, pages, kind):
    """DEMONSTRATES: the kind is read from the first page, so the order does not depend on the
    files' names or the order they were added in."""
    assert received_files.guess_kind(text, pages) == kind


def test_a_long_file_that_opens_with_a_custodians_declaration_is_records():
    """GUARD: the office's main declaration is a few pages; a copy service's records open with a
    custodian's declaration of their own, which the office ignores (2026-11-19)."""
    text = "DECLARATION OF CUSTODIAN OF RECORDS\nunder penalty of perjury"
    assert received_files.guess_kind(text, 5) == "declaration"
    assert received_files.guess_kind(text, 40) == "records"


def test_a_long_file_with_postal_words_is_not_an_envelope():
    assert received_files.guess_kind("USPS tracking enclosed", 2) == "envelope"
    assert received_files.guess_kind("USPS tracking enclosed", 30) == "records"


def test_the_pdfs_own_text_is_read_without_ocr(tmp_path, monkeypatch):
    def no_ocr(*_args, **_kwargs):
        raise AssertionError("a page with text needs no OCR")

    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", no_ocr)
    path = tmp_path / "letter.pdf"
    path.write_bytes(_text_pdf("Dear Dr. Sample, enclosed are the records for the evaluation."))

    assert "Dear Dr. Sample" in received_files.first_page_text(path)


def test_a_scanned_first_page_is_read_by_ocr(tmp_path, monkeypatch):
    calls = []

    def ocr(path, pages):
        calls.append(pages)
        return "PROOF OF SERVICE"

    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", ocr)
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf())

    assert received_files.first_page_text(path) == "PROOF OF SERVICE"
    assert calls == [[1]]


def test_an_ocr_failure_gives_no_text_not_an_error(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("tesseract went away")

    monkeypatch.setattr(received_files, "extract_text_from_selected_pages", broken)
    path = tmp_path / "scan.pdf"
    path.write_bytes(_pdf())

    assert received_files.first_page_text(path) == ""


def test_old_staged_files_are_swept_from_every_account(tmp_path):
    old = tmp_path / "1" / "staged" / "old.pdf"
    new = tmp_path / "2" / "staged" / "new.pdf"
    for path in (old, new):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"%PDF-")
    hours_ago = time.time() - (received_files.STAGED_HOURS + 1) * 3600
    os.utime(old, (hours_ago, hours_ago))

    assert received_files.sweep(str(tmp_path)) == 1
    assert not old.exists()
    assert new.exists()


def test_a_staged_file_is_found_only_by_its_token_in_its_own_folder(tmp_path):
    """GUARD: a token finds a file only in the caller's own staging folder, and only the file it
    names exactly; a token of another shape names nothing."""
    assert received_files.staged_path(str(tmp_path), 1, "../../etc/passwd") is None
    assert received_files.staged_path(str(tmp_path), 1, "") is None
    token, path = received_files.new_staged_path(str(tmp_path), 1)
    assert len(token) == 32
    assert path == tmp_path / "1" / "staged" / f"{token}.pdf"
    # Nothing written yet: the token names no file.
    assert received_files.staged_path(str(tmp_path), 1, token) is None
    path.write_bytes(b"%PDF-")
    assert received_files.staged_path(str(tmp_path), 1, token) == path
    assert received_files.staged_path(str(tmp_path), 2, token) is None
    other, _ = received_files.new_staged_path(str(tmp_path), 1)
    assert received_files.staged_path(str(tmp_path), 1, other) is None


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


def test_the_kinds_follow_the_offices_guideline():
    """GUARD: the order the office set (2026-11-18/19). The cover sheet is no longer used."""
    assert received_files.KINDS == (
        "advocacy_letter",
        "declaration",
        "medical_index",
        "records",
        "proof_of_service",
        "email",
        "envelope",
    )
    assert set(received_files.KIND_LABELS) == set(received_files.KINDS)


def test_every_party_has_a_label():
    assert received_files.PARTIES == ("defense", "applicant", "insurance", "copy_service")
    assert set(received_files.PARTY_LABELS) == set(received_files.PARTIES)


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
    assert document.appointment_date == date(2026, 12, 1)
    assert document.records_from == "applicant"
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
    assert got["appointment_date"] == "2026-12-01"
    assert got["records_from"] == "applicant"
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
        ({"appointment_date": ""}, "Enter the appointment date."),
        ({"appointment_date": None}, "Enter the appointment date."),
        ({"appointment_date": "12/01/2026"}, "Enter the appointment date."),
        ({"records_from": ""}, "Choose who sent the records."),
        ({"records_from": None}, "Choose who sent the records."),
        ({"records_from": "attorney"}, "Choose who sent the records."),
    ],
)
async def test_every_detail_the_uploader_gives_is_required(client, overrides, detail):
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client, **overrides)

    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    assert _documents_of(user_id) == []


async def test_each_file_needs_its_kind(client):
    user_id, email = _user()
    await _login(client, email)
    tokens = [(await _stage(client, _pdf())).json()["token"] for _ in range(2)]

    short = await client.post(
        "/api/documents/combine", data={**_FIELDS, "kinds": ["email"], "tokens": tokens}
    )
    unknown = await _combine(client, files=[("letter", _pdf())])
    # The office's cover sheet is no longer used (2026-11-19), so it is no longer a kind.
    cover = await _combine(client, files=[("cover", _pdf())])

    for resp in (short, unknown, cover):
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Choose what each file is."
    assert _documents_of(user_id) == []


async def test_no_files_is_a_400(client):
    _, email = _user()
    await _login(client, email)
    resp = await client.post("/api/documents/combine", data=_FIELDS)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no PDFs uploaded"


# --- POST/DELETE /api/documents/combine/files ----------------------------------------------------


async def test_a_file_is_staged_counted_and_read_as_it_is_added(client, tmp_path):
    """DEMONSTRATES: each file reaches the server on its own, its pages are counted and its first
    page tells its kind - a file named "part.pdf" that is a proof of service is one."""
    user_id, email = _user()
    await _login(client, email)

    resp = await _stage(client, _text_pdf("PROOF OF SERVICE\nI served the documents", pages=3))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["pages"], body["kind"]) == (3, "proof_of_service")
    assert _staged_files(tmp_path, user_id) == [f"{body['token']}.pdf"]
    assert _documents_of(user_id) == []


@pytest.mark.parametrize("bad", [b"not a pdf", b""])
async def test_an_unreadable_file_is_refused_when_added_and_not_kept(client, tmp_path, bad):
    user_id, email = _user()
    await _login(client, email)

    resp = await _stage(client, bad)

    assert resp.status_code == 400
    assert resp.json()["detail"] == "This file is not a readable PDF or image."
    assert _staged_files(tmp_path, user_id) == []


async def test_staging_needs_a_file(client):
    _, email = _user()
    await _login(client, email)
    resp = await client.post("/api/documents/combine/files")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no PDFs uploaded"


async def test_a_removed_file_is_deleted_and_removing_it_again_is_harmless(client, tmp_path):
    user_id, email = _user()
    await _login(client, email)
    token = (await _stage(client, _pdf())).json()["token"]

    first = await client.delete(f"/api/documents/combine/files/{token}")
    again = await client.delete(f"/api/documents/combine/files/{token}")
    odd = await client.delete("/api/documents/combine/files/not-a-token")

    assert [r.status_code for r in (first, again, odd)] == [200, 200, 200]
    assert _staged_files(tmp_path, user_id) == []


async def test_the_staged_files_are_deleted_once_the_record_is_stored(client, tmp_path):
    user_id, email = _user()
    await _login(client, email)

    resp = await _combine(client)

    assert resp.status_code == 201, resp.text
    assert _staged_files(tmp_path, user_id) == []


async def test_another_accounts_staged_file_cannot_be_used(client, tmp_path):
    """GUARD: a token is looked up in the caller's own staging folder only."""
    owner_id, owner_email = _user()
    await _login(client, owner_email)
    token = (await _stage(client, _pdf())).json()["token"]
    stranger_id, stranger_email = _user()
    await _login(client, stranger_email)

    resp = await client.post(
        "/api/documents/combine", data={**_FIELDS, "kinds": ["records"], "tokens": [token]}
    )
    removed = await client.delete(f"/api/documents/combine/files/{token}")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "File 1 is no longer on the server. Add it again."
    assert _documents_of(stranger_id) == []
    assert removed.status_code == 200
    assert _staged_files(tmp_path, owner_id) == [f"{token}.pdf"]


async def test_a_staged_file_that_went_bad_uploads_nothing_and_says_which_one(client, tmp_path):
    """GUARD: no record and no joined file is left behind; the staged files stay for a retry."""
    user_id, email = _user()
    await _login(client, email)
    tokens = [(await _stage(client, _pdf(n))).json()["token"] for n in (1, 2, 3)]
    (Path(tmp_path) / str(user_id) / "staged" / f"{tokens[1]}.pdf").write_bytes(b"not a pdf")

    resp = await client.post(
        "/api/documents/combine",
        data={**_FIELDS, "kinds": ["email", "records", "records"], "tokens": tokens},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "File 2 is not a readable PDF. Nothing was uploaded."
    assert _documents_of(user_id) == []
    assert [p.name for p in (Path(tmp_path) / str(user_id)).glob("*.pdf")] == []


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


# --- what a file is: the model, and the rules when it gives no answer -----------------------------


class _Model:
    """A fake model behind the seam: answers ``reply`` and remembers what it was asked."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.reply)


def _answering(monkeypatch, reply: str) -> _Model:
    model = _Model(reply)
    stages = []
    monkeypatch.setattr(
        received_files, "provider_for_stage", lambda stage: stages.append(stage) or model
    )
    model.stages = stages  # type: ignore[attr-defined]
    return model


def test_the_model_decides_what_a_file_is(monkeypatch):
    """DEMONSTRATES: the kind is the model's. A subpoena's proof of service - which the keyword
    rules took for the case's own - is records when the model says so, and a sure answer is not
    flagged."""
    model = _answering(monkeypatch, '{"kind": "records", "certain": true}')

    reading = received_files.read_kind("PROOF OF SERVICE\nof deposition subpoena", 3)

    assert reading == received_files.KindReading("records", False, "model")
    assert received_files.guess_kind("PROOF OF SERVICE\nof deposition subpoena", 3) == (
        "proof_of_service"
    )
    assert model.stages == ["extract"]
    call = model.calls[0]
    assert call["stage"] == "extract"
    assert call["model"] == get_settings().model_for_stage("extract")
    assert call["schema"]["properties"]["kind"]["enum"] == list(received_files.KINDS)
    assert call["temperature"] == 0.0
    assert "This file has 3 page(s)" in call["parts"][0].text


def test_an_unsure_answer_is_flagged_for_the_uploader(monkeypatch):
    _answering(monkeypatch, '{"kind": "declaration", "certain": false}')
    assert received_files.read_kind("DECLARATION", 2) == received_files.KindReading(
        "declaration", True, "model"
    )


@pytest.mark.parametrize(
    "reply", ["not json", '{"kind": "letter", "certain": true}', "[]", '{"certain": true}']
)
def test_an_unusable_answer_leaves_the_rules_and_a_flag(monkeypatch, reply):
    """GUARD: a reply outside the kinds is never trusted; the rules guess and the uploader looks."""
    _answering(monkeypatch, reply)
    assert received_files.read_kind("PROOF OF SERVICE\nI served", 1) == (
        received_files.KindReading("proof_of_service", True, "rules")
    )


def test_with_the_model_off_the_rules_guess_and_the_file_is_flagged(monkeypatch, caplog):
    """GUARD: our model's box with the pod off - the model server does not answer - still stages the
    file; nothing about the page is logged."""

    def off(stage):
        raise ConnectionError("the model server is not running")

    monkeypatch.setattr(received_files, "provider_for_stage", off)
    with caplog.at_level(logging.WARNING):
        reading = received_files.read_kind("Dear Dr. Sample:\nPlease find enclosed", 2)

    assert reading == received_files.KindReading("advocacy_letter", True, "rules")
    assert "Dear" not in caplog.text
    assert "ConnectionError" in caplog.text


def test_a_page_with_no_text_is_records_flagged_and_asks_no_model(monkeypatch):
    model = _answering(monkeypatch, '{"kind": "email", "certain": true}')
    assert received_files.read_kind("  \n ", 4) == received_files.KindReading(
        "records", True, "none"
    )
    assert model.calls == []


def test_a_long_page_is_cut_before_it_is_sent(monkeypatch):
    model = _answering(monkeypatch, '{"kind": "records", "certain": true}')
    received_files.read_kind("x" * 20000, 1)
    sent = model.calls[0]["parts"][0].text
    assert sent.count("x") == received_files._KIND_TEXT_CHARS


async def test_staging_answers_the_models_kind_its_flag_and_the_files_fingerprint(
    client, monkeypatch
):
    _answering(monkeypatch, '{"kind": "medical_index", "certain": true}')
    _, email = _user()
    await _login(client, email)
    body = _text_pdf("INDEX OF RECORDS", pages=2)

    resp = await _stage(client, body)

    assert resp.status_code == 201
    staged = resp.json()
    assert (staged["kind"], staged["check"], staged["pages"]) == ("medical_index", False, 2)
    assert staged["sha256"] == hashlib.sha256(body).hexdigest()


# --- the same file twice, scanned images, password-protected PDFs, the preview ------------------


async def test_the_same_file_twice_is_refused_and_nothing_is_stored(client):
    """DEMONSTRATES: a file added twice would be joined - and reviewed - twice."""
    user_id, email = _user()
    await _login(client, email)
    tokens = [(await _stage(client, body)).json()["token"] for body in (_pdf(1), _pdf(2), _pdf(1))]

    resp = await client.post(
        "/api/documents/combine",
        data={**_FIELDS, "kinds": ["records"] * 3, "tokens": tokens},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "File 3 is the same file as file 1. Remove one of them."
    assert _documents_of(user_id) == []


async def test_one_token_sent_twice_is_refused_too(client):
    user_id, email = _user()
    await _login(client, email)
    token = (await _stage(client, _pdf())).json()["token"]
    resp = await client.post(
        "/api/documents/combine",
        data={**_FIELDS, "kinds": ["records", "records"], "tokens": [token, token]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "File 2 is the same file as file 1. Remove one of them."
    assert _documents_of(user_id) == []


def _image(fmt: str, frames: int = 1) -> bytes:
    from PIL import Image

    frame = Image.new("RGB", (170, 220), "white")
    buffer = io.BytesIO()
    frame.save(buffer, fmt, save_all=frames > 1, append_images=[frame] * (frames - 1))
    return buffer.getvalue()


@pytest.mark.parametrize(("fmt", "frames"), [("JPEG", 1), ("PNG", 1), ("TIFF", 3), ("BMP", 1)])
async def test_a_scanned_image_is_taken_as_pdf_pages(client, tmp_path, fmt, frames):
    """DEMONSTRATES: a scan sent as an image is part of the record, as in Acrobat's Combine - every
    page of a multi-page TIFF."""
    user_id, email = _user()
    await _login(client, email)
    resp = await client.post(
        "/api/documents/combine/files",
        files={"pdf": (f"scan.{fmt.lower()}", _image(fmt, frames), "image/" + fmt.lower())},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["pages"] == frames
    [staged] = _staged_files(tmp_path, user_id)
    stored = Path(tmp_path) / str(user_id) / "staged" / staged
    assert stored.read_bytes().startswith(b"%PDF-")


async def test_a_record_joins_a_pdf_and_a_scanned_image(client):
    _, email = _user()
    await _login(client, email)
    tokens = [
        (await _stage(client, _pdf(2))).json()["token"],
        (
            await client.post(
                "/api/documents/combine/files",
                files={"pdf": ("scan.tif", _image("TIFF", 2), "image/tiff")},
            )
        ).json()["token"],
    ]
    resp = await client.post(
        "/api/documents/combine",
        data={**_FIELDS, "kinds": ["records", "records"], "tokens": tokens},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["page_count"] == 4


def _protected_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()
    return data


async def test_a_password_protected_pdf_is_named_as_such_and_not_kept(client, tmp_path):
    user_id, email = _user()
    await _login(client, email)
    resp = await _stage(client, _protected_pdf())
    assert resp.status_code == 400
    assert resp.json()["detail"] == (
        "This PDF needs a password to open. Save it without the password and add it again."
    )
    assert _staged_files(tmp_path, user_id) == []


async def test_the_preview_is_a_small_picture_of_the_first_page(client):
    _, email = _user()
    await _login(client, email)
    token = (await _stage(client, _text_pdf("PROOF OF SERVICE", pages=2))).json()["token"]

    resp = await client.get(f"/api/documents/combine/files/{token}/preview")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "no-store"
    picture = pymupdf.Pixmap(resp.content)
    assert picture.width == 600


@pytest.mark.parametrize(
    ("width_in", "height_in"),
    [(154, 200), (8.5, 400)],  # a huge page; a narrow, very tall one the height cap must hold
)
def test_a_page_declaring_a_huge_size_still_previews_small(tmp_path, width_in, height_in):
    """GUARD: some scans declare pages of 150 inches; the preview is held to its pixel size."""
    doc = pymupdf.open()
    doc.new_page(width=width_in * 72, height=height_in * 72)
    path = tmp_path / "huge.pdf"
    doc.save(path)
    picture = pymupdf.Pixmap(received_files.first_page_png(path))
    assert picture.width <= 600
    assert picture.height <= 1000


async def test_another_accounts_file_has_no_preview(client):
    _, owner_email = _user()
    await _login(client, owner_email)
    token = (await _stage(client, _pdf())).json()["token"]
    _, other_email = _user()
    await _login(client, other_email)

    resp = await client.get(f"/api/documents/combine/files/{token}/preview")
    assert resp.status_code == 404
    unknown = await client.get("/api/documents/combine/files/" + "a" * 32 + "/preview")
    assert unknown.status_code == 404


def test_a_file_that_is_neither_pdf_nor_image_is_unreadable(tmp_path):
    source = tmp_path / "upload.part"
    source.write_bytes(b"PK\x03\x04 a word document")
    with pytest.raises(received_files.UnreadableFile):
        received_files.make_pdf(source, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()
