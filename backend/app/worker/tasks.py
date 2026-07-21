"""RQ worker task functions (torch-heavy; imported only in worker processes).

Each opens its OWN Session (never shared across processes/threads), loads the Job + Document, runs
the pipeline, and drives the state machine (queued -> running -> done/error) + throttled DB
progress. A PipelineError -> job.state=error + a friendly user_message (technical detail logged
server-side, ids only). The worker is the single writer of Document.status after enqueue.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import user_facing_message
from app.models import Document, Job, ReviewRow, SegmentRow, Summary
from app.services import catalog
from app.services.jobs import STATUS_ON_DONE

logger = logging.getLogger(__name__)

_PROGRESS_MIN_INTERVAL = 1.0  # seconds between same-stage progress writes (DB contention guard)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run(job_id, work) -> None:
    """Shared runner: mark running, provide a throttled report(), run work(session, job, report),
    finalize done/error. work() persists its own rows/summaries on the given session."""
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.warning("job %s vanished before it ran", job_id)
            return
        job.state = "running"
        job.started_at = _utcnow()
        session.commit()

        last_write = 0.0

        def report(stage, current, total):
            nonlocal last_write
            now = time.monotonic()
            # Stage changes always write (the UI keys its label off them); same-stage ticks
            # are rate-limited so per-row progress does not contend with the job's own inserts.
            if stage == job.stage and now - last_write < _PROGRESS_MIN_INTERVAL:
                return
            job.stage, job.current, job.total = stage, current, total
            session.commit()
            last_write = now

        try:
            work(session, job, report)
        except Exception as exc:
            session.rollback()  # the work may have died mid-transaction
            job = session.get(Job, job_id)
            job.state = "error"
            job.error = user_facing_message(exc)  # friendly; never the raw vendor error
            job.finished_at = _utcnow()
            document = session.get(Document, job.document_id)
            if document is not None:
                document.status = "error"
            session.commit()
            logger.exception("job %s (%s) failed on document %s", job_id, job.kind, job.document_id)
            return

        job.state = "done"
        job.finished_at = _utcnow()
        document = session.get(Document, job.document_id)
        if document is not None:
            document.status = STATUS_ON_DONE[job.kind]
        session.commit()


def segment_document(job_id) -> None:
    """RQ entry: segment the document -> SegmentRows (immutable model output) + ReviewRows (the
    editable copy that diverges as the human corrects it)."""
    from app.services.segment_engine import run_segmentation

    def work(session, job, report):
        document = session.get(Document, job.document_id)
        rows = run_segmentation(document.stored_path, document.page_count, progress=report)
        session.execute(delete(ReviewRow).where(ReviewRow.document_id == document.id))
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
            session.add(SegmentRow(job_id=job.id, **fields))
            session.add(ReviewRow(document_id=document.id, **fields))

        # Best-effort report header: auto-extract name/DOB/law firm so Review opens pre-filled. A
        # failure (429, empty OCR) must NOT fail identify - the rows are the primary output and the
        # reviewer can re-run extraction from the header bar. Logged ids-only, never the values.
        from app.services.extraction import extract_header

        pages = list(range(1, min(15, document.page_count) + 1))
        try:
            header = extract_header(document.stored_path, pages)
            document.patient_first_name = header["first_name"]
            document.patient_last_name = header["last_name"]
            document.patient_dob = header["dob"]
            document.law_firm = header["lawfirm"]
        except Exception:
            logger.warning("header extraction skipped for document %s", document.id, exc_info=True)

    _run(job_id, work)


def classify_document(job_id) -> None:
    """RQ entry (P6 individual-records): classify each already-seeded ReviewRow by its first-page
    OCR, setting the category + review flag. Rows come pre-split from the aggregate merge, so this
    does NOT re-segment. OCR is best-effort per row (a missing/unreadable page degrades to
    title-only classification rather than failing the whole case)."""
    from app.services.classification import classify
    from app.services.ocr import extract_text_from_selected_pages

    def work(session, job, report):
        document = session.get(Document, job.document_id)
        rows = session.scalars(
            select(ReviewRow)
            .where(ReviewRow.document_id == job.document_id)
            .order_by(ReviewRow.idx)
        ).all()
        for i, row in enumerate(rows):
            report("categorizing", i, len(rows))
            try:
                page_text = extract_text_from_selected_pages(document.stored_path, [row.start])
            except Exception:
                page_text = ""  # best-effort: classify on the title alone if OCR is unavailable
            result = classify(row.title, page_text=page_text or None)
            row.category = result.category
            if result.needs_review:
                row.flag = "x"
        report("categorizing", len(rows), len(rows))

    _run(job_id, work)


def summarize_document(job_id) -> None:
    """RQ entry: summarize the included ReviewRows -> Summary rows, replacing the prior set only
    after the new set is complete (a failed run keeps the previous results)."""
    from app.services.summarize_engine import summarize_row

    def work(session, job, report):
        document = session.get(Document, job.document_id)
        rows = [
            row.as_row()
            for row in session.scalars(
                select(ReviewRow)
                .where(ReviewRow.document_id == job.document_id, ReviewRow.include.is_(True))
                .order_by(ReviewRow.idx)
            ).all()
        ]
        report("summarizing", 0, len(rows))
        # Resolve prompts up front: the DB session is not thread-safe, so no catalog reads happen
        # inside the parallel section. Cache by category (many rows share one prompt).
        prompt_by_cat: dict[str, str] = {}
        for row in rows:
            cat = str(row["category"])
            if cat not in prompt_by_cat:
                prompt_by_cat[cat] = catalog.get_prompt(session, "summary", cat)

        # Independent rows (OCR + Vertex, no session access) run on a bounded pool; the seam's rate
        # limiter caps the aggregate request rate. Outputs are placed by idx so the persisted set
        # keeps document order. Replace the prior set only after all rows succeed.
        outputs: list = [None] * len(rows)
        pdf_path, model = document.stored_path, job.model
        with ThreadPoolExecutor(max_workers=get_settings().pipeline_workers) as pool:
            futures = {
                pool.submit(
                    summarize_row, pdf_path, row, model, prompt_by_cat[str(row["category"])]
                ): i
                for i, row in enumerate(rows)
            }
            for done, future in enumerate(as_completed(futures), start=1):
                outputs[futures[future]] = future.result()  # a row failure fails the job loudly
                report("summarizing", done, len(rows))

        summaries = [
            Summary(
                document_id=job.document_id,
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
            for i, (row, output) in enumerate(zip(rows, outputs))
        ]
        session.execute(delete(Summary).where(Summary.document_id == job.document_id))
        session.add_all(summaries)

    _run(job_id, work)
