"""Document-scoped JSON API: upload, segment, review rows, summarize, export, delete.

This is the multi-user, multi-document flow: everything persists in the DB and jobs run
on services/job_queue, so nothing here reads or writes the module globals in state.py
(those remain for the classic single-user UI).

OWNERSHIP: every route resolves the document by id AND current_user; a miss returns 404
- not 403 - so a non-owner can never even confirm a document exists (IDOR guard).
LOGGING: document ids only; original_filename is PHI-bearing and never logged.
"""

import hashlib
import io
import os
import re
import uuid

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_security import current_user

from mrr_ai import config
from mrr_ai.blueprints.export import _DOCX_MIMETYPE, _build_mrr_document
from mrr_ai.blueprints.review_api import EDITABLE_CATEGORIES, validate_rows
from mrr_ai.extensions import db
from mrr_ai.models import Document, Job, ReviewRow, SegmentRow, Summary
from mrr_ai.services import job_queue
from mrr_ai.services.audit import audit
from mrr_ai.services.files import safe_name
from mrr_ai.services.gemini import PROMPT_VERSION
from mrr_ai.services.pdf import get_pdf_page_count

bp = Blueprint("documents_api", __name__, url_prefix="/api/documents")


def _own(document_id):
    """The current user's document, or None. The user_id filter IS the IDOR guard."""
    return Document.query.filter_by(id=document_id, user_id=current_user.id).first()


def _not_found():
    return jsonify({"error": "not found"}), 404


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_rows(document, rows):
    """Replace the document's ReviewRows with ``rows``; returns a 400 error string or None."""
    error = validate_rows(rows, document.page_count)
    if error:
        return error
    ReviewRow.query.filter_by(document_id=document.id).delete()
    for idx, row in enumerate(rows):
        db.session.add(
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
    db.session.commit()
    return None


@bp.route("", methods=["POST"])
def create_document():
    file = request.files.get("pdf")
    if file is None or not file.filename:
        return jsonify({"error": "no PDF uploaded"}), 400

    document_id = str(uuid.uuid4())
    # Storage names are uuids: no collisions, and no patient-named filename in any
    # path that later shows up in logs or process listings.
    user_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], str(current_user.id))
    os.makedirs(user_dir, exist_ok=True)
    stored_path = os.path.join(user_dir, document_id + ".pdf")
    file.save(stored_path)

    page_count = get_pdf_page_count(stored_path)
    if not page_count:
        os.remove(stored_path)
        return jsonify({"error": "file is not a readable PDF"}), 400

    sha256 = _sha256(stored_path)
    # A duplicate upload is a WARNING, never a block: re-running a case is legitimate.
    duplicate = Document.query.filter_by(user_id=current_user.id, sha256=sha256).count() > 0

    document = Document(
        id=document_id,
        user_id=current_user.id,
        original_filename=safe_name(file.filename),
        stored_path=stored_path,
        sha256=sha256,
        page_count=page_count,
    )
    db.session.add(document)
    db.session.commit()
    audit("upload", document_id)
    return jsonify(
        {"id": document_id, "page_count": page_count, "sha256_duplicate": duplicate}
    ), 201


@bp.route("", methods=["GET"])
def list_documents():
    documents = (
        Document.query.filter_by(user_id=current_user.id).order_by(Document.created_at.desc()).all()
    )
    return jsonify([document.listing() for document in documents])


@bp.route("/<document_id>", methods=["GET"])
def get_document(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    payload = document.listing()
    payload["rows"] = [row.as_row() for row in document.review_rows]
    payload["categories"] = EDITABLE_CATEGORIES
    return jsonify(payload)


@bp.route("/<document_id>", methods=["DELETE"])
def delete_document(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    if document.active_job is not None:
        return jsonify({"error": "a job is running for this document; wait for it"}), 409
    stored_path = document.stored_path
    db.session.delete(document)  # cascades to jobs/rows/summaries
    db.session.commit()
    try:
        os.remove(stored_path)
    except OSError:
        # The DB row is gone either way; leave a trace for manual cleanup (id only).
        current_app.logger.warning("could not remove stored file for document %s", document_id)
    audit("delete", document_id)
    return jsonify({"ok": True})


@bp.route("/<document_id>/pdf")
def get_pdf(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    audit("view_pdf", document.id)
    return send_file(document.stored_path, mimetype="application/pdf", conditional=True)


@bp.route("/<document_id>/status")
def get_status(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    latest = Job.query.filter_by(document_id=document.id).order_by(Job.id.desc()).first()
    return jsonify({"status": document.status, "job": latest.progress() if latest else None})


@bp.route("/<document_id>/rows", methods=["PUT"])
def put_rows(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    if document.active_job is not None:
        # A finishing segment job would overwrite these rows; a summarize job is
        # actively reading them. Either way edits must wait.
        return jsonify({"error": "a job is running for this document; wait for it"}), 409
    rows = (request.json or {}).get("rows") or []
    error = _store_rows(document, rows)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True, "count": len(rows)})


def _segment_target(document_id, pdf_path, total_pages):
    """Job target: run segmentation, persist raw SegmentRows, seed ReviewRows.

    Runs on a pool worker inside an app context (job_queue provides both). SegmentRows
    are the immutable model output; ReviewRows start as a copy and diverge as the human
    corrects them - that divergence is the training signal.
    """

    def target(report):
        from mrr_ai.services.segment_engine import run_segmentation

        rows = run_segmentation(pdf_path, total_pages, progress=report)
        job = job_queue.active_job(document_id)
        ReviewRow.query.filter_by(document_id=document_id).delete()
        for idx, row in enumerate(rows):
            fields = dict(
                idx=idx,
                start=int(row["start"]),
                end=int(row["end"]),
                category=str(row["category"]),
                title=str(row.get("title") or "-"),
                date=str(row.get("date") or "-"),
                injury_date=str(row.get("injury_date") or "-"),
                flag=str(row.get("flag") or "-"),
                suggest_merge=bool(row.get("suggest_merge")),
            )
            db.session.add(SegmentRow(job_id=job.id, **fields))
            db.session.add(ReviewRow(document_id=document_id, **fields))
        db.session.commit()

    return target


@bp.route("/<document_id>/segment/start", methods=["POST"])
def segment_start(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    job = job_queue.submit(
        document.id,
        "segment",
        _segment_target(document.id, document.stored_path, document.page_count),
        model=config.GENAI_MODEL,
        prompt_version=PROMPT_VERSION,
    )
    if job is None:
        return jsonify({"error": "a job is already running for this document"}), 409
    return jsonify({"ok": True})


def _summarize_target(document_id, pdf_path, model):
    """Job target: summarize the persisted ReviewRows into Summary rows.

    Old summaries are replaced only after the new set is complete, so a failed run
    keeps the previous results instead of leaving half a report.
    """

    def target(report):
        from mrr_ai.services.summarize_engine import summarize_row

        job = job_queue.active_job(document_id)
        # Only rows the reviewer marked for summarization; the rest stay editable
        # but cost nothing and produce nothing.
        rows = [
            row.as_row()
            for row in ReviewRow.query.filter_by(document_id=document_id, include=True)
            .order_by(ReviewRow.idx)
            .all()
        ]
        summaries = []
        for i, row in enumerate(rows):
            report("summarizing", i, len(rows))
            output = summarize_row(pdf_path, row, model)
            summaries.append(
                Summary(
                    document_id=document_id,
                    job_id=job.id,
                    idx=i,
                    title=output["summaryTitle"],
                    date=output.get("summaryDate") or "-",
                    text=output["summaryText"],
                    source_text=output.get("sourceText"),
                    manual_check=bool(output.get("manualCheck")),
                    row_start=row["start"],
                    row_end=row["end"],
                    row_category=row["category"],
                )
            )
        report("summarizing", len(rows), len(rows))
        Summary.query.filter_by(document_id=document_id).delete()
        db.session.add_all(summaries)
        db.session.commit()

    return target


@bp.route("/<document_id>/summarize/start", methods=["POST"])
def summarize_start(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    body = request.json or {}

    rows = body.get("rows")
    if rows is not None:
        if document.active_job is not None:
            return jsonify({"error": "a job is already running for this document"}), 409
        error = _store_rows(document, rows)  # flush the editor's final state first
        if error:
            return jsonify({"error": error}), 400
    if not any(row.include for row in document.review_rows):
        return jsonify({"error": "no rows are marked for summarization"}), 400

    model = body.get("model") or config.SUMMARY_MODEL
    job = job_queue.submit(
        document.id,
        "summarize",
        _summarize_target(document.id, document.stored_path, model),
        model=model,
        prompt_version=PROMPT_VERSION,
    )
    if job is None:
        return jsonify({"error": "a job is already running for this document"}), 409
    return jsonify({"ok": True})


@bp.route("/<document_id>/summaries")
def get_summaries(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    return jsonify([summary.listing() for summary in document.summaries])


@bp.route("/<document_id>/summaries/<int:idx>", methods=["PUT"])
def put_summary(document_id, idx):
    """Reviewer edits to one summary: title/date/text land in edited_* (the raw model
    output stays immutable - it is training data), excluded toggles export membership."""
    document = _own(document_id)
    if document is None:
        return _not_found()
    summary = Summary.query.filter_by(document_id=document.id, idx=idx).first()
    if summary is None:
        return _not_found()
    if document.active_job is not None and document.active_job.kind == "summarize":
        return jsonify({"error": "summarization is rewriting these summaries; wait"}), 409

    body = request.json or {}
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
    db.session.commit()
    return jsonify(summary.listing())


def _export_entry(summary):
    """One docx entry with the legacy tag format recomposed around reviewer edits.

    The web view strips "[ManualCheck] " from titles and the "**DOI**:date," text
    prefix for readability, so edited values come back clean - but the exported
    report format must not change. The flag is re-applied from the structured
    manual_check field; the DOI prefix is recovered from the immutable raw text.
    """
    title = summary.effective_title()
    if summary.manual_check and not title.lstrip().startswith("[ManualCheck]"):
        title = f"[ManualCheck] {title}"
    text = summary.effective_text()
    doi = re.match(r"\s*(\*\*DOI\*\*:[^,]*,)", summary.text or "")
    if doi and "**DOI**" not in text:
        text = f"{doi.group(1)} {text}"
    return {
        "summaryDate": summary.effective_date(),
        "summaryTitle": title,
        "summaryText": text,
    }


@bp.route("/<document_id>/export", methods=["POST"])
def export_document(document_id):
    document = _own(document_id)
    if document is None:
        return _not_found()
    included = [s for s in document.summaries if not s.excluded]
    if not included:
        return jsonify({"error": "no summaries to export yet"}), 409
    body = request.json or {}
    entries = [_export_entry(s) for s in included]
    docx = _build_mrr_document(
        entries,
        document.page_count,
        body.get("patientName") or "",
        body.get("patientdob") or "",
        body.get("QMEorAME") or "",
        body.get("lawfirm") or "",
    )
    # In-memory: no PHI-named files accumulating under ~/MRRs from the new flow.
    buffer = io.BytesIO()
    docx.save(buffer)
    buffer.seek(0)
    audit("export", document.id)
    return send_file(
        buffer,
        mimetype=_DOCX_MIMETYPE,
        as_attachment=True,
        download_name="summaries.docx",
    )
