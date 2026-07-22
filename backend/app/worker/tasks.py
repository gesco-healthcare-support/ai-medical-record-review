"""RQ worker task functions (torch-heavy; imported only in worker processes).

Each opens its OWN Session (never shared across processes/threads), loads the Job + Document, runs
the pipeline, and drives the state machine (queued -> running -> done/error) + throttled DB
progress. A PipelineError -> job.state=error + a friendly user_message (technical detail logged
server-side, ids only). The worker is the single writer of Document.status after enqueue.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import get_sessionmaker
from app.errors import user_facing_message
from app.models import Document, Job, ReviewRow, SegmentRow, Summary
from app.services import catalog
from app.services.jobs import STATUS_ON_DONE
from app.services.pools import PoolTimeout, drain_pool
from app.worker.failures import (
    JobNeedsAttention,
    JobPaused,
    classify_failure,
    reason_for,
)
from app.worker.queues import queue_for, worker_fn

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
        logger.info("job %s (%s) started on document %s", job_id, job.kind, job.document_id)

        last_write = 0.0

        def report(stage, current, total):
            nonlocal last_write
            now = time.monotonic()
            # Stage changes always write (the UI keys its label off them); same-stage ticks
            # are rate-limited so per-row progress does not contend with the job's own inserts.
            if stage == job.stage and now - last_write < _PROGRESS_MIN_INTERVAL:
                return
            if stage != job.stage:
                logger.info("job %s stage %r on document %s", job_id, stage, job.document_id)
            job.stage, job.current, job.total = stage, current, total
            session.commit()
            last_write = now

        try:
            work(session, job, report)
        except JobPaused as sig:
            # Resumable summarize: transient pressure -> keep the persisted rows + auto-resume.
            _finalize_paused(session, job_id, sig)
            return
        except JobNeedsAttention as sig:
            # Resumable summarize: a permanent failure -> calm terminal state, partial results kept.
            _finalize_needs_attention(session, job_id, sig)
            return
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
        logger.info("job %s (%s) done on document %s", job_id, job.kind, job.document_id)


def _job_timeout(session, document_id) -> int:
    """Size-aware RQ wall-clock cap for a (re)enqueue (mirrors services.jobs.enqueue)."""
    settings = get_settings()
    pages = getattr(session.get(Document, document_id), "page_count", 0) or 0
    return settings.effective_job_timeout(pages)


def _finalize_paused(session, job_id, sig: JobPaused) -> None:
    """Persist progress + schedule a delayed resume of the SAME job. document.status stays
    in-flight ("summarizing") so the UI keeps showing progress ("paused, will retry"). A fresh RQ
    job id is recorded so orphan recovery correlates the scheduled resume, not the finished run."""
    job = session.get(Job, job_id)
    job.state = "paused"
    job.stage = "paused"
    job.current, job.total = sig.done, sig.total
    job.attempts = (job.attempts or 0) + 1
    try:
        rq_job = queue_for(job.kind).enqueue_in(
            timedelta(seconds=sig.delay),
            worker_fn(job.kind),
            job.id,
            job_timeout=_job_timeout(session, job.document_id),
        )
        job.rq_job_id = rq_job.id
        session.commit()
    except Exception:
        # Could not schedule the resume (e.g. Redis down): fail visibly rather than strand paused.
        session.rollback()
        job = session.get(Job, job_id)
        job.state = "interrupted"
        job.finished_at = _utcnow()
        document = session.get(Document, job.document_id)
        if document is not None:
            document.status = "interrupted"
        session.commit()
        logger.warning(
            "resume enqueue failed for job %s; marked interrupted", job_id, exc_info=True
        )
        return
    logger.info(
        "job %s paused after %d/%d; resume scheduled in %ss (attempt %d)",
        job_id,
        sig.done,
        sig.total,
        sig.delay,
        job.attempts,
    )


def _finalize_needs_attention(session, job_id, sig: JobNeedsAttention) -> None:
    """Terminal, calm outcome: some sub-documents could not be summarized. Successful summaries are
    already persisted (per-row); record the friendly reason + the affected rows (non-PHI)."""
    job = session.get(Job, job_id)
    job.state = "needs_attention"
    job.error = sig.message
    job.attention = {"rows": sig.rows, "message": sig.message}
    job.finished_at = _utcnow()
    document = session.get(Document, job.document_id)
    if document is not None:
        document.status = "needs_attention"
    session.commit()
    logger.info("job %s needs attention: %d row(s) could not be summarized", job_id, len(sig.rows))


def _build_summary(job, idx, row, output) -> Summary:
    """One Summary ORM row from a summarize_row output + its source row (legacy shape)."""
    return Summary(
        document_id=job.document_id,
        job_id=job.id,
        idx=idx,
        title=output["summaryTitle"],
        date=output.get("summaryDate") or "-",
        text=output["summaryText"],
        source_text=output.get("sourceText"),
        manual_check=bool(output.get("manualCheck")),
        row_start=row["start"],
        row_end=row["end"],
        row_category=row["category"],
    )


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
    """RQ entry: summarize the included ReviewRows -> Summary rows, RESUMABLY (item 7).

    Per-row: each Summary is persisted the moment it succeeds, so a mid-run failure never loses
    completed work. Skip-done: a row whose (start, end, category) already has a Summary is REUSED
    (its reviewer edits preserved) and only re-positioned to the current order - so auto-resume, a
    manual re-click, and post-crash recovery all only pay for the missing rows. A run that ends
    with retryable rows left raises JobPaused (auto-resume after a fixed delay, forever); a run
    whose only failures are permanent (blank OCR, auth, per-day quota) raises JobNeedsAttention,
    keeping every successful summary. The "Re-summarize all" path clears summaries in the route
    first, so nothing is reused here.
    """
    from app.services.summarize_engine import summarize_row

    def work(session, job, report):
        settings = get_settings()
        document = session.get(Document, job.document_id)
        rows = [
            row.as_row()
            for row in session.scalars(
                select(ReviewRow)
                .where(ReviewRow.document_id == job.document_id, ReviewRow.include.is_(True))
                .order_by(ReviewRow.idx)
            ).all()
        ]
        total = len(rows)
        wanted = {(int(r["start"]), int(r["end"]), str(r["category"])) for r in rows}

        # Reconcile persisted summaries by row identity: keep the first for each still-wanted row,
        # drop any that are stale (row removed/edited) or duplicate. This never touches summaries
        # for rows still in the set, so reviewer edits survive a resume/re-run.
        existing: dict[tuple, Summary] = {}
        for summary in session.scalars(
            select(Summary).where(Summary.document_id == job.document_id)
        ).all():
            key = (int(summary.row_start), int(summary.row_end), str(summary.row_category))
            if key in wanted and key not in existing:
                existing[key] = summary
            else:
                session.delete(summary)
        session.commit()

        # Position reused summaries to the current row order; collect the rows still to generate.
        pending: list[tuple[int, dict]] = []
        for i, row in enumerate(rows):
            key = (int(row["start"]), int(row["end"]), str(row["category"]))
            reused = existing.get(key)
            if reused is not None:
                if reused.idx != i:
                    reused.idx = i
                continue
            pending.append((i, row))
        session.commit()

        done_count = total - len(pending)
        report("summarizing", done_count, total)
        if not pending:
            return  # everything already summarized -> _run marks done

        # Resolve prompts up front (the DB session is not thread-safe; no catalog reads in the pool).
        prompt_by_cat: dict[str, str] = {}
        for _, row in pending:
            cat = str(row["category"])
            if cat not in prompt_by_cat:
                prompt_by_cat[cat] = catalog.get_prompt(session, "summary", cat)

        pdf_path, model = document.stored_path, job.model
        attention_rows: list[dict] = []  # permanent per-row failures {idx, pages, reason}
        transient_left = False  # >=1 row failed transiently -> retry on resume
        consecutive_transient = 0
        should_pause = False

        pool_timeout = settings.pool_timeout(document.page_count)
        with ThreadPoolExecutor(max_workers=settings.pipeline_workers) as pool:
            futures = {
                pool.submit(
                    summarize_row, pdf_path, row, model, prompt_by_cat[str(row["category"])]
                ): (i, row)
                for i, row in pending
            }
            try:
                for future in drain_pool(futures, pool_timeout):
                    i, row = futures[future]
                    try:
                        output = future.result()
                    except Exception as exc:
                        if classify_failure(exc) == "transient":
                            transient_left = True
                            consecutive_transient += 1
                            logger.warning(
                                "summarize row %d transient failure on document %s (%d in a row)",
                                i,
                                job.document_id,
                                consecutive_transient,
                            )
                            if consecutive_transient >= settings.summarize_pause_after:
                                should_pause = True
                                for pending_future in futures:
                                    pending_future.cancel()  # skip not-yet-started rows
                                break
                        else:
                            attention_rows.append(
                                {
                                    "idx": i,
                                    "pages": f"{row['start']}-{row['end']}",
                                    "reason": reason_for(exc),
                                }
                            )
                            logger.warning(
                                "summarize row %d permanent failure on document %s",
                                i,
                                job.document_id,
                                exc_info=True,
                            )
                        continue
                    # Success: persist immediately so a later failure never loses this row.
                    session.add(_build_summary(job, i, row, output))
                    session.commit()
                    done_count += 1
                    consecutive_transient = 0
                    report("summarizing", done_count, total)
            except PoolTimeout as pt:
                # A stalled pool near the wall-clock wall: pause and let the outstanding rows retry
                # on the next resume (pending is recomputed by row identity), never hang.
                transient_left = True
                should_pause = True
                logger.warning(
                    "summarize pool timed out after %ss on document %s; %d row(s) will retry",
                    pool_timeout,
                    job.document_id,
                    len(pt.unfinished),
                )

        # Retryable rows outstanding -> pause + auto-resume (transient wins over permanent this
        # cycle; permanents resurface once transient pressure clears). Otherwise, if only permanent
        # failures remain -> needs attention. Otherwise every row is summarized -> done.
        if should_pause or transient_left:
            raise JobPaused(delay=settings.summarize_resume_delay, done=done_count, total=total)
        if attention_rows:
            n = len(attention_rows)
            raise JobNeedsAttention(
                f"{n} of {total} document{'s' if n != 1 else ''} could not be summarized. "
                "Review, correct, or exclude them, then summarize again.",
                attention_rows,
            )

    _run(job_id, work)
