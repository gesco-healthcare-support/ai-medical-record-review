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
from rq.command import send_stop_job_command
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_owned_document
from app.auth.deps import current_active_user
from app.config import get_settings
from app.db import get_db
from app.errors import EmptyExtractionError, OcrUnavailableError, PipelineError
from app.models import Document, Job, ReviewRow, Summary, User
from app.schemas.documents import (
    BundlePayload,
    CancelPayload,
    DedupStartPayload,
    DuplicateResolvePayload,
    ExportPayload,
    HeaderPayload,
    ResummarizePayload,
    RowsPayload,
    SegmentStartPayload,
    SummarizeStartPayload,
    SummaryEditPayload,
)
from app.services import bundles, catalog
from app.services.aggregate import merge_pdfs
from app.services.audit import audit
from app.services.extraction import extract_header
from app.services.files import safe_name
from app.services.gemini import PROMPT_VERSION
from app.services.jobs import ACTIVE_STATES, JobConflict, enqueue
from app.services.linked_pdf import build_linked_pdf
from app.services.pdf import get_pdf_page_count
from app.services.reporting import DOCX_MIMETYPE, build_mrr_document
from app.services.rows import validate_rows
from app.services.summarize_engine import (
    presentable_title,
    standalone_studies_from_rows,
    summarize_row,
)
from app.services.summary_doi import doi_prefix
from app.worker.cancel import request_cancel
from app.worker.queues import get_redis

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


def _store_rows(session: Session, document: Document, rows) -> tuple[str | None, dict]:
    """Replace the document's ReviewRows with ``rows``; returns ``(error, stats)``.

    ``stats`` counts the reviewer's boundary work - rows before and after, and boundaries removed
    (merges) or added (splits). It exists because this function DELETES the row set rather than
    updating it, so nothing survives afterwards from which the edit could be reconstructed; the
    caller audits it. The previous boundary set is already materialised below to preserve the dedup
    fields, so the counts are free. Empty dict when validation failed and nothing was written.
    """
    error = validate_rows(session, rows, document.page_count)
    if error:
        return error, {}
    # The editor payload carries no dedup fields, so a plain delete+recreate would wipe the
    # duplicate clustering on every autosave. Carry them across by page range: the same (start, end)
    # is the same pages, hence the same OCR text the grouping was computed from. A row whose range
    # changed (merge/split/boundary edit) is genuinely different content and correctly starts fresh.
    preserved = {
        (row.start, row.end): (
            row.source_text,
            row.dupe_group,
            row.dupe_primary,
            row.dupe_dismissed,
            row.dupe_similarity,
        )
        for row in document.review_rows
    }
    # Editing ONE copy's boundaries reopens the whole duplicate question, so the surviving copies drop
    # their dismissal too. Otherwise the eroded cluster still looks like an intact dismissed one and a
    # re-check would silently re-dismiss a set the reviewer never judged.
    incoming_ranges = {(int(row["start"]), int(row["end"])) for row in rows}
    reopened_groups = {
        group
        for (start, end), (_text, group, _primary, _dismissed, _sim) in preserved.items()
        if group is not None and (start, end) not in incoming_ranges
    }
    # Count the boundary work before the rows are gone. A boundary is a row's last page, so a
    # boundary the reviewer dropped is a merge and one they introduced is a split. Computed on
    # sets rather than by pairing rows, because a merge renumbers every row after it.
    before_ends = {row.end for row in document.review_rows}
    after_ends = {end for _start, end in incoming_ranges}
    stats = {
        "before": len(document.review_rows),
        "after": len(rows),
        "merges": len(before_ends - after_ends),
        "splits": len(after_ends - before_ends),
    }
    session.execute(delete(ReviewRow).where(ReviewRow.document_id == document.id))
    for idx, row in enumerate(rows):
        start, end = int(row["start"]), int(row["end"])
        source_text, dupe_group, dupe_primary, dupe_dismissed, dupe_similarity = preserved.get(
            (start, end), (None, None, False, False, None)
        )
        if dupe_group in reopened_groups:
            dupe_dismissed = False
        session.add(
            ReviewRow(
                document_id=document.id,
                idx=idx,
                start=start,
                end=end,
                category=str(row["category"]),
                title=str(row.get("title") or "-"),
                date=str(row.get("date") or "-"),
                injury_date=str(row.get("injury_date") or "-"),
                flag=str(row.get("flag") or "-"),
                suggest_merge=bool(row.get("suggest_merge")),
                include=bool(row.get("include", True)),
                source_text=source_text,
                dupe_group=dupe_group,
                dupe_primary=dupe_primary,
                dupe_dismissed=dupe_dismissed,
                dupe_similarity=dupe_similarity,
            )
        )
    session.commit()
    # Snapshotting above LOADED document.review_rows, so the collection is cached and now stale
    # (its rows were deleted and replaced). Expire it so callers that read it next - e.g.
    # summarize_start's "at least one row is included" check - see the rows just written.
    session.expire(document, ["review_rows"])
    return None, stats


def _rows_edit_detail(stats: dict) -> str:
    """Non-PHI audit detail for a row-set save: counts only, never a title or a date."""
    return (
        f"rows {stats['before']}->{stats['after']} "
        f"(merges {stats['merges']}, splits {stats['splits']})"
    )


def _apply_row_category(
    session: Session, document: Document, summary: Summary, category: str, user: User
) -> None:
    """Re-classify the ReviewRow behind ``summary``. Raises the 4xx itself; commits nothing.

    Three deliberate choices:

    ANY active job is refused, not just a summarize one. The edit fields in put_summary only touch the
    Summary, so a segment job cannot disturb them; this write lands on a ReviewRow, and a finishing
    segment job replaces the entire row set via _store_rows - the edit would vanish with no error.

    A missing row is a 409, not a partial write. Writing only summary.row_category would leave Review
    & correct and Summaries permanently disagreeing about what this sub-document is; an error the
    reviewer can act on by re-segmenting is the better failure.

    summary.row_category is left ALONE. It is the snapshot of the category that generated the current
    text, and the gap between it and the row is exactly what tells the reviewer to re-draft. Updating
    it here would erase the signal in the act of creating it.
    """
    if document.active_job is not None:
        raise HTTPException(
            status_code=409,
            detail="a job is running for this document; wait for it before changing the category",
        )
    if category not in catalog.get_category_ids(session, active_only=True):
        raise HTTPException(status_code=400, detail="unknown category")

    review_row = session.scalar(
        select(ReviewRow).where(
            ReviewRow.document_id == document.id,
            ReviewRow.start == summary.row_start,
            ReviewRow.end == summary.row_end,
        )
    )
    if review_row is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "this summary's sub-document boundaries changed; re-run the segment check before "
                "changing its category"
            ),
        )
    if review_row.category == category:
        return  # no-op: do not spend an audit row saying nothing happened

    previous = review_row.category
    review_row.category = category
    session.commit()
    audit(
        session,
        "summary.category",
        user.id,
        document.id,
        detail=f"idx {summary.idx} pages {summary.row_start}-{summary.row_end}: "
        f"{previous} -> {category}",
    )


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
                # category 100 (General) seeds to unchecked; classify_document re-derives per row.
                include=catalog.summarize_default_for(session, "100"),
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


def _dupe_groups(document: Document) -> dict[int, list[ReviewRow]]:
    """Confirmed duplicate clusters keyed by group id (rows with a non-null dupe_group).

    Groups with a single surviving member are dropped: a "duplicate" of one is meaningless, and a
    boundary edit can orphan a member (its row starts fresh - see _store_rows). Filtering here, the
    one read path both the Duplicates tab and the unreviewed count use, keeps the guard in one place
    without mutating rows; a dedup re-run reclusters from scratch anyway.
    """
    groups: dict[int, list[ReviewRow]] = {}
    for row in document.review_rows:
        if row.dupe_group is not None:
            groups.setdefault(row.dupe_group, []).append(row)
    return {group: rows for group, rows in groups.items() if len(rows) >= 2}


def _unreviewed_dupe_count(groups: dict[int, list[ReviewRow]]) -> int:
    """Clusters where 2+ copies would still be summarized and which are not dismissed - the advisory
    count that drives the non-blocking 'you have duplicates to review' hint.

    Inclusion, not the primary mark, is the test: a keep-one resolution excludes the other copies, so
    the cluster stops being advised even after a dedup re-run recomputes its group. A cluster that
    later gains another included copy is correctly advised again.
    """
    return sum(
        1
        for members in groups.values()
        if not any(m.dupe_dismissed for m in members) and sum(1 for m in members if m.include) >= 2
    )


def _dupe_date_key(date: str | None) -> tuple[int, int, int]:
    """Sort key for MM/DD/YYYY dates (unknown/"-" sorts last), so a cluster lists copies oldest-first."""
    match = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", date or "")
    if not match:
        return (9999, 12, 31)
    month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return (year + 2000 if year < 100 else year, month, day)


@router.get("/{document_id}/status")
def get_status(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    latest = session.scalars(
        select(Job).where(Job.document_id == document.id).order_by(Job.id.desc())
    ).first()
    return {
        "status": document.status,
        "job": latest.progress() if latest else None,
        # Advisory only - the FE badges/notices this but Summarize is never blocked on it.
        "unreviewed_duplicate_groups": _unreviewed_dupe_count(_dupe_groups(document)),
    }


@router.get("/{document_id}/duplicates")
def get_duplicates(
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """The confirmed duplicate clusters (each: rows sorted oldest-first) plus the latest dedup job's
    progress, so the Duplicates tab can show 'checking...' while the background job runs."""
    groups = _dupe_groups(document)
    clusters = []
    for group_id in sorted(groups):
        members = sorted(groups[group_id], key=lambda r: _dupe_date_key(r.date))
        clusters.append(
            {
                "group": group_id,
                "dismissed": any(m.dupe_dismissed for m in members),
                # Every member carries the cluster's score; None for rows grouped before the column
                # existed. ~1.0 = re-scans of one document, low = a recurring form series.
                "similarity": next(
                    (m.dupe_similarity for m in members if m.dupe_similarity is not None), None
                ),
                "rows": [
                    {
                        "idx": m.idx,
                        "title": m.title,
                        "date": m.date,
                        "pages": {"start": m.start, "end": m.end},
                        "include": m.include,
                        "primary": m.dupe_primary,
                    }
                    for m in members
                ],
            }
        )
    dedup_job = session.scalars(
        select(Job)
        .where(Job.document_id == document.id, Job.kind == "dedup")
        .order_by(Job.id.desc())
    ).first()
    # The newest job drives PROGRESS - "checking (37/84)", and the error when a run failed. It must
    # not drive what has been CHECKED, because the two answer different questions once a re-check
    # ends badly. `dedup_document` rewrites the grouping in a single transaction at the very end,
    # precisely so a run that dies leaves the previous clusters intact rather than emptying the tab -
    # so after a cancelled or errored re-check the stored clusters still come from the last COMPLETED
    # run, and the flags have to describe that run.
    #
    # Reading them off the newest job instead reported a record with four stored clusters as never
    # checked: the tab rendered "No duplicate check has run on this record yet" directly above four
    # groups it was asking the reviewer to resolve, dropped the "N sub-documents could not be read"
    # warning that the earlier run had earned, and dropped the "boundaries changed" nudge while the
    # edits that made it true were still in place.
    last_completed = session.scalars(
        select(Job)
        .where(Job.document_id == document.id, Job.kind == "dedup", Job.state == "done")
        .order_by(Job.id.desc())
    ).first()
    # "stale" = the clusters no longer cover every row dedup would look at, so the tab can offer a
    # MANUAL re-check (never an automatic AI run). A completed dedup stores source_text on every row
    # IN SCOPE, and a metadata edit keeps it (_store_rows), so a missing one means a boundary changed,
    # a row appeared, or a row was newly included since that run. While a dedup is in flight there is
    # nothing to nudge.
    #
    # Scope is include=True, matching dedup_document. This filter is load-bearing rather than tidy:
    # an excluded row is never OCR'd, so without it source_text stays None forever and the tab would
    # offer a re-check that could not possibly change anything. A DISMISSED row is not an excluded one
    # - dismissing says "not duplicates", not "do not summarize" - so it stays in scope and still
    # counts.
    in_scope = [row for row in document.review_rows if row.include]
    stale = bool(last_completed and any(r.source_text is None for r in in_scope))
    # Sub-documents a completed check could not read. Their text is empty, and empty text matches
    # nothing (the Jaccard signature is a null set), so they were not compared against anything - a
    # run that could not read a fifth of the record is not a clean bill of health and must not
    # present as one. Derived, so no column and no migration: "" means read-and-textless, None means
    # never attempted - which `stale` does NOT cover, despite what this comment used to claim: `stale`
    # is itself gated on a completed dedup_job, so on a document nothing has checked both it and this
    # are falsy and the tab had nothing to tell "checked, clean" from "never checked". Hence `checked`.
    # Same scope as `stale` above, so the two derived values cannot disagree about what dedup looked
    # at. An excluded row has no text at all rather than empty text, so it could not be counted here
    # anyway; filtering explicitly keeps that true if the storage rule ever changes.
    unreadable = (
        sum(1 for r in in_scope if r.source_text is not None and not r.source_text.strip())
        if last_completed
        else 0
    )
    return {
        "clusters": clusters,
        "job": dedup_job.progress() if dedup_job else None,
        "stale": stale,
        "unreadable": unreadable,
        # Has a duplicate check ever COMPLETED on this document? Empty clusters mean two entirely
        # different things and the tab was presenting both as the same one: a completed run that found
        # nothing, and no run at all. The second is the common case, because dedup is gated behind the
        # review phase - measured 2026-08-19 on four records taken end to end, none of which had a
        # dedup job, while the tab told the reviewer "No duplicate documents found". Two of those
        # records have a human deliverable that counts 6 and 2 pages of duplicate copies, so the tab
        # was affirmatively wrong, not merely silent.
        #
        # A job that exists but errored or was cancelled is not itself a check - but a COMPLETED run
        # before it still is, and its clusters are still stored, so this asks whether any dedup has
        # ever finished rather than how the newest one ended.
        "checked": last_completed is not None,
    }


@router.post("/{document_id}/dedup/start")
def dedup_start(
    payload: DedupStartPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Manually (re)run duplicate clustering (it also runs automatically after identify). 409 if a
    job is already active for this document.

    ``fresh`` clears every row's stored ``source_text`` first, so the run re-OCRs instead of reusing
    the previous extraction. That is the meaningful difference between Start over and Continue here:
    the OCR is the expensive part, and reusing it is what makes a continue nearly free."""
    if payload is not None and payload.fresh:
        session.execute(
            update(ReviewRow).where(ReviewRow.document_id == document.id).values(source_text=None)
        )
        session.commit()
    try:
        enqueue(
            session,
            document.id,
            "dedup",
            model=get_settings().classify_model,
            prompt_version=PROMPT_VERSION,
            catalog_revision=catalog.catalog_version(session),
        )
    except JobConflict:
        raise HTTPException(status_code=409, detail="a job is already running for this document")
    return {"ok": True}


def _leave_cluster(session: Session, row: ReviewRow) -> None:
    """Take ``row`` out of its duplicate cluster and put it back in the report on its own terms.

    Restoring `include` is the load-bearing part. `keep_one` sets `include = is_primary and wanted`,
    so every non-primary copy is left unchecked - correct while they ARE copies. "Not a duplicate"
    then cleared the group and never touched `include`, and because a row with no `dupe_group` is
    invisible to `_dupe_groups`, the copy disappeared from the Duplicates tab while staying excluded
    from the report. So a reviewer who kept one copy, read further, and then declared a third copy a
    distinct document silently dropped those pages: the cluster reads "Resolved", nothing on the
    duplicates surface mentions the row, and the only trace is an unchecked box on an unrelated-looking
    row in the editor. The test asserting the opposite passed because its fixture seeds `include=True`
    and never runs keep_one first.

    The category default rather than a flat True, for the reason `keep_one` gives two branches up:
    three copies of a routing slip are category 100, which is unchecked by default, and forcing one on
    would push paperwork nobody asked for into the report. A row leaving a cluster is a standalone
    document again, so it gets exactly what a freshly classified standalone row of its category gets -
    which is what `classify_document` assigns too.
    """
    row.dupe_group = None
    row.dupe_primary = False
    row.dupe_dismissed = False
    row.include = catalog.summarize_default_for(session, row.category)


@router.post("/{document_id}/duplicates/{group}/resolve")
def resolve_duplicate(
    group: int,
    payload: DuplicateResolvePayload,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Resolve one cluster: keep_one (mark the primary, exclude the rest) or dismiss (not duplicates)."""
    members = [r for r in document.review_rows if r.dupe_group == group]
    if not members:
        raise HTTPException(status_code=404, detail="no such duplicate group")
    if document.active_job is not None:
        raise HTTPException(
            status_code=409, detail="a job is running for this document; wait for it"
        )
    if payload.action == "keep_one":
        primary = next((m for m in members if m.idx == payload.primary_idx), None)
        if primary is None:
            raise HTTPException(status_code=400, detail="primary_idx is not in this cluster")
        # Keeping a copy must not RAISE inclusion above what the cluster already had: three copies of
        # a routing slip are category 100, which is unchecked by default, and turning the kept one on
        # would put paperwork nobody asked for into the report. The cluster's existing intent moves
        # onto the kept copy - so an all-excluded cluster stays excluded, and a normal cluster still
        # produces exactly one summary.
        wanted = any(m.include for m in members)
        for member in members:
            is_primary = member.idx == payload.primary_idx
            member.dupe_primary = is_primary
            member.dupe_dismissed = False
            member.include = is_primary and wanted
    elif payload.action == "dismiss":
        for member in members:
            member.dupe_dismissed = True
            member.dupe_primary = False
    elif payload.action == "remove_member":
        target = next((m for m in members if m.idx == payload.idx), None)
        if target is None:
            raise HTTPException(status_code=400, detail="idx is not in this cluster")
        # Leaving the group outright, rather than a per-row dismissed flag: `dismissed` is derived
        # cluster-wide with any(...), so a per-row flag would make it ambiguous everywhere it is read.
        _leave_cluster(session, target)
        remaining = [m for m in members if m is not target]
        if len(remaining) < 2:
            # A cluster of one is not a duplicate set. _dupe_groups already hides it on read; clear it
            # here too so the stored state matches what every surface shows.
            for member in remaining:
                _leave_cluster(session, member)
    else:
        raise HTTPException(
            status_code=400,
            detail="action must be 'keep_one', 'dismiss' or 'remove_member'",
        )
    session.commit()
    return {"ok": True}


@router.put("/{document_id}/rows")
def put_rows(
    payload: RowsPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    if document.active_job is not None:
        # A finishing segment job would overwrite these rows; a summarize job is reading them.
        raise HTTPException(
            status_code=409, detail="a job is running for this document; wait for it"
        )
    rows = (payload.rows if payload else None) or []
    error, stats = _store_rows(session, document, rows)
    if error:
        raise HTTPException(status_code=400, detail=error)
    # Audited even when nothing changed: "the reviewer opened this and confirmed it" is a different
    # fact from "nobody has looked at it", and only the event can tell them apart - the rows carry
    # no timestamp of their own because _store_rows recreates them.
    audit(session, "rows.edit", user.id, document.id, detail=_rows_edit_detail(stats))
    return {"ok": True, "count": len(rows)}


@router.post("/{document_id}/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    payload: CancelPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Ask a job to stop. Cooperative by default; ``force`` also kills the RQ work-horse.

    Terminal jobs are a 200 NO-OP, not a 409. A job can finish between the reviewer's click and this
    request arriving - that race is normal, and answering it with an error would surface a scary
    message for a stop that simply arrived a moment late.
    """
    job = session.get(Job, job_id)
    if job is None or job.document_id != document.id:
        raise HTTPException(status_code=404, detail="not found")

    # The UI needs to know when to offer "Force stop", and that number must come from the SERVER:
    # hardcoding it in the client would let JOB_CANCEL_GRACE_SECONDS drift from the moment the button
    # actually changes, so the setting would be a lie.
    grace = {"graceSeconds": get_settings().job_cancel_grace_seconds}

    force = bool(payload.force) if payload else False
    if job.state not in ACTIVE_STATES:
        return {**job.progress(), **grace}  # already terminal: nothing to ask

    job.cancel_requested = True
    session.commit()
    request_cancel(job.id)  # the signal the retry backoff can see without a session

    if force:
        try:
            send_stop_job_command(get_redis(), job.rq_job_id or str(job.id))
        except Exception:
            # No work-horse to stop (already exited), or Redis is down. The cooperative flag and the
            # DB row are already set, and orphan recovery reaps whatever is left, so this is not a
            # failure worth returning to a reviewer who just asked for a stop.
            logger.info("force stop could not be delivered for job %s", job_id, exc_info=True)

    audit(
        session,
        "job.cancel",
        user.id,
        document.id,
        detail=f"job {job.id} kind {job.kind} state {job.state} force {force}",
    )
    return {**job.progress(), **grace}


@router.post("/{document_id}/segment/start")
def segment_start(
    payload: SegmentStartPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
):
    """Enqueue a segmentation job on the `segment` queue. The DB one-active-job index -> 409.

    ``fresh`` is accepted now and is currently a no-op WITHOUT pretending otherwise: segmentation keeps
    no checkpoints yet, so every run already recomputes every window. It exists so the UI can offer
    the same Continue / Start over pair for all four kinds, and the checkpoint-clearing half arrives
    with the checkpoint table itself."""
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
    user: User = Depends(current_active_user),
):
    """Enqueue a summarization job on the `summarize` queue. Optionally flush the editor's final
    rows first; at least one row must be marked for inclusion."""
    payload = payload or SummarizeStartPayload()
    if payload.rows is not None:
        if document.active_job is not None:
            raise HTTPException(
                status_code=409, detail="a job is already running for this document"
            )
        error, stats = _store_rows(session, document, payload.rows)
        if error:
            raise HTTPException(status_code=400, detail=error)
        # The same reviewer edit surface as put_rows - the editor flushes its final row set through
        # here on "Summarize". Auditing only put_rows would undercount the boundary work.
        audit(session, "rows.edit", user.id, document.id, detail=_rows_edit_detail(stats))
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


def _summary_response(document: Document, summary: Summary) -> dict:
    """One summary's listing plus ``rowCategoryLive``: the CURRENT category of the row behind it.

    ``listing()["row"]["category"]`` is the snapshot of what generated the text; this is what the row
    says NOW. The Summaries tab compares the two to flag a summary as needing a re-draft, and the
    comparison has to be against what is SAVED - joining client-side against the editor's in-memory
    rows would warn about an unsaved edit the reviewer cannot see from that tab.

    ``None`` when no row covers the summary's stored page range (boundaries were re-segmented): there
    is no live category to compare, so the UI must not claim a mismatch.

    EVERY route that returns a summary must go through this, not bare ``listing()``. The client patches
    its cache with whatever a mutation returns (``useSummaryPatch`` replaces the item wholesale), so a
    single endpoint answering with the un-enriched shape silently deletes this field from the cache -
    which is exactly how the badge failed to appear after a successful save. Built here rather than in
    ``listing()`` because that is a pure model method with no session.
    """
    live = {(row.start, row.end): row.category for row in document.review_rows}
    return {
        **summary.listing(),
        "rowCategoryLive": live.get((summary.row_start, summary.row_end)),
    }


@router.get("/{document_id}/summaries")
def get_summaries(document: Document = Depends(get_owned_document)):
    return [_summary_response(document, summary) for summary in document.summaries]


@router.put("/{document_id}/summaries/{idx}")
def put_summary(
    idx: int,
    payload: SummaryEditPayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Reviewer edits to one summary: title/date/text land in edited_* (the raw model output stays
    immutable - training data), excluded toggles export membership.

    ``category`` is the odd one out and is handled first: it re-classifies the sub-document, so it is
    written to the owning ReviewRow rather than to the Summary, and it carries a STRICTER job guard
    than the edit fields below (see _apply_row_category)."""
    summary = session.scalar(
        select(Summary).where(Summary.document_id == document.id, Summary.idx == idx)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="not found")

    body = payload.model_dump(exclude_unset=True) if payload else {}
    if "category" in body:
        _apply_row_category(session, document, summary, str(body.pop("category")), user)

    if document.active_job is not None and document.active_job.kind == "summarize":
        raise HTTPException(
            status_code=409, detail="summarization is rewriting these summaries; wait"
        )

    changed = []
    for field, column, cap in (
        ("summaryTitle", "edited_title", 512),
        ("summaryDate", "edited_date", 16),
        ("summaryText", "edited_text", None),
    ):
        if field in body:
            value = str(body[field])
            setattr(summary, column, value[:cap] if cap else value)
            changed.append(column)
    if "excluded" in body:
        summary.excluded = bool(body["excluded"])
        changed.append("excluded")
    session.commit()
    if changed:
        # A LENGTH delta, never the text: this column is read by humans and must stay free of PHI,
        # and the delta is what makes the edit an effort measurement rather than a bare event.
        delta = len(summary.edited_text or "") - len(summary.text or "")
        audit(
            session,
            "summary.edit",
            user.id,
            document.id,
            detail=f"idx {summary.idx} pages {summary.row_start}-{summary.row_end}: "
            f"{'+'.join(changed)}"
            + (f", body chars {delta:+d}" if "edited_text" in changed else ""),
        )
    # _summary_response, not listing(): a category write changed a ReviewRow, so review_rows is stale
    # on this identity-mapped document. Expire it or the response echoes the PRE-change category and
    # the client patches its cache with a value that is already wrong.
    session.expire(document, ["review_rows"])
    return _summary_response(document, summary)


@router.post("/{document_id}/summaries/{idx}/resummarize")
def resummarize(
    idx: int,
    payload: ResummarizePayload | None = None,
    document: Document = Depends(get_owned_document),
    session: Session = Depends(get_db),
    user: User = Depends(current_active_user),
):
    """Re-run one summary: re-summarize its pages with its CURRENT category's prompt, replace the
    stored model output, and CLEAR the reviewer's edits. Synchronous.

    It does NOT necessarily re-OCR. ``as_row()`` carries the row's stored ``source_text`` (the duplicate
    check's extraction of exactly these pages) and ``summarize_row`` reuses it when non-blank, which is
    the point - re-OCRing a long row is minutes of work for the same bytes. Fresh OCR happens only when
    that text is blank or absent. This docstring claimed "re-OCR its pages" until 2026-07-31, which is
    how a hand-seeded row whose source_text did not match its pages produced a summary of the WRONG
    text with no error: legitimate reuse, misleading documentation."""
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
    # E-08 document-set context: the record's OTHER standalone diagnostic studies, so a document
    # carrying a records review does not restate a study summarized in its own right elsewhere. Only
    # the included rows count - an unchecked study is summarized nowhere, so suppressing it here would
    # drop it from the record entirely.
    studies = standalone_studies_from_rows(
        [
            other.as_row()
            for other in session.scalars(
                select(ReviewRow).where(
                    ReviewRow.document_id == document.id,
                    ReviewRow.include.is_(True),
                    ReviewRow.category == "3",
                )
            ).all()
        ],
        exclude=row,
    )
    try:
        output = summarize_row(
            document.stored_path, row, model, prompt=prompt, standalone_studies=studies
        )
    except PipelineError as exc:
        return _pipeline_error_response(document.id, exc)

    summary.title = output["summaryTitle"]
    summary.date = output.get("summaryDate") or "-"
    summary.text = output["summaryText"]
    summary.source_text = output.get("sourceText")
    summary.verified = bool(output.get("verified"))
    summary.verified_text = output.get("verifiedText")
    # Assigned, not left alone: effective_title() PREFERS verified_title over title, so a stale one
    # from the previous draft would show the old header above the new body. output.get() both stores a
    # fresh correction and clears an absent one.
    summary.verified_title = output.get("verifiedTitle")
    summary.verify_issues = output.get("verifyIssues")
    # `bodyFallbackFrom` belongs here for the same reason it does in `_build_summary`: the row was
    # answered by a LESSER model than the job asked for, and flagging it is how that downgrade
    # surfaces in the UI rather than only in a log. This route predates the fallback (#126) and was
    # never updated, so a single-row re-draft that silently downgraded reached the deliverable
    # unflagged - the opposite of what the same downgrade does on the worker path.
    summary.manual_check = (
        bool(output.get("manualCheck"))
        or bool(output.get("truncated"))
        or bool(output.get("bodyFallbackFrom"))
    )
    # ASSIGNED, not left alone, for the same reason verified_title above is: a re-draft that now reads
    # cleanly must CLEAR a stale notice flag, or the row would keep claiming pages were unreadable
    # after a retry recovered them. summarize_row returns a notice instead of raising when the pages
    # still cannot be read, so this route stores that notice rather than showing an error.
    summary.unreadable = bool(output.get("unreadablePages"))
    # Assigned for the same reason, and it will normally clear here: this route summarizes ONE row and
    # does not seed `embedded_review_pages`, which needs the neighbouring rows. A re-draft of a tagged
    # evaluation therefore drops its tag until the next full summarize run restores it. Recorded
    # rather than worked around - re-deriving the neighbours on a single-row path would duplicate the
    # worker's rule in a second place, which is how two copies of one decision start to drift.
    summary.embedded_review = bool(output.get("embeddedReviewPages"))
    summary.row_start = int(row["start"])
    summary.row_end = int(row["end"])
    summary.row_category = str(row["category"])
    # Provenance, rewritten rather than left alone - and `model` is load-bearing, not bookkeeping.
    #
    # `_unreadable_output` returns `model=None` on purpose: "no model saw this row, so `model=None`
    # beside `unreadablePages` is what tells a notice row apart from one that WAS summarized off its
    # readable pages". `_is_retryable_notice` keys on exactly that, requiring `summary.model is None`
    # before it will re-attempt a delivered notice.
    #
    # Leaving `model` at the PREVIOUS draft's value therefore produced the one state the retry logic
    # cannot recognise: `unreadable=True` with a non-NULL model. A row that OCR'd cleanly first time
    # and hit a transient extraction failure on re-draft became permanently un-retryable - the next
    # Summarize reuses it, the pages are never re-read, and the reviewer's only escape is
    # "Re-summarize all", which discards every hand edit in the record. It is also a FALSE RECORD by
    # the column's own definition, where that combination means "summarized off the readable pages,
    # with a notice appended".
    #
    # The other four are attribution: `summaries.model` is the column the pro-versus-flash quality
    # work groups by, and the fingerprints are how a summary is traced to the prompt text that wrote
    # it. A re-draft left all five describing a body that no longer exists.
    summary.model = output.get("model")
    summary.title_model = output.get("titleModel")
    summary.audit_model = output.get("auditModel")
    summary.prompt_fingerprint = output.get("promptFingerprint")
    summary.audit_fingerprint = output.get("auditFingerprint")
    # Fresh model output supersedes the prior hand-edits for this row.
    summary.edited_title = None
    summary.edited_date = None
    summary.edited_text = None
    session.commit()
    audit(session, "resummarize", user.id, document.id)
    # Enriched, like every other summary response. A re-draft re-snapshots row_category from the row,
    # so this is also what CLEARS the "category changed" badge on the client.
    return _summary_response(document, summary)


_BUNDLE_NAME_CHARS = re.compile(r"[^a-z0-9]+")


def _export_title_and_text(summary: Summary, *, with_pages: bool = False) -> tuple[str, str]:
    """Shared export title + body used by BOTH the Word and linked-PDF entries (so the two stay
    identical). Strips the internal [ManualCheck] and [Diagnostic Study] tags and the stale page
    suffix, then prepends the DOI.

    Both tags are internal review markers. They stay visible in the app, but a finished report or
    PDF cannot be edited to remove them, and the human-written deliverables this output is measured
    against carry neither. [Diagnostic Study] used to be RE-APPLIED here; it is now removed.

    ``with_pages`` re-applies the ``(Pages X-Y)`` suffix from the row's CURRENT range. It is off by
    default because the presentable report carries no internal page ranges; the stored suffix is
    stripped either way, since a row edit leaves it stale.

    The stripping itself now lives in `summarize_engine.presentable_title`, beside the `_row_tags`
    that apply the markers, because the bundle export path needed the same logic and could not import
    it from here.
    """
    title = presentable_title(summary.effective_title())
    if with_pages:
        title = f"{title} (Pages {summary.row_start}-{summary.row_end})"
    text = summary.effective_text()
    # The Summaries UI strips the DOI prefix into its edit box, so a reviewer-saved body carries
    # none; restore it from the raw model output. doi_prefix owns the grammar, so a document that
    # states two injury dates keeps both.
    prefix = doi_prefix(summary.text)
    if prefix and "**DOI**" not in text:
        text = f"{prefix} {text}"
    return title, text


def _export_entry(summary: Summary, *, with_pages: bool = False) -> dict:
    """One docx entry; the [ManualCheck] review flag is dropped from exports (see
    _export_title_and_text)."""
    title, text = _export_title_and_text(summary, with_pages=with_pages)
    return {
        "summaryDate": summary.effective_date(),
        "summaryTitle": title,
        "summaryText": text,
    }


def _pdf_entry(summary: Summary, *, with_pages: bool = False) -> dict:
    """Linked-PDF entry: like _export_entry, plus ``startPage`` (the 1-based source page the title
    links to - unaffected by whether the page range is printed in the text)."""
    title, text = _export_title_and_text(summary, with_pages=with_pages)
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
    entries = [_export_entry(s, with_pages=payload.includePageNumbers) for s in included]
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
    entries = [_pdf_entry(s, with_pages=payload.includePageNumbers) for s in included]
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
