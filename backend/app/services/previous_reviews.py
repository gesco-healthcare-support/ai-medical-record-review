"""Entries an earlier review of the same patient already covered - highlighted for the editors.

Records of one patient do not arrive in order, and the office asked (2026-11-12) for the record
FINISHED LAST to carry the highlights: each of its entries that an earlier-finished record of the
same patient (`documents.patient_id`) already summarized is marked, on the Summaries tab and in
yellow in the Word letter and the linked PDF, so an editor can see it was viewed before and may
skip it. The entry stays in the review; nothing is removed.

The rule is by PAGE CONTENT, not by date or category. An entry is reviewed before when every
readable page of its row was, in an earlier-finished record of the patient, inside an entry
summarized there (one the reviewer left in the export). A page is readable when its stored text
holds at least `MIN_SHINGLES` five-word runs, and it matches when `MATCH_SHARE` of its runs are found
on one such earlier page. An entry with no readable page is never marked.

Measured read-only on the live box (2026-11-12): over 84 later-finished records of patients with an
earlier one (1,420 entries), 183 had every readable page in an earlier summarized entry. Another 105
shared a date and a category with an earlier entry but held pages the earlier record did not: date
and category alone would have marked new content, and a wrong mark can make an editor skip it, where
a missed mark only costs a read. Entries with no readable page (a hand-filled form) cannot be
compared, so they stay unmarked for the same reason.

"Earlier-finished" is by the last summarize run of each record (`jobs.finished_at`): a record
uploaded first but summarized second is the one marked. Only the page text already stored is read;
no model is called. Nothing here logs page text, a file name or the ID.
"""

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, Job, PageText, Summary

MIN_SHINGLES = 25
MATCH_SHARE = 0.6

_WORD = re.compile(r"[a-z]{2,}")


def _shingles(text: str | None) -> set[int]:
    """The page's five-word runs (letters only, lower-case), hashed. Compared within one call."""
    words = _WORD.findall((text or "").lower())
    return {hash(" ".join(words[i : i + 5])) for i in range(len(words) - 4)}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def summarized_at(session: Session, document_id: str) -> datetime | None:
    """When the record's last summarize run finished, or None if none has."""
    return session.scalar(
        select(func.max(Job.finished_at)).where(
            Job.document_id == document_id, Job.kind == "summarize"
        )
    )


def _earlier_records(session: Session, document: Document, before: datetime) -> dict[str, datetime]:
    """The patient's OTHER records whose last summarize run finished before ``before``, in any
    reviewer's account, with their upload times."""
    finished = (
        select(Job.document_id, func.max(Job.finished_at).label("at"))
        .where(Job.kind == "summarize", Job.finished_at.is_not(None))
        .group_by(Job.document_id)
        .subquery()
    )
    rows = session.execute(
        select(Document.id, Document.created_at)
        .join(finished, finished.c.document_id == Document.id)
        .where(
            Document.patient_id == document.patient_id,
            Document.id != document.id,
            finished.c.at < before,
        )
    ).all()
    return {doc_id: created for doc_id, created in rows}


def _reviewed_pages(session: Session, earlier: dict[str, datetime]):
    """Every page of the earlier records that sits inside an entry left in the export, indexed by
    its runs: run -> {(record, page)}, and (record, page) -> how many runs it has."""
    covered: dict[str, set[int]] = defaultdict(set)
    for doc_id, start, end in session.execute(
        select(Summary.document_id, Summary.row_start, Summary.row_end).where(
            Summary.document_id.in_(earlier), Summary.excluded.is_(False)
        )
    ).all():
        covered[doc_id].update(range(start, end + 1))
    index: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for doc_id, page, text in session.execute(
        select(PageText.document_id, PageText.page, PageText.text).where(
            PageText.document_id.in_(list(covered))
        )
    ).all():
        if page in covered[doc_id]:
            for run in _shingles(text):
                index[run].add((doc_id, page))
    return index


def _match(runs: set[int], index) -> str | None:
    """The earlier record holding this page (MATCH_SHARE of its runs on one page), or None."""
    hits: Counter = Counter()
    for run in runs:
        for key in index.get(run, ()):
            hits[key] += 1
    if not hits:
        return None
    (doc_id, _), count = hits.most_common(1)[0]
    return doc_id if count >= MATCH_SHARE * len(runs) else None


def _seen_in(pages: list[set[int]], index) -> set[str] | None:
    """The earlier records an entry's readable pages were found in, or None when the entry has no
    readable page or any readable page was not found."""
    readable = [runs for runs in pages if len(runs) >= MIN_SHINGLES]
    if not readable:
        return None
    found = set()
    for runs in readable:
        doc_id = _match(runs, index)
        if doc_id is None:
            return None
        found.add(doc_id)
    return found


def mark_reviewed_before(
    session: Session, document: Document, *, done_at: datetime | None = None
) -> int:
    """Set `Summary.reviewed_before` on every summary of ``document``: the upload time of the
    earliest earlier record its pages were found in, or None. ``done_at`` is when this record's
    summaries count as finished (default: its last summarize run, else now); only records finished
    before it are read. Returns how many summaries are marked. The caller commits."""
    summaries = session.scalars(select(Summary).where(Summary.document_id == document.id)).all()
    if not summaries:
        return 0
    before = done_at or summarized_at(session, document.id) or _now()
    earlier = _earlier_records(session, document, before) if document.patient_id else {}
    index = _reviewed_pages(session, earlier) if earlier else {}
    wanted = {p for s in summaries for p in range(s.row_start, s.row_end + 1)}
    pages = (
        {
            page: _shingles(text)
            for page, text in session.execute(
                select(PageText.page, PageText.text).where(PageText.document_id == document.id)
            ).all()
            if page in wanted
        }
        if index
        else {}
    )
    marked = 0
    for summary in summaries:
        found = (
            _seen_in(
                [pages.get(p, set()) for p in range(summary.row_start, summary.row_end + 1)], index
            )
            if index
            else None
        )
        summary.reviewed_before = min(earlier[d] for d in found) if found else None
        marked += found is not None
    return marked
