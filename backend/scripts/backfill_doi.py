"""One-off, idempotent backfill: correct the DOI on already-stored summaries.

The segmentation model propagated the claim's date of injury onto documents that never state one,
so summaries written before the isolated-extraction fix carry a wrong **DOI** prefix. This
re-extracts each summary's DOI in ISOLATION (only its own pages, via vision - see
app.services.summary_doi) and rewrites the prefix on text / verified_text / edited_text, removing it
when the document states no injury date.

The scope is ALWAYS explicit: a shared box hosts several users' records, and rewriting someone
else's summaries (or re-sending their pages to the model) is not this script's business. Pass
exactly one of --user-email / --document-id / --all, and the scope is printed before anything is
read:

    cd backend && uv run python scripts/backfill_doi.py --user-email someone@example.com --dry-run
    cd backend && uv run python scripts/backfill_doi.py --document-id <uuid> [--document-id <uuid>]
    cd backend && uv run python scripts/backfill_doi.py --all

Idempotent (a second run makes no changes). Only the leading **DOI** prefix is touched; the isolated
extraction is fail-safe (a read error yields "-", i.e. no prefix). One Gemini vision call per
summary in scope, so this is a bounded batch job, never the request path.
"""

import argparse
import os
import sys

from sqlalchemy import func, select

# Make the project root (parent of scripts/) importable when run as `python scripts/x.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402, F401 - registers every table on Base.metadata
from app.db import get_sessionmaker  # noqa: E402
from app.models import Document, Summary, User  # noqa: E402
from app.services.summary_doi import apply_doi_prefix, extract_injury_date  # noqa: E402


def scoped_document_ids(session, user_email=None, document_ids=None, every=False) -> list[str]:
    """The document ids this run may touch. Exits with a message rather than guessing a scope."""
    given = [bool(user_email), bool(document_ids), bool(every)]
    if sum(given) != 1:
        raise SystemExit(
            "backfill_doi: choose exactly one scope - --user-email EMAIL, --document-id ID "
            "(repeatable), or --all"
        )
    if every:
        return list(session.scalars(select(Document.id).order_by(Document.id)).all())
    if user_email:
        user = session.scalar(
            select(User).where(func.lower(User.email) == user_email.strip().lower())
        )
        if user is None:
            raise SystemExit(f"backfill_doi: no user with email {user_email}")
        return list(
            session.scalars(
                select(Document.id).where(Document.user_id == user.id).order_by(Document.id)
            ).all()
        )
    found = set(session.scalars(select(Document.id).where(Document.id.in_(document_ids))).all())
    missing = [doc_id for doc_id in document_ids if doc_id not in found]
    if missing:
        raise SystemExit(f"backfill_doi: no such document(s): {', '.join(missing)}")
    return list(document_ids)


def run(session, document_ids, dry_run: bool = False) -> int:
    """Rewrite each summary's DOI prefix from an isolated re-extraction. Returns the changed count."""
    changed = 0
    doc_paths: dict = {}
    summaries = session.scalars(
        select(Summary).where(Summary.document_id.in_(document_ids)).order_by(Summary.id)
    )
    for summary in summaries:
        path = doc_paths.get(summary.document_id)
        if path is None:
            document = session.get(Document, summary.document_id)
            if document is None:
                continue
            path = doc_paths[summary.document_id] = document.stored_path
        injury = extract_injury_date(path, summary.row_start, summary.row_end)
        new_text = apply_doi_prefix(summary.text, injury)
        new_verified = apply_doi_prefix(summary.verified_text, injury)
        new_edited = apply_doi_prefix(summary.edited_text, injury)
        if (new_text, new_verified, new_edited) != (
            summary.text,
            summary.verified_text,
            summary.edited_text,
        ):
            changed += 1
            if not dry_run:
                summary.text = new_text
                summary.verified_text = new_verified
                summary.edited_text = new_edited
    if not dry_run:
        session.commit()
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill: DOI only when the document states it.")
    parser.add_argument("--user-email", help="scope to one account's records")
    parser.add_argument(
        "--document-id",
        action="append",
        dest="document_ids",
        help="scope to one record (repeatable)",
    )
    parser.add_argument("--all", action="store_true", help="EVERY record on this box")
    parser.add_argument("--dry-run", action="store_true", help="report the count, write nothing")
    args = parser.parse_args()
    with get_sessionmaker()() as session:
        ids = scoped_document_ids(
            session,
            user_email=args.user_email,
            document_ids=args.document_ids,
            every=args.all,
        )
        # Print the scope BEFORE reading a single page, so a dry run shows exactly what is at stake.
        print(
            f"backfill_doi: scope {len(ids)} document(s): {', '.join(ids) or '(none)'}", flush=True
        )
        changed = run(session, ids, dry_run=args.dry_run)
    verb = "would change" if args.dry_run else "changed"
    print(f"backfill_doi: {verb} {changed} summary/summaries")


if __name__ == "__main__":
    main()
