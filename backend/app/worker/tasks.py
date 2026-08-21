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

from sqlalchemy import delete, select, update

from app.config import get_settings
from app.errors import OcrUnavailableError
from app.db import get_engine, get_sessionmaker
from app.errors import user_facing_message
from app.models import Document, Job, ReviewRow, SegmentRow, Summary
from app.services import catalog
from app.services.jobs import STATUS_ON_CANCEL, STATUS_ON_DONE, mark_terminal
from app.services.pools import PoolTimeout, drain_pool
from app.worker.cancel import (
    clear_cancel,
    clear_current_job,
    current_job_cancelled,
    set_current_job,
)
from app.worker.failures import (
    JobCancelled,
    JobNeedsAttention,
    JobPaused,
    classify_failure,
    reason_for,
)
from app.worker.finalizers import on_job_failed, on_job_stopped
from app.worker.queues import get_redis, queue_for, worker_fn

logger = logging.getLogger(__name__)

_PROGRESS_MIN_INTERVAL = 1.0  # seconds between same-stage progress writes (DB contention guard)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _run(job_id, work) -> None:
    """Shared runner: mark running, provide a throttled report(), run work(session, job, report),
    finalize done/error. work() persists its own rows/summaries on the given session."""
    # Give this work-horse its OWN connection pool before touching the database. This is the single
    # entry point for every job kind, and a horse is a fork of the worker parent, which has already
    # opened a pooled connection (`__main__._user_ids` enumerates queue lanes before `work()`). The
    # fork inherits that socket, so without this call parent and child SHARE one connection: the
    # horse opens a transaction on it and Force stop then SIGKILLs the horse mid-transaction, leaving
    # the socket INTRANS. The parent's next checkout - which is exactly where the stopped callback in
    # finalizers.py runs - dies with "can't change 'autocommit' now". pool_pre_ping does not save it,
    # because psycopg raises ProgrammingError rather than a disconnect error, so the pool propagates
    # it instead of reconnecting. That silently defeated force-stop finalization until this landed.
    #
    # dispose(close=False) is SQLAlchemy's documented fork initializer: it de-references the inherited
    # pool WITHOUT closing the parent's sockets (close=False exists for precisely this, per the 1.4.33
    # changelog), so the horse builds its own connections and can never corrupt one the parent reuses.
    get_engine().dispose(close=False)
    with get_sessionmaker()() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.warning("job %s vanished before it ran", job_id)
            return
        job.state = "running"
        job.started_at = _utcnow()
        session.commit()
        logger.info("job %s (%s) started on document %s", job_id, job.kind, job.document_id)
        # Publish which job this forked work-horse owns, so generate_with_retry's backoff can check
        # for a cancel without a session or a job argument. Cleared in the finally below.
        set_current_job(job_id)

        last_write = 0.0

        def report(stage, current, total):
            nonlocal last_write
            # BEFORE the throttle, deliberately: a job that reports faster than once a second - the
            # normal case for summarize and classify - would otherwise swallow the stop indefinitely.
            if current_job_cancelled():
                raise JobCancelled(current, total)
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
            try:
                work(session, job, report)
            except JobPaused as sig:
                # Resumable summarize: transient pressure -> keep the persisted rows + auto-resume.
                _finalize_paused(session, job_id, sig)
                return
            except JobCancelled as sig:
                # The reviewer asked to stop: a normal outcome, so no rollback and no error message.
                _finalize_cancelled(session, job_id, sig)
                return
            except JobNeedsAttention as sig:
                # Resumable summarize: a permanent failure -> calm terminal state, partial kept.
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
                logger.exception(
                    "job %s (%s) failed on document %s", job_id, job.kind, job.document_id
                )
                return

            job.state = "done"
            job.finished_at = _utcnow()
            document = session.get(Document, job.document_id)
            if document is not None:
                document.status = STATUS_ON_DONE[job.kind]
            session.commit()
            logger.info("job %s (%s) done on document %s", job_id, job.kind, job.document_id)
        finally:
            # A forked work-horse exits after one job, so this is belt-and-braces there - but it is
            # load-bearing for the tests, which run many jobs in one process, and for any future
            # non-forking worker where a stale id would cancel the NEXT job on this process.
            clear_current_job()


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
        from rq import Callback

        # Same lane as the original dispatch: a resumed job must not jump onto the shared base queue,
        # or a paused summarize would start blocking other users on every retry cycle.
        owner = getattr(session.get(Document, job.document_id), "user_id", None)
        rq_job = queue_for(job.kind, owner).enqueue_in(
            timedelta(seconds=sig.delay),
            worker_fn(job.kind),
            job.id,
            job_timeout=_job_timeout(session, job.document_id),
            # Same finalizers as the original dispatch: a resumed summarize is the LONGEST-running
            # job in the system and so the likeliest to be force-stopped. Omitting them here would
            # leave exactly those runs wedged.
            on_stopped=Callback(on_job_stopped),
            on_failure=Callback(on_job_failed),
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


def _finalize_cancelled(session, job_id, sig: JobCancelled) -> None:
    """Terminal state for a reviewer-requested stop. Mirrors _finalize_paused, minus the resume.

    Three deliberate choices:

    NO session.rollback(). Whatever the job committed is the reviewer's - a cancelled summarize's
    finished summaries, a cancelled classify's categorized rows - and hiding completed work would be
    the surprising outcome. `error` stays NULL: a stop is not a fault, and putting "cancelled" there
    would corrupt the failure metrics that `error` and `interrupted` exist to carry.

    A SCHEDULED resume is cancelled too. After a pause, `job.rq_job_id` points at the delayed RQ job,
    not the original run, so without this a cancelled summarize would quietly reappear minutes later.

    STATUS_ON_CANCEL, not STATUS_ON_DONE: a cancelled segment run has no rows, and "reviewing" would
    render an empty editor as though the record contained nothing.
    """
    job = session.get(Job, job_id)
    # Through mark_terminal, so a cooperative stop and a forced one write the SAME terminal state
    # via the same code path - see app/worker/finalizers.py.
    mark_terminal(
        session,
        job_id,
        "cancelled",
        stage="cancelled",
        done=sig.done,
        total=sig.total,
        document_status=STATUS_ON_CANCEL[job.kind],
    )

    if job.rq_job_id:
        try:
            from rq.job import Job as RQJob

            RQJob.fetch(job.rq_job_id, connection=get_redis()).cancel()
        except Exception:
            # Already gone, never scheduled, or Redis is down. The DB row is already terminal, which
            # is what the UI and the one-active-job index read, so this is not worth failing over.
            logger.info("no scheduled RQ job to cancel for job %s", job_id, exc_info=True)

    clear_cancel(job_id)
    logger.info("job %s (%s) cancelled at %d/%d", job_id, job.kind, sig.done, sig.total)


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
        verified=bool(output.get("verified")),
        verified_text=output.get("verifiedText"),
        verified_title=output.get("verifiedTitle"),
        verify_issues=output.get("verifyIssues"),
        # The row's own review flag, a body the model cut off at the token cap, OR a body answered by
        # the fallback model after the configured one was refused: all three mean a human has to look
        # at this summary before it ships. The fallback case belongs here because the row was produced
        # by a LESSER model than the job asked for - which is precisely the situation a reviewer would
        # want flagged, and it is how the downgrade surfaces in the UI rather than only in a log.
        manual_check=bool(output.get("manualCheck"))
        or bool(output.get("truncated"))
        or bool(output.get("bodyFallbackFrom")),
        row_start=row["start"],
        row_end=row["end"],
        row_category=row["category"],
        # Provenance, straight from summarize_row's output: which models wrote this row and the hash
        # of the prompt text they were given. Per-row rather than per-job because a job spans many
        # categories, so one job-level prompt hash cannot describe any individual summary.
        model=output.get("model"),
        title_model=output.get("titleModel"),
        audit_model=output.get("auditModel"),
        prompt_fingerprint=output.get("promptFingerprint"),
        audit_fingerprint=output.get("auditFingerprint"),
    )


def segment_document(job_id) -> None:
    """RQ entry: segment the document -> SegmentRows (immutable model output) + ReviewRows (the
    editable copy that diverges as the human corrects it)."""
    from app.services.segment_engine import run_segmentation

    def work(session, job, report):
        from app.services.page_text import get_page_text, populate_document

        document = session.get(Document, job.document_id)
        # OCR every page ONCE, before segmentation, so classify/dedup/summarize and any later re-run
        # read stored text instead of re-extracting the same pages. Idempotent, so a re-segment pays
        # nothing. Best-effort: a failure here must not fail the job - every reader falls back to
        # extracting on demand.
        report("reading", 0, document.page_count or 0)
        try:
            populate_document(session, document.id, document.stored_path, document.page_count or 0)
        except OcrUnavailableError:
            # The ONE failure that best-effort must not cover. "Every reader falls back to extracting
            # on demand" is true of a transient failure and false of a missing binary: there is no
            # reader that can fall back, because nothing can extract. Swallowed, the document segments
            # with no text at all and the operator meets the problem downstream as a Vertex 400 that
            # names nothing about OCR. `_run` already turns this into a friendly "OCR" job error.
            raise
        except Exception:
            logger.warning("page text population failed for %s", document.id, exc_info=True)

        # Read page text from the store rather than re-OCRing: population above already did it.
        # Its own session is used because this runs on segmentation's thread pool and a Session is
        # not thread-safe - see the same rule in the summarize pool.
        def _stored_page_text(page):
            with get_sessionmaker()() as reader:
                return get_page_text(reader, document.id, page, pdf_path=document.stored_path)

        rows = run_segmentation(
            document.stored_path,
            document.page_count,
            progress=report,
            page_text_fn=_stored_page_text,
        )
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
            # include follows the category's summarize_default, which is a per-category DB flag -
            # see catalog.summarize_default_for for why the set is not what it looks like. It is NOT a
            # SegmentRow column, so it is passed only to the editable ReviewRow copy.
            session.add(
                ReviewRow(
                    document_id=document.id,
                    include=catalog.summarize_default_for(session, fields["category"]),
                    **fields,
                )
            )

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
    from app.services.page_text import get_page_text

    def work(session, job, report):
        document = session.get(Document, job.document_id)
        rows = session.scalars(
            select(ReviewRow)
            .where(ReviewRow.document_id == job.document_id, ReviewRow.include.is_(True))
            .order_by(ReviewRow.idx)
        ).all()
        for i, row in enumerate(rows):
            report("categorizing", i, len(rows))
            try:
                # Stored text, extracted once by the segment job; OCRs on a miss so a document
                # segmented before the store existed still classifies.
                page_text = get_page_text(
                    session, document.id, row.start, pdf_path=document.stored_path
                )
            except Exception:
                page_text = ""  # best-effort: classify on the title alone if OCR is unavailable
            result = classify(row.title, page_text=page_text or None)
            row.category = result.category
            # Re-derive the summarize default for the (possibly new) category, before the reviewer
            # sees the row - so a category whose flag is off lands unchecked.
            row.include = catalog.summarize_default_for(session, result.category)
            if result.needs_review:
                row.flag = "x"
        report("categorizing", len(rows), len(rows))

    _run(job_id, work)


def dedup_document(job_id) -> None:
    """RQ entry: OCR every ReviewRow once (persist source_text), cluster likely-duplicate
    sub-documents by content, confirm each candidate with one cheap model call, and store a shared
    `dupe_group` + similarity per confirmed set. Advisory/precompute: it never edits summaries - it
    only annotates rows for the Duplicates review.

    Scope is the rows the reviewer CHECKED for summarization (include=True), so a document nobody will
    summarize is never OCR'd or clustered. This runs on demand from the Duplicates tab rather than
    automatically after identify, so by the time it runs the reviewer's selection is real.

    It used to cover EVERY row, because General and Depositions are unchecked by default and that is
    where re-scanned cover letters and exhibit lists live. Depositions became checked by default
    (2026-08-06), leaving General as the only off-by-default category, and that residual gap was
    ACCEPTED: a row the reviewer excluded will not be summarized, so a duplicate among excluded rows
    cannot reach a client. Recorded rather than silently dropped, because it IS a real narrowing.

    A consequence worth knowing: a keep-one resolution excludes the copies it just found, so a later
    re-check cannot resurrect a cluster the reviewer already collapsed. That is the settled answer
    staying settled, not a loss.

    Dismissed clusters ("not duplicates") are re-examined, but the dismissal is re-applied when the
    new cluster holds exactly the same copies - so a settled answer stays quiet while a cluster that
    gains or loses a copy is a fresh question. The grouping is rewritten in ONE transaction at the
    end, so a run that dies during OCR leaves the previous clusters intact rather than emptying the
    tab.
    """
    import gc

    from app.services.dedup import cluster_rows, confirm_cluster, duplicate_gate
    from app.services.page_text import get_row_text_with_report

    def work(session, job, report):
        settings = get_settings()
        document = session.get(Document, job.document_id)
        rows = session.scalars(
            select(ReviewRow)
            .where(ReviewRow.document_id == job.document_id, ReviewRow.include.is_(True))
            .order_by(ReviewRow.idx)
        ).all()
        total = len(rows)

        # Which clusters the reviewer dismissed, keyed by their exact set of page ranges. Captured
        # BEFORE anything is rewritten so the answer can be re-applied to an identical cluster below.
        dismissed_sets = set()
        previous_groups: dict[int, list[ReviewRow]] = {}
        for row in rows:
            if row.dupe_group is not None:
                previous_groups.setdefault(row.dupe_group, []).append(row)
        for members in previous_groups.values():
            if any(member.dupe_dismissed for member in members):
                dismissed_sets.add(frozenset((member.start, member.end) for member in members))

        # OCR each row's pages once (persist). The existing grouping is deliberately left in place:
        # a run that dies here must not empty the Duplicates tab, so clearing happens in one
        # transaction after clustering. Per-page OCR + gc keeps memory flat on a large record.
        unreadable = 0
        for i, row in enumerate(rows):
            report("deduping", i, total)
            if not (row.source_text or "").strip():
                try:
                    # From the page store (populated once by the segment job), which keeps the
                    # errored-vs-blank report intact while no longer re-OCRing pages segmentation
                    # already read. Falls back to extracting any page the store lacks.
                    text, ocr_report = get_row_text_with_report(
                        session,
                        document.id,
                        range(row.start, row.end + 1),
                        pdf_path=document.stored_path,
                    )
                    row.source_text = text
                    # A row with no text can never cluster with anything (_jaccard returns 0.0 when
                    # either side is empty), so it is invisible to the whole check rather than merely
                    # unmatched. Say so, and say WHICH failure it was: pages that errored may be
                    # transient, pages that read blank are films/photos/separators and will never
                    # yield words. Measured on a real 91-row record: 18 rows were structurally
                    # uncomparable and the run still reported success.
                    if not text.strip():
                        unreadable += 1
                        logger.warning(
                            "dedup could not read row %d (pages %d-%d) of document %s: "
                            "%d page(s) errored, %d blank - it cannot match any duplicate",
                            row.idx,
                            row.start,
                            row.end,
                            job.document_id,
                            len(ocr_report["errored"]),
                            len(ocr_report["blank"]),
                        )
                except Exception:
                    unreadable += 1
                    logger.warning(
                        "dedup OCR skipped a row on document %s", job.document_id, exc_info=True
                    )
                    row.source_text = row.source_text or ""
            session.commit()
            gc.collect()
        report("deduping", total, total)
        if unreadable:
            logger.warning(
                "dedup on document %s compared %d of %d sub-documents; %d could not be read",
                job.document_id,
                total - unreadable,
                total,
                unreadable,
            )

        # Candidate clusters (content similarity) -> confirm each is truly the same document ->
        # assign a shared per-document group number to the confirmed members.
        items = [
            {
                "id": row.id,
                "title": row.title,
                "date": row.date,
                # Category joins the gate as an ALTERNATIVE to a shared title (see duplicate_gate).
                "category": row.category,
                "text": row.source_text or "",
            }
            for row in rows
        ]
        by_id = {row.id: row for row in rows}
        confirmed_clusters = []
        for cluster in cluster_rows(items):
            members, similarity = cluster["members"], cluster["similarity"]
            pages = ", ".join(f"{by_id[m['id']].start}-{by_id[m['id']].end}" for m in members)
            if not duplicate_gate(members, similarity):
                # No shared date, or a shared date with neither title nor category agreeing, and
                # the content is not near-identical: a recurring form series, not copies.
                # Rejected without spending a confirm call.
                logger.info(
                    "dedup rejected a %d-member candidate on document %s (similarity %s, pages %s): "
                    "date plus title-or-category did not agree",
                    len(members),
                    job.document_id,
                    similarity,
                    pages,
                )
                continue
            # Above dupe_model_override the text has already settled it. Skipping the call both saves
            # quota and removes the confirm step's silent-discard failure mode, which is the one way a
            # real duplicate can vanish with no trace anywhere.
            if similarity is not None and similarity >= settings.dupe_model_override:
                logger.info(
                    "dedup accepted a %d-member candidate on document %s by similarity %s "
                    "(pages %s); confirm call skipped",
                    len(members),
                    job.document_id,
                    similarity,
                    pages,
                )
                confirmed_clusters.append((members, similarity))
                continue
            confirmed = confirm_cluster(members)
            if len(confirmed) >= 2:
                confirmed_clusters.append((confirmed, similarity))
            else:
                # Previously invisible: the candidate was dropped with no record, so a reported miss
                # could not be explained from the logs at all.
                logger.warning(
                    "dedup discarded a %d-member candidate on document %s after confirm "
                    "(similarity %s, pages %s): the model judged them distinct documents",
                    len(members),
                    job.document_id,
                    similarity,
                    pages,
                )

        # Everything below is one transaction: the old grouping is dropped and the new one written
        # together, so the tab never shows a half-rewritten state (and a crash above changed nothing).
        # `dupe_primary` is NOT cleared - it is the reviewer's "this is the copy I kept", and
        # _store_rows already resets it when a row's page range changes.
        session.execute(
            update(ReviewRow)
            .where(ReviewRow.document_id == job.document_id)
            .values(dupe_group=None, dupe_dismissed=False, dupe_similarity=None)
            .execution_options(synchronize_session="fetch")
        )
        for group_no, (confirmed, similarity) in enumerate(confirmed_clusters, start=1):
            members = [by_id[member["id"]] for member in confirmed]
            # Same copies as a cluster the reviewer dismissed -> keep it dismissed; a cluster that
            # gained or lost a copy is a new question and surfaces for review.
            dismissed = frozenset((row.start, row.end) for row in members) in dismissed_sets
            for row in members:
                row.dupe_group = group_no
                row.dupe_similarity = similarity
                row.dupe_dismissed = dismissed
        session.commit()

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
    from app.services.summarize_engine import standalone_studies_from_rows, summarize_row

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
        # The three models come from the JOB, resolved once when it was created, so a config change
        # mid-run cannot split one delivered document across two models. `or job.model` is what makes
        # a job created before 2026-08-06 behave exactly as it did: those jobs used one model for all
        # three calls, and their new columns are NULL rather than back-filled with a guess.
        title_model = job.title_model or job.model
        audit_model = job.audit_model or job.model
        attention_rows: list[dict] = []  # permanent per-row failures {idx, pages, reason}
        transient_left = False  # >=1 row failed transiently -> retry on resume
        consecutive_transient = 0
        should_pause = False
        # Give-up state. `generated` counts successes in THIS attempt, not the document's total: a
        # resumed job carries earlier rows in `done_count`, and the question here is whether the model
        # is answering NOW. Zero of it, with failures accumulating, is what a refused model looks like.
        generated = 0
        transient_failures = 0
        giveup_exc: Exception | None = None
        refused_rows: list[dict] = []

        pool_timeout = settings.pool_timeout(document.page_count)
        with ThreadPoolExecutor(max_workers=settings.pipeline_workers) as pool:
            futures = {
                pool.submit(
                    summarize_row,
                    pdf_path,
                    row,
                    model,
                    prompt_by_cat[str(row["category"])],
                    # E-08 document-set context, derived from the FULL included row set rather than
                    # `pending`: a study already summarized on an earlier attempt still stands as its
                    # own sub-document, so a resumed run must give the same context as the first.
                    standalone_studies=standalone_studies_from_rows(rows, exclude=row),
                    title_model=title_model,
                    audit_model=audit_model,
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
                            transient_failures += 1
                            refused_rows.append(
                                {
                                    "idx": i,
                                    "pages": f"{row['start']}-{row['end']}",
                                    "reason": reason_for(exc),
                                }
                            )
                            # Checked BEFORE the pause below, and the order is the whole point: both
                            # dials ship at 3, so whichever runs first decides between ending the job
                            # and auto-resuming into the same refusal until RQ's cap kills it.
                            if (
                                generated == 0
                                and transient_failures >= settings.summarize_giveup_after_failures
                            ):
                                giveup_exc = exc
                                for pending_future in futures:
                                    pending_future.cancel()  # skip not-yet-started rows
                                break
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
                    generated += 1  # one success is proof the model answers -> never give up early
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
        # A model that admitted nothing: end the job instead of pausing, because a resume replays the
        # same refusal. This MUST precede the pause check - `transient_left` was set by the very
        # failures that triggered the give-up, so the pause branch would otherwise win and we would be
        # back to job 1000173 grinding for 96 minutes.
        if giveup_exc is not None:
            raise JobNeedsAttention(
                f"{user_facing_message(giveup_exc)} No sub-documents could be summarized, so the "
                "job stopped rather than retrying.",
                attention_rows + refused_rows,
            )
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
