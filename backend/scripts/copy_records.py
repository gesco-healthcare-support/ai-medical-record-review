"""Copy one user's documents - rows AND the stored PDF - under another user.

The box hosts several people's records, and the most valuable ones are not ours. They carry 102
reviewer CATEGORY corrections: a `review_rows.category` that disagrees with the `segment_rows`
category for the same page range. That disagreement is the only ground truth any classification
measurement has, no more of it is coming, and BOTH writers of a review row destroy it -
`segment_document` deletes every ReviewRow and re-adds from the model, and `classify_document`
assigns `row.category` in place. So a record holding labels cannot be reprocessed.

This makes a copy that CAN be. The original keeps its labels whatever happens to the duplicate.

Scope is always explicit and printed before anything is read - rewriting or re-sending someone
else's records is not this script's business, and neither is guessing whose:

    docker compose exec -T api python scripts/copy_records.py \
        --from-email owner@example.com --to-email me@example.com --dry-run

Four things that are easy to get wrong here:

* **The JOB travels, not just the document.** `segment_rows` hang off `jobs`, not off `documents`.
  Copy the document and its review rows alone and you keep the reviewer's answer while losing the
  model's - which is half of every label. The newest `kind='segment' state='done'` job comes too.

* **The PDF is copied, never shared.** `delete_document` does an unconditional
  `os.remove(stored_path)` after deleting the row, so a copy pointing at the original's file would
  delete the ORIGINAL's PDF the first time anyone deleted the duplicate, leaving a live row with no
  file behind it. The file lands first and the row is only written once it has, so an interrupted
  run leaves a stray file rather than a row pointing at nothing.

* **`rq_job_id` is cleared.** It names a queue job belonging to the original, and orphan recovery
  correlates by it. Carrying it over would let a copy be mistaken for the original's in-flight work.

* **A document with an ACTIVE job is skipped.** Its rows are being rewritten as we read them, so a
  copy taken now would be a torn snapshot.

Idempotent. Each copy writes an `audit_log` row (`action='copy'`, `detail='source=<uuid> ...'`) and
a second run skips any source that already has one. That is also the only durable record of where a
duplicate came from, which sha256 cannot be: one sha is already shared between two accounts here,
and two of one account's own documents share another.

Summaries are NOT copied - they are model output, reproducible, large, and no measurement needs
them. The audit trail is not copied either: it is the history of the original.
"""

import argparse
import os
import shutil
import sys
import uuid

from sqlalchemy import inspect, select

# Make the project root (parent of scripts/) importable when run as `python scripts/x.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402, F401 - registers every table on Base.metadata
from app.config import get_settings  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    Document,
    Job,
    PageText,
    ReviewRow,
    SegmentRow,
    User,
)
from app.services.audit import audit  # noqa: E402

COPY_ACTION = "copy"
# `Document.active_job`'s own definition. A job in any other state - including `interrupted` and
# `needs_attention`, of which this box has plenty - is finished and cannot rewrite rows underneath us.
ACTIVE_STATES = ("queued", "running", "paused")
# Visible in the landing table, which is where someone needs to tell a copy from real work. ASCII and
# alphanumeric so it stays safe in an export filename.
COPY_PREFIX = "COPY-"


def _clone(row, exclude: set[str], **overrides):
    """A new instance of ``row``'s class with every mapped column copied, minus ``exclude``.

    Column-wise rather than field-by-field on purpose: this script's whole job is "copy everything",
    so a column added later should travel by default. Getting that wrong is silent - a missing field
    reads as a legitimately empty one - and `method` (#188) is a live example of a column that would
    have been forgotten by a hand-written list.
    """
    values = {
        attr.key: getattr(row, attr.key)
        for attr in inspect(type(row)).mapper.column_attrs
        if attr.key not in exclude
    }
    values.update(overrides)
    return type(row)(**values)


def _user(session, email: str) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        sys.exit(f"no user with email {email}")
    return user


def _already_copied(session, target_user_id: int) -> set[str]:
    """Source document ids this target already holds a copy of."""
    details = session.scalars(
        select(AuditLog.detail).where(
            AuditLog.action == COPY_ACTION, AuditLog.user_id == target_user_id
        )
    )
    return {
        part.removeprefix("source=")
        for detail in details
        if detail
        for part in detail.split()
        if part.startswith("source=")
    }


def _newest_segment_job(session, document_id: str) -> Job | None:
    """The job whose `segment_rows` are the model's current answer for this document.

    Newest DONE segment job, matching how every measurement reads the corpus. An errored or
    interrupted job left partial rows that were never what the reviewer saw.
    """
    return session.scalar(
        select(Job)
        .where(Job.document_id == document_id, Job.kind == "segment", Job.state == "done")
        .order_by(Job.id.desc())
        .limit(1)
    )


def _has_active_job(session, document_id: str) -> bool:
    return (
        session.scalar(
            select(Job.id)
            .where(Job.document_id == document_id, Job.state.in_(ACTIVE_STATES))
            .limit(1)
        )
        is not None
    )


def copy_document(session, source: Document, target: User, dry_run: bool) -> dict:
    """Copy one document under ``target``. Returns the per-table counts, for the summary line."""
    # Same convention as `create_document`: uuid storage names, so no patient-named filename ever
    # reaches a path that shows up in a log or a process listing.
    new_id = str(uuid.uuid4())
    user_dir = os.path.join(get_settings().upload_folder, str(target.id))
    stored_path = os.path.join(user_dir, new_id + ".pdf")

    job = _newest_segment_job(session, source.id)
    segment_rows = (
        session.scalars(
            select(SegmentRow).where(SegmentRow.job_id == job.id).order_by(SegmentRow.idx)
        ).all()
        if job is not None
        else []
    )
    review_rows = session.scalars(
        select(ReviewRow).where(ReviewRow.document_id == source.id).order_by(ReviewRow.idx)
    ).all()
    page_texts = session.scalars(
        select(PageText).where(PageText.document_id == source.id).order_by(PageText.page)
    ).all()
    counts = {
        "segment_job": 1 if job is not None else 0,
        "segment_rows": len(segment_rows),
        "review_rows": len(review_rows),
        "page_texts": len(page_texts),
    }
    if dry_run:
        return counts

    # The file first. A row written before its PDF exists would break the viewer, OCR and every
    # export for a document that LOOKS complete; a file written before its row is a stray this
    # script can be re-run over, because the source is still marked uncopied.
    os.makedirs(user_dir, exist_ok=True)
    shutil.copyfile(source.stored_path, stored_path)
    try:
        session.add(
            _clone(
                source,
                exclude={"id", "user_id", "stored_path", "original_filename"},
                id=new_id,
                user_id=target.id,
                stored_path=stored_path,
                original_filename=(COPY_PREFIX + source.original_filename)[:512],
            )
        )
        if job is not None:
            # `rq_job_id` names the ORIGINAL's queue job; carrying it over would let orphan recovery
            # correlate this copy with work that is not its own.
            new_job = _clone(
                job, exclude={"id", "document_id", "rq_job_id"}, document_id=new_id, rq_job_id=None
            )
            session.add(new_job)
            session.flush()  # assigns new_job.id, which the segment rows need
            for row in segment_rows:
                session.add(_clone(row, exclude={"id", "job_id"}, job_id=new_job.id))
        for row in review_rows:
            session.add(_clone(row, exclude={"id", "document_id"}, document_id=new_id))
        for row in page_texts:
            session.add(_clone(row, exclude={"id", "document_id"}, document_id=new_id))
        session.commit()
    except BaseException:
        session.rollback()
        # Do not leave a file with no row behind it: the source stays unmarked, so the next run
        # would copy it again and this one would never be reclaimed.
        if os.path.exists(stored_path):
            os.remove(stored_path)
        raise
    audit(
        session,
        COPY_ACTION,
        target.id,
        new_id,
        detail=f"source={source.id} source_user={source.user_id} pages={source.page_count}",
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy one user's documents - rows and the stored PDF - under another user."
    )
    parser.add_argument("--from-email", required=True, help="account whose documents to copy")
    parser.add_argument("--to-email", required=True, help="account the copies are created under")
    parser.add_argument(
        "--document-id", action="append", default=[], help="copy only these (repeatable)"
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be copied")
    args = parser.parse_args()

    session = get_sessionmaker()()
    source_user, target = _user(session, args.from_email), _user(session, args.to_email)
    if source_user.id == target.id:
        sys.exit("--from-email and --to-email are the same account")

    query = select(Document).where(Document.user_id == source_user.id)
    if args.document_id:
        query = query.where(Document.id.in_(args.document_id))
    documents = session.scalars(query.order_by(Document.created_at)).all()
    done = _already_copied(session, target.id)

    # Ids and counts only: `original_filename` is PHI and does not belong in a terminal, a log or a
    # screenshot of one.
    print(
        f"scope: {len(documents)} document(s) of user {source_user.id} -> user {target.id}"
        f"{' [DRY RUN]' if args.dry_run else ''}"
    )
    totals: dict[str, int] = {}
    copied = skipped = 0
    for document in documents:
        if document.id in done:
            print(f"  {document.id[:8]}  skip: already copied")
            skipped += 1
            continue
        if _has_active_job(session, document.id):
            print(f"  {document.id[:8]}  skip: a job is running on it")
            skipped += 1
            continue
        counts = copy_document(session, document, target, args.dry_run)
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value
        copied += 1
        print(
            f"  {document.id[:8]}  {document.page_count}pp  "
            + "  ".join(f"{key}={value}" for key, value in counts.items())
        )
    print(
        f"copied {copied}, skipped {skipped}; "
        + "  ".join(f"{key}={value}" for key, value in sorted(totals.items()))
    )


if __name__ == "__main__":
    main()
