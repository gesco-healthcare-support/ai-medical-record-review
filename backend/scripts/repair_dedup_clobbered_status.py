"""One-off, idempotent repair: restore the terminal status a dedup run overwrote.

Until the fix in #180, `STATUS_ON_ENQUEUE["dedup"]` and `STATUS_ON_DONE["dedup"]` were both
"reviewing", which only expressed "leave the stage alone" while the document already WAS
"reviewing". The Duplicates tab lives in step 2 and the stepper lets a reviewer return there from
step 3, so pressing "Re-check duplicates" on a finished record rewrote its status.

That fix stops it happening again. It does NOT repair the records it already happened to - they keep
reading "Ready for review" on the landing page, sit in the wrong filter tab, and do not open on
Summaries. Measured on the box 2026-08-27: six documents, holding 4 to 126 stored summaries each.
One of them ended `needs_attention`, which is the costly case: that status is what names the
sub-documents a summarize run could not write, and `GET /status` returns only the NEWEST job - the
dedup, which carries no `attention` - so the list a reviewer was told to act on is unreachable in the
UI until the status is restored.

WHAT IT TOUCHES, and the guard is the whole point. A document qualifies only when ALL of these hold:

  * its status is "reviewing"                      - never any other status, in either direction
  * its NEWEST job is a dedup                      - i.e. a dedup wrote the status last
  * its newest NON-dedup job is a summarize that
    finished "done" or "needs_attention"           - so the value to restore is not a guess
  * it holds at least one stored summary           - corroboration independent of the job rows

The third condition is the one that matters, and it cut the count from 9 to 6 when this was first
measured: a SEGMENT run after a summarize sets "reviewing" legitimately, so three documents that
looked identical on a "is the newest job a dedup, and are there summaries?" test were correctly
left alone. Checking the job ORDER rather than the mere presence of a summarize is what separates
them.

Idempotent: a second run changes nothing, because a repaired document no longer has status
"reviewing".

Scope is ALWAYS explicit, mirroring backfill_doi.py - a shared box hosts several users' records:

    cd backend && uv run python scripts/repair_dedup_clobbered_status.py --dry-run --all
    cd backend && uv run python scripts/repair_dedup_clobbered_status.py --user-email x@y.com
    cd backend && uv run python scripts/repair_dedup_clobbered_status.py --document-id <uuid>
    cd backend && uv run python scripts/repair_dedup_clobbered_status.py --all

Reads and writes `documents.status` only. No AI call, no PHI in the output - document ids, statuses
and counts.
"""

import argparse
import os
import sys

from sqlalchemy import func, select

# Make the project root (parent of scripts/) importable when run as `python scripts/x.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402, F401 - registers every table on Base.metadata
from app.db import get_sessionmaker  # noqa: E402
from app.models import Document, Job, Summary, User  # noqa: E402

# The two summarize outcomes that map to a document status, and they map by the same NAME. Stated as
# a dict rather than relying on that coincidence, so a later state whose name differs cannot be
# copied across silently.
_RESTORABLE = {"done": "done", "needs_attention": "needs_attention"}


def scoped_document_ids(session, user_email=None, document_ids=None, every=False) -> list[str]:
    """The document ids this run may touch. Exits with a message rather than guessing a scope."""
    given = [bool(user_email), bool(document_ids), bool(every)]
    if sum(given) != 1:
        sys.exit("pass exactly one of --user-email / --document-id / --all")
    if document_ids:
        print(f"scope: {len(document_ids)} document(s) given explicitly")
        return list(document_ids)
    if user_email:
        user = session.scalar(select(User).where(User.email == user_email))
        if user is None:
            sys.exit(f"no user with email {user_email}")
        ids = list(session.scalars(select(Document.id).where(Document.user_id == user.id)))
        print(f"scope: {len(ids)} document(s) owned by {user_email}")
        return ids
    ids = list(session.scalars(select(Document.id)))
    print(f"scope: ALL {len(ids)} document(s) on this box")
    return ids


def restorable_status(session, document_id: str) -> str | None:
    """The status a dedup overwrote, or None when this document does not qualify.

    Returns None for every case the guard rejects, so the caller cannot accidentally treat "no
    evidence" as "restore to done".
    """
    jobs = list(session.scalars(select(Job).where(Job.document_id == document_id).order_by(Job.id)))
    if not jobs or jobs[-1].kind != "dedup":
        return None  # a dedup did not write the status last
    last_other = next((job for job in reversed(jobs) if job.kind != "dedup"), None)
    if last_other is None or last_other.kind != "summarize":
        return None  # a segment run after the summarize sets "reviewing" legitimately
    if last_other.state not in _RESTORABLE:
        return None
    summaries = session.scalar(
        select(func.count()).select_from(Summary).where(Summary.document_id == document_id)
    )
    if not summaries:
        return None  # no corroboration independent of the job rows
    return _RESTORABLE[last_other.state]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email")
    parser.add_argument("--document-id", action="append", dest="document_ids")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args()

    with get_sessionmaker()() as session:
        ids = scoped_document_ids(session, args.user_email, args.document_ids, args.all)
        changed = 0
        for document_id in ids:
            document = session.get(Document, document_id)
            if document is None or document.status != "reviewing":
                continue
            restore_to = restorable_status(session, document_id)
            if restore_to is None:
                continue
            summaries = session.scalar(
                select(func.count()).select_from(Summary).where(Summary.document_id == document_id)
            )
            print(
                f"  {document_id[:8]}  reviewing -> {restore_to:<16} summaries={summaries}"
                f"{'  (dry run)' if args.dry_run else ''}"
            )
            if not args.dry_run:
                document.status = restore_to
            changed += 1
        if not args.dry_run:
            session.commit()
        verb = "would restore" if args.dry_run else "restored"
        print(f"{verb} {changed} document status(es)")


if __name__ == "__main__":
    main()
