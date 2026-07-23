"""Document-scoped JSON API (ported from the Flask documents_api blueprint).

All 15 routes: 13 landed in P3b; segment/start + summarize/start landed in P4b (they enqueue RQ
jobs via app.services.jobs, routed to the segment/summarize queues). Every id route depends on
get_owned_document -> 404 on a non-owner (IDOR guard). Handlers are sync `def` on the sync session
(get_db); FastAPI runs them in its threadpool, so the OCR/Vertex work in resummarize/bundle-summarize
blocks a worker thread, not the event loop. Logging is ids-only; original_filename is PHI, never
logged.
"""

import hashlib
import io
import logging
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_owned_document
from app.auth.deps import current_active_user
from app.config import get_settings
from app.db import get_db
from app.errors import EmptyExtractionError, OcrUnavailableError, PipelineError
from app.models import Document, Job, ReviewRow, Summary, User
from app.schemas.documents import (
    BundlePayload,
    ExportPayload,
    HeaderPayload,
    ResummarizePayload,
    RowsPayload,
    SummarizeStartPayload,
    SummaryEditPayload,
)
from app.services import bundles, catalog
from app.services.aggregate import merge_pdfs
from app.services.audit import audit
from app.services.extraction import extract_header
from app.services.files import safe_name
from app.services.gemini import PROMPT_VERSION
from app.services.jobs import JobConflict, enqueue
from app.services.linked_pdf import build_linked_pdf
from app.services.pdf import get_pdf_page_count
from app.services.reporting import DOCX_MIMETYPE, build_mrr_document
from app.services.rows import validate_rows
from app.services.summarize_engine import summarize_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pipeline_error_response(document_id: str, exc: PipelineError) -> JSONResponse:
    """Surface a sync-AI failure as a friendly message (never the raw vendor error). Log the
    technical detail server-side (ids only). OCR-unavailable is a server config problem (503);
    empty extraction is a property of the document (422)."""
    logger.warning("pipeline error on document %s: %s", document_id, exc)
    if isinstance(exc, OcrUnavailableError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(exc, EmptyExtractionError):
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return JSONResponse(status_code=code, content={"error": exc.user_message})


def _store_rows(session: Session, document: Document, rows) -> str | None:
    """Replace the document's ReviewRows with ``rows``; returns a 400 error string or None."""
    error = validate_rows(session, rows, document.page_count)
    if error:
        return error
    session.execute(delete(ReviewRow).where(ReviewRow.document_id == document.id))
    for idx, row in enumerate(rows):
        session.add(
            ReviewRow(
                document_id=document.id,
                idx=idx,
                start=int(row["start"]),
                end=int(row["end"]),
                category=str(row["category"]),
                title=str(row.get("title") or "-"),
                date=str(row.get("date") or "-"),
                injury_date=str(row.get("injury_date") or "-"),
                flag=str(row.get("flag") or "-"),
                suggest_merge=bool(row.get("suggest_merge")),
                include=bool(row.get("include", True)),
            )
        )
    session.commit()
    return None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_document(
    pdf: UploadFile | None = File(default=None),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    if pdf is None or not pdf.filename:
        raise HTTPException(status_code=400, detail="no PDF uploaded")

    document_id = str(uuid.uuid4())
    # Storage names are uuids: no collisions, and no patient-named filename in any path that
    # later shows up in logs or process listings.
    user_dir = os.path.join(get_settings().upload_folder, str(user.id))
    os.makedirs(user_dir, exist_ok=True)
    stored_path = os.path.join(user_dir, document_id + ".pdf")
    with open(stored_path, "wb") as out:
        while chunk := pdf.file.read(1 << 20):
            out.write(chunk)

    page_count = get_pdf_page_count(stored_path)
    if not page_count:
        os.remove(stored_path)
        raise HTTPException(status_code=400, detail="file is not a readable PDF")

    sha256 = _sha256(stored_path)
    # A duplicate upload is a WARNING, never a block: re-running a case is legitimate.
    duplicate = (
        session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.user_id == user.id, Document.sha256 == sha256)
        )
        > 0
    )

    document = Document(
        id=document_id,
        user_id=user.id,
        original_filename=safe_name(pdf.filename),
        stored_path=stored_path,
        sha256=sha256,
        page_count=page_count,
    )
    session.add(document)
    session.commit()
    audit(session, "upload", user.id, document_id)
    return {"id": document_id, "page_count": page_count, "sha256_duplicate": duplicate}


@router.post("/aggregate", status_code=status.HTTP_201_CREATED)
def aggregate_documents(
    pdfs: list[UploadFile] = File(default=[]),
    name: str = Form(default=""),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Individual-record upload: merge several pre-split PDFs into one Document, seed one ReviewRow
    per source file (by page range), and enqueue a classify job to auto-categorize them. The
    optional `name` becomes the record's display name (shown to its owner only)."""
    sources = [(safe_name(f.filename), f.file.read()) for f in pdfs if f.filename]
    if not sources:
        raise HTTPException(status_code=400, detail="no PDFs uploaded")
    merged, records = merge_pdfs(sources)
    if not records:
        raise HTTPException(status_code=400, detail="no readable PDFs uploaded")

    document_id = str(uuid.uuid4())
    user_dir = os.path.join(get_settings().upload_folder, str(user.id))
    os.makedirs(user_dir, exist_ok=True)
    stored_path = os.path.join(user_dir, document_id + ".pdf")
    with open(stored_path, "wb") as out:
        out.write(merged)
    page_count = records[-1]["end"]  # ranges tile, so the last end is the merged page count

    document = Document(
        id=document_id,
        user_id=user.id,
        original_filename=name.strip()[:512] or "aggregated-records.pdf",
        stored_path=stored_path,
        sha256=_sha256(stored_path),
        page_count=page_count,
    )
    session.add(document)
    # One row per source record (its page range); category defaults to general until the classify
    # job runs. Source filenames may be PHI, so they are NOT persisted as the title.
    for idx, record in enumerate(records):
        session.add(
            ReviewRow(
                document_id=document_id,
                idx=idx,
                start=record["start"],
                end=record["end"],
                category="100",
                title="-",
                date="-",
                injury_date="-",
                flag="-",
                include=True,
            )
        )
    session.commit()
    audit(session, "aggregate_upload", user.id, document_id)
    try:
        enqueue(
            session,
            document_id,
            "classify",
            model=get_settings().genai_model,
            prompt_version=PROMPT_VERSION,
            catalog_revision=catalog.catalog_version(session),
        )
    except JobConflict:
        pass  # a brand-new document cannot already have an active job; never fail the upload on it
    return {"id": document_id, "page_count": page_count, "records": records}


@router.get("")
def list_documents(
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    documents = session.scalars(
        select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())
    ).all()
    # One grouped count for the landing table (touching each document's rows would load every
    # full row set per request).
    counts = dict(
        session.execute(
            select(ReviewRow.document_id, func.count(ReviewRow.id))
            .join(Document, Document.id == ReviewRow.document_id)
            .where(Document.user_id == user.id)
            .group_by(ReviewRow.document_id)
        ).all()
    )
    return [doc.listing() | {"rows_count": counts.get(doc.id, 0)} for doc in documents]


@router.get("/{document_id}")
def get_document(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    payload = document.listing()
    payload["rows"] = [row.as_row() for row in document.review_rows]
    payload["categories"] = catalog.get_category_options(session)
    return payload


def _header_shape(data: dict) -> dict:
    """Map the extraction service's neutral keys onto the persisted header shape the FE uses."""
    return {
        "patient_first_name": data.get("first_name", ""),
        "patient_last_name": data.get("last_name", ""),
        "patient_dob": data.get("dob", ""),
        "law_firm": data.get("lawfirm", ""),
    }


@router.post("/{document_id}/extract-header")
def extract_header_route(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Re-extract {patient_first_name, patient_last_name, patient_dob, law_firm} from the record's
    first pages (Vertex) AND persist them onto the document, so a single detect is available
    everywhere (Review, Summaries, Export, bundles) without a separate Save. On a PipelineError
    nothing is persisted. Sync-AI: FastAPI runs this sync handler in its threadpool."""
    pages = list(range(1, min(15, document.page_count) + 1))
    try:
        data = extract_header(document.stored_path, pages)
    except PipelineError as exc:
        return _pipeline_error_response(document.id, exc)
    shape = _header_shape(data)
    document.patient_first_name = shape["patient_first_name"]
    document.patient_last_name = shape["patient_last_name"]
    document.patient_dob = shape["patient_dob"]
    document.law_firm = shape["law_firm"]
    session.commit()
    return shape


@router.put("/{document_id}/header")
def put_header(
    payload: HeaderPayload,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Persist the reviewer-edited report header on the document."""
    document.patient_first_name = payload.patient_first_name
    document.patient_last_name = payload.patient_last_name
    document.patient_dob = payload.patient_dob
    document.law_firm = payload.law_firm
    session.commit()
    return document.listing()


@router.delete("/{document_id}")
def delete_document(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    if document.active_job is not None:
        raise HTTPException(
            status_code=409, detail="a job is running for this document; wait for it"
        )
    stored_path = document.stored_path
    # The persisted id (a DB-sourced value, not the raw request path) is safe to log.
    document_uuid = document.id
    session.delete(document)  # cascades to jobs/rows/summaries
    session.commit()
    try:
        os.remove(stored_path)
    except OSError:
        logger.warning("could not remove stored file for document %s", document_uuid)
    audit(session, "delete", user.id, document_uuid)
    return {"ok": True}


@router.get("/{document_id}/pdf")
def get_pdf(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    audit(session, "view_pdf", user.id, document.id)
    # FileResponse serves conditional/range requests so the browser viewer can seek.
    return FileResponse(document.stored_path, media_type="application/pdf")


@router.get("/{document_id}/status")
def get_status(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    latest = session.scalars(
        select(Job).where(Job.document_id == document.id).order_by(Job.id.desc())
    ).first()
    return {"status": document.status, "job": latest.progress() if latest else None}


@router.put("/{document_id}/rows")
def put_rows(
    payload: RowsPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    if document.active_job is not None:
        # A finishing segment job would overwrite these rows; a summarize job is reading them.
        raise HTTPException(
            status_code=409, detail="a job is running for this document; wait for it"
        )
    rows = (payload.rows if payload else None) or []
    error = _store_rows(session, document, rows)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, "count": len(rows)}


@router.post("/{document_id}/segment/start")
def segment_start(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Enqueue a segmentation job on the `segment` queue. The DB one-active-job index -> 409."""
    try:
        enqueue(
            session,
            document.id,
            "segment",
            model=get_settings().genai_model,
            prompt_version=PROMPT_VERSION,
            catalog_revision=catalog.catalog_version(session),
        )
    except JobConflict:
        raise HTTPException(status_code=409, detail="a job is already running for this document")
    return {"ok": True}


@router.post("/{document_id}/summarize/start")
def summarize_start(
    payload: SummarizeStartPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Enqueue a summarization job on the `summarize` queue. Optionally flush the editor's final
    rows first; at least one row must be marked for inclusion."""
    payload = payload or SummarizeStartPayload()
    if payload.rows is not None:
        if document.active_job is not None:
            raise HTTPException(
                status_code=409, detail="a job is already running for this document"
            )
        error = _store_rows(session, document, payload.rows)
        if error:
            raise HTTPException(status_code=400, detail=error)
    if not any(row.include for row in document.review_rows):
        raise HTTPException(status_code=400, detail="no rows are marked for summarization")
    if payload.fresh:
        # "Re-summarize all": wipe prior summaries so the run regenerates every row (the resumable
        # worker otherwise reuses done rows by identity). Committed before enqueue so the worker
        # starts from a clean slate.
        session.execute(delete(Summary).where(Summary.document_id == document.id))
        session.commit()
    model = payload.model or get_settings().summary_model
    try:
        enqueue(
            session,
            document.id,
            "summarize",
            model=model,
            prompt_version=PROMPT_VERSION,
            catalog_revision=catalog.catalog_version(session),
        )
    except JobConflict:
        raise HTTPException(status_code=409, detail="a job is already running for this document")
    return {"ok": True}


@router.get("/{document_id}/summaries")
def get_summaries(document: Document = Depends(get_owned_document)):
    return [summary.listing() for summary in document.summaries]


@router.put("/{document_id}/summaries/{idx}")
def put_summary(
    idx: int,
    payload: SummaryEditPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Reviewer edits to one summary: title/date/text land in edited_* (the raw model output stays
    immutable - training data), excluded toggles export membership."""
    summary = session.scalar(
        select(Summary).where(Summary.document_id == document.id, Summary.idx == idx)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="not found")
    if document.active_job is not None and document.active_job.kind == "summarize":
        raise HTTPException(
            status_code=409, detail="summarization is rewriting these summaries; wait"
        )

    body = payload.model_dump(exclude_unset=True) if payload else {}
    for field, column, cap in (
        ("summaryTitle", "edited_title", 512),
        ("summaryDate", "edited_date", 16),
        ("summaryText", "edited_text", None),
    ):
        if field in body:
            value = str(body[field])
            setattr(summary, column, value[:cap] if cap else value)
    if "excluded" in body:
        summary.excluded = bool(body["excluded"])
    session.commit()
    return summary.listing()


@router.post("/{document_id}/summaries/{idx}/resummarize")
def resummarize(
    idx: int,
    payload: ResummarizePayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Re-run one summary from scratch: re-OCR its pages, re-summarize with its category prompt,
    replace the stored model output, and CLEAR the reviewer's edits. Synchronous."""
    summary = session.scalar(
        select(Summary).where(Summary.document_id == document.id, Summary.idx == idx)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="not found")
    if document.active_job is not None:
        raise HTTPException(
            status_code=409, detail="a job is running for this document; wait for it"
        )

    # Prefer the current review row (full, live metadata); fall back to the summary's snapshot.
    review_row = session.scalar(
        select(ReviewRow).where(
            ReviewRow.document_id == document.id,
            ReviewRow.start == summary.row_start,
            ReviewRow.end == summary.row_end,
        )
    )
    row = (
        review_row.as_row()
        if review_row is not None
        else {
            "start": summary.row_start,
            "end": summary.row_end,
            "category": summary.row_category,
            "date": summary.date,
            "injury_date": "-",
            "flag": "x" if summary.manual_check else "-",
        }
    )

    model = (payload.model if payload else None) or get_settings().summary_model
    prompt = catalog.get_prompt(session, "summary", str(row["category"]))
    try:
        output = summarize_row(document.stored_path, row, model, prompt=prompt)
    except PipelineError as exc:
        return _pipeline_error_response(document.id, exc)

    summary.title = output["summaryTitle"]
    summary.date = output.get("summaryDate") or "-"
    summary.text = output["summaryText"]
    summary.source_text = output.get("sourceText")
    summary.manual_check = bool(output.get("manualCheck"))
    summary.row_start = int(row["start"])
    summary.row_end = int(row["end"])
    summary.row_category = str(row["category"])
    # Fresh model output supersedes the prior hand-edits for this row.
    summary.edited_title = None
    summary.edited_date = None
    summary.edited_text = None
    session.commit()
    audit(session, "resummarize", user.id, document.id)
    return summary.listing()


# A trailing engine-style page suffix; en dash included because the web view displays ranges with
# one. Possessive quantifiers are backtrack-free and there is no leading \s*, so re.search runs in
# linear time (ReDoS-safe, Sonar S5852).
_PAGES_SUFFIX = re.compile(r"\(pages\s++\d++\s*+[-–]\s*+\d++\)\s*+$", re.IGNORECASE)
_BUNDLE_NAME_CHARS = re.compile(r"[^a-z0-9]+")


def _export_title_and_text(summary: Summary) -> tuple[str, str]:
    """Shared export title + body used by BOTH the Word and linked-PDF entries (so the two stay
    identical). Strips the internal [ManualCheck] review flag - dropped from exports because a
    finished report/PDF cannot be edited to remove it, though it stays visible in the app - and the
    stale page suffix, then re-applies [Diagnostic Study] + (Pages X-Y) and prepends the DOI."""
    title = re.sub(r"^\[ManualCheck\]\s*", "", summary.effective_title().strip())
    title = _PAGES_SUFFIX.sub("", title).rstrip()
    if str(summary.row_category) == "3" and "[Diagnostic Study]" not in title:
        title = f"{title} [Diagnostic Study]"
    title = f"{title} (Pages {summary.row_start}-{summary.row_end})"
    text = summary.effective_text()
    doi = re.match(r"\s*(\*\*DOI\*\*:[^,]*,)", summary.text or "")
    if doi and "**DOI**" not in text:
        text = f"{doi.group(1)} {text}"
    return title, text


def _export_entry(summary: Summary) -> dict:
    """One docx entry; the [ManualCheck] review flag is dropped from exports (see
    _export_title_and_text)."""
    title, text = _export_title_and_text(summary)
    return {
        "summaryDate": summary.effective_date(),
        "summaryTitle": title,
        "summaryText": text,
    }


def _pdf_entry(summary: Summary) -> dict:
    """Linked-PDF entry: like _export_entry, plus ``startPage`` (the 1-based source page the title
    links to)."""
    title, text = _export_title_and_text(summary)
    return {
        "summaryDate": summary.effective_date(),
        "linkTitle": title,
        "summaryText": text,
        "startPage": summary.row_start,
    }


def _download_name(label: str | None, ext: str) -> str:
    """Safe download filename from a free-text label ('Diagnostic & Operative' -> ...)."""
    slug = _BUNDLE_NAME_CHARS.sub("-", (label or "records").lower()).strip("-") or "records"
    return f"{slug}.{ext}"


def _summary_filename(document: Document) -> str:
    """Lastname_Firstname_Medical_Records_summary.docx from the persisted header; falls back to
    <original-filename>_summary.docx when no patient name was extracted."""
    parts = [
        p.strip()
        for p in (document.patient_last_name, document.patient_first_name)
        if (p or "").strip()
    ]
    if parts:
        base = "_".join([*parts, "Medical_Records_summary"])
    else:
        stem = os.path.splitext(os.path.basename(document.original_filename or "summaries"))[0]
        base = f"{stem}_summary"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_") or "summaries"
    return f"{safe}.docx"


def _linked_filename(document: Document) -> str:
    """Lastname_Firstname_Medical_Records_linked.pdf from the persisted header; falls back to
    <original-filename>_linked.pdf when no patient name was extracted."""
    parts = [
        p.strip()
        for p in (document.patient_last_name, document.patient_first_name)
        if (p or "").strip()
    ]
    if parts:
        base = "_".join([*parts, "Medical_Records_linked"])
    else:
        stem = os.path.splitext(os.path.basename(document.original_filename or "record"))[0]
        base = f"{stem}_linked"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_") or "record"
    return f"{safe}.pdf"


def _matched_rows(session: Session, document: Document, categories):
    """The current review rows whose category is in the requested set, or raise: empty/invalid
    categories -> 400; a set that matches nothing in this record -> 409."""
    if not isinstance(categories, list) or not categories:
        raise HTTPException(status_code=400, detail="categories must be a non-empty list")
    rows = [
        row.as_row()
        for row in session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == document.id).order_by(ReviewRow.idx)
        ).all()
    ]
    matched = bundles.matched_rows(rows, categories)
    if not matched:
        raise HTTPException(status_code=409, detail="no matching documents in this record")
    return matched


@router.post("/{document_id}/export")
def export_document(
    payload: ExportPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    payload = payload or ExportPayload()
    included = [s for s in document.summaries if not s.excluded]
    if not included:
        raise HTTPException(status_code=409, detail="no summaries to export yet")
    entries = [_export_entry(s) for s in included]
    docx = build_mrr_document(
        entries,
        document.page_count,
        payload.patientName,
        payload.patientdob,
        payload.QMEorAME,
        payload.lawfirm,
    )
    buffer = io.BytesIO()
    docx.save(buffer)
    buffer.seek(0)
    audit(session, "export", user.id, document.id)
    return StreamingResponse(
        buffer,
        media_type=DOCX_MIMETYPE,
        headers={"Content-Disposition": f'attachment; filename="{_summary_filename(document)}"'},
    )


@router.post("/{document_id}/export/pdf")
def export_document_pdf(
    payload: ExportPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Combined linked PDF: the summary letter (two-column, blue linked titles) followed by the
    full source record, each title linking to that sub-document's first source page."""
    payload = payload or ExportPayload()
    included = [s for s in document.summaries if not s.excluded]
    if not included:
        raise HTTPException(status_code=409, detail="no summaries to export yet")
    entries = [_pdf_entry(s) for s in included]
    pdf_bytes = build_linked_pdf(
        document.stored_path,
        entries,
        document.page_count,
        payload.patientName,
        payload.patientdob,
        payload.QMEorAME,
        payload.lawfirm,
    )
    audit(session, "export_pdf", user.id, document.id)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{_linked_filename(document)}"'},
    )


@router.post("/{document_id}/bundle/pdf")
def bundle_pdf(
    payload: BundlePayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Combine the category-matched documents' pages into one downloadable PDF (no LLM)."""
    payload = payload or BundlePayload()
    matched = _matched_rows(session, document, payload.categories)
    buffer = bundles.build_bundle_pdf(document.stored_path, matched)
    audit(session, "bundle_pdf", user.id, document.id)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_name(payload.label, "pdf")}"'
        },
    )


@router.post("/{document_id}/bundle/summarize")
def bundle_summarize(
    payload: BundlePayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Summarize just the category-matched documents into a filtered Word report (synchronous,
    bounded by BUNDLE_SUMMARIZE_CAP; larger records route to the main Summaries flow)."""
    payload = payload or BundlePayload()
    matched = _matched_rows(session, document, payload.categories)
    cap = get_settings().bundle_summarize_cap
    if len(matched) > cap:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(matched)} matching documents exceeds the on-demand limit of {cap}; "
                "use the main Summaries flow for a record this large"
            ),
        )
    model = payload.model or get_settings().summary_model
    try:
        entries = bundles.bundle_summary_entries(
            document.stored_path,
            matched,
            model,
            prompt_for=lambda row: catalog.get_prompt(session, "summary", str(row["category"])),
        )
    except PipelineError as exc:
        return _pipeline_error_response(document.id, exc)
    docx = build_mrr_document(
        entries,
        document.page_count,
        payload.patientName,
        payload.patientdob,
        payload.QMEorAME,
        payload.lawfirm,
    )
    buffer = io.BytesIO()
    docx.save(buffer)
    buffer.seek(0)
    audit(session, "bundle_summarize", user.id, document.id)
    return StreamingResponse(
        buffer,
        media_type=DOCX_MIMETYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{_download_name(payload.label, "docx")}"'
        },
    )
