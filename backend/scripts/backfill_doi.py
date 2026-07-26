"""One-off, idempotent backfill: correct the DOI on already-stored summaries.

The segmentation model propagated the claim's date of injury onto documents that never state one,
so existing summaries carry a wrong **DOI** prefix. This re-extracts each summary's DOI in
ISOLATION (only its own pages, via vision - see app.services.summary_doi) and rewrites the prefix
on text / verified_text / edited_text, removing it when the document states no injury date. Run
once per box AFTER deploying the DOI fix.

    cd backend && uv run python scripts/backfill_doi.py [--dry-run]

Idempotent (a second run makes no changes). Only the leading **DOI** prefix is touched; the
isolated extraction is fail-safe (a read error yields "-", i.e. no prefix). One Gemini vision call
per summary, so this is a bounded batch job, never the request path.
"""

import argparse
import os
import sys

from sqlalchemy import select

# Make the project root (parent of scripts/) importable when run as `python scripts/x.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402, F401 - registers every table on Base.metadata
from app.db import get_sessionmaker  # noqa: E402
from app.models import Document, Summary  # noqa: E402
from app.services.summary_doi import apply_doi_prefix, extract_injury_date  # noqa: E402


def run(session, dry_run: bool = False) -> int:
    """Rewrite each summary's DOI prefix from an isolated re-extraction. Returns the changed count."""
    changed = 0
    doc_paths: dict = {}
    for summary in session.scalars(select(Summary)):
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
    parser.add_argument("--dry-run", action="store_true", help="report the count, write nothing")
    args = parser.parse_args()
    with get_sessionmaker()() as session:
        changed = run(session, dry_run=args.dry_run)
    verb = "would change" if args.dry_run else "changed"
    print(f"backfill_doi: {verb} {changed} summary/summaries")


if __name__ == "__main__":
    main()
