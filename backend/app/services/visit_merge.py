"""One visit's pieces merged into one draft row before the reviewer sees the draft.

The reviewers' answer of 2026-10-28: "for visits you can always merge them if it's from the same
doctor. a) Similarly, if there is an RFA (category 10) or a Work Status from the same doctor and the
same date, you can also merge them with the PR-2/Visit ... c) Diagnostics are always separate."

The export already folds such entries (`summarize_engine.fold_record_entries`), but only once the
summaries carry an author: the review page still shows every piece as its own row, and on one
reviewer's 1,885-page record on our model 122 of the 148 row edits were merging exactly these pieces
by hand (96% same date, 93% same category, mostly hand-filled PR-2 pages). A segmentation title
names no author, so here the stage's model is asked, per candidate boundary, whether one provider
wrote both rows.

A CANDIDATE is two adjacent draft rows with the same known date where both are visit documents of
the same category (1 and 1, or 5 and 5), or one is a visit document and the other joins a visit (an
RFA-titled category 10 row, or a row whose title names a work status). Never a diagnostic study
(category 3), never two dates. A category 1 row next to a category 5 row is not a candidate, the
same limit the export fold keeps: nothing has measured how often that pair is one visit.

The HINTS (``VISIT_MERGE_HINTS``, `hint_visit_pieces`) take one more visit category: a PR-4
(category 2) piece next to a PR-2 (1) or another PR-4 piece of the same date, and a PR-4 next to an
RFA or a work status. Measured on the reviewers' finished records (2026-11-05), the hints this adds
on top of the rule above: Gemini's drafts, 283 live records, 179 new hints, 134 joined by the
reviewer (75%; the rule above 77% on the same records); our model's drafts, 21 records, 29 new, 19
joined (66%; the rule above 93%). Still out: 1|5 and 2|5 (joined 9-26% of the time), and the same
category of 8, 9, 10, 13, 14 or 15, where the two sets of drafts disagree or the rate is low. The
merge below keeps its candidates as they were: it is off everywhere, and its "same doctor?" check
was never measured on a PR-4.

It merges only on a clear YES. A failed call, an unclear reply or a timeout leaves the boundary
where segmentation put it, because a wrong merge hides a document and nothing downstream surfaces
it. The merged row keeps the visit document's category and title and starts where the first piece
starts.

Off by default (``MERGE_VISIT_PIECES``): it changes what the model decides at segmentation, so it is
turned on per box once it has been measured there.

A MERGED ROW NEVER STARTS WITH AN RFA OR A WORK STATUS. The senior reviewer, 2026-11-24, on our
model: an RFA and the History and Physical after it, one date, merged into one category 1 row,
"would only summarize it as a Category 10" until he split them, while "Usually it would correctly
summarize it as a Category 1" - when the visit comes first. A summary reads the row's pages in
order, and the category 1 prompt lists a Request for Authorization among its document types, so a
row that opens on the RFA form is summarized as the form. Gemini writes such a row's body as the
visit, but its title call reads the first page: measured read-only on the live box the same day
(795 records), the summary title named the work status or the RFA on 111 of 296 visit rows that
open on one, against 41 of 547 that open on the visit, and reviewers retitled 44% against 22%. So a
row that joins a visit joins only a run a visit has already started (`_runs_start_with_a_visit`):
an RFA or a work status BEFORE its visit stays its own row, and the export fold still puts it into
that visit's entry with the report leading (`summarize_engine.fold_record_entries`). The merge, the
automatic merge and the hints keep the rule alike: a boundary after such a row is asked about,
merged or hinted only when the row itself joins the visit before it.

THE AUTOMATIC MERGE (``AUTO_MERGE_VISIT_PIECES``, `auto_merge_visit_pieces`) merges the same
candidates with NO model call. The senior reviewer, 2026-11-05, asked whether the suggested merges
for one visit's pieces may be applied automatically: "I would say yes, it is fairly rare that they
are wrong so it would save a lot of time." Measured wrong (the reviewer had kept the two apart): our
model's drafts 28 of 420 merges (6.7%), in 10 of 21 records, 13 of them on one Kaiser record where
one entry per day now wants them merged; Gemini's drafts 273 of 1,198 (23%), records finished
before the 2026-10-28 rule, when reviewers merged less. So a wrong merge must be easy to see and to
undo: it is applied only to the reviewer's copy (`review_rows`), never to the machine's
(`segment_rows`), and the editor offers "Split back" on a row whose pages still match a run of
pieces (`auto_merge_pieces`). A run never holds a category 1 visit and a category 5 visit: a work
status or an RFA after the category 1 visit goes with it; one after the category 5 visit and before
the category 1 visit stays its own row, and the export fold puts it with the category 1 visit.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.config import get_settings
from app.models import Job, SegmentRow
from app.services.llm import Part, TextPart, provider_for_stage
from app.services.pools import PoolTimeout, drain_pool
from app.services.rasterise import page_image_parts

# The export fold's own patterns, so the pass and the fold read one rule.
from app.services.summarize_engine import _RFA_TITLE, _WORK_STATUS_TITLE
from app.worker.failures import JobCancelled

logger = logging.getLogger(__name__)

_VISIT_CATEGORIES = frozenset({"1", "5"})
# The hints' visit categories: a PR-4 (2) as well. Two visit rows of different categories are a
# candidate only as a pair listed here - a PR-2 and a PR-4, never a visit and a therapy note.
_HINT_VISIT_CATEGORIES = frozenset({"1", "2", "5"})
_HINT_MIXED_PAIRS = frozenset({frozenset({"1", "2"})})
_RFA_CATEGORY = "10"
_DIAGNOSTIC_CATEGORY = "3"
# The first and last page of each of the two rows: the most images one check sends. Mirrored in
# `config._VERIFY_IMAGE_CAP` for the pod's image guard.
_MAX_IMAGES = 4

_SAME_AUTHOR_SYSTEM = (
    "You review the segmentation of a scanned workers' compensation medical record. Given two "
    "adjacent documents of the same date and their page images, decide whether the same doctor or "
    "provider wrote both."
)


def _role(row, visits=_VISIT_CATEGORIES):
    """``"visit"`` for a visit document, ``"join"`` for a row that joins a visit, else ``None``."""
    category = str(row.get("category") or "")
    if category == _DIAGNOSTIC_CATEGORY:
        return None
    title = str(row.get("title") or "")
    if _WORK_STATUS_TITLE.search(title) or (category == _RFA_CATEGORY and _RFA_TITLE.search(title)):
        return "join"
    return "visit" if category in visits else None


def joins_a_visit(row) -> bool:
    """Whether ``row`` is one that joins a visit - an RFA or a work status - and so must never open
    a merged row (see the module)."""
    return _role(row) == "join"


def _is_candidate(prev, row, visits=_VISIT_CATEGORIES, mixed_pairs=frozenset()):
    """Whether the boundary between ``prev`` and ``row`` may be one visit's - see the module."""
    date = str(row.get("date") or "").strip()
    if date in ("", "-") or str(prev.get("date") or "").strip() != date:
        return False
    roles = (_role(prev, visits), _role(row, visits))
    if roles == ("visit", "visit"):
        pair = frozenset({str(prev["category"]), str(row["category"])})
        return len(pair) == 1 or pair in mixed_pairs
    return set(roles) == {"visit", "join"}


def _runs_start_with_a_visit(rows, indices, visits=_VISIT_CATEGORIES):
    """``indices`` (ascending) without a boundary whose upper row joins a visit - an RFA, a work
    status - unless that row's own boundary with the row before it is kept: a row that joins a visit
    is never the first piece of a merged row (see the module)."""
    kept: list[int] = []
    for i in indices:
        if _role(rows[i - 1], visits) == "join" and (not kept or kept[-1] != i - 1):
            continue
        kept.append(i)
    return kept


def candidate_indices(rows):
    """Indices ``i`` whose boundary with row ``i - 1`` the visit MERGE asks about, ascending."""
    return _runs_start_with_a_visit(
        rows, [i for i in range(1, len(rows)) if _is_candidate(rows[i - 1], rows[i])]
    )


def hint_candidate_indices(rows):
    """Indices ``i`` whose boundary with row ``i - 1`` gets a visit HINT: the merge's candidates,
    plus a PR-4 piece's (see the module), ascending."""
    return _runs_start_with_a_visit(
        rows,
        [
            i
            for i in range(1, len(rows))
            if _is_candidate(rows[i - 1], rows[i], _HINT_VISIT_CATEGORIES, _HINT_MIXED_PAIRS)
        ],
        _HINT_VISIT_CATEGORIES,
    )


def hint_visit_pieces(rows):
    """Every hint candidate boundary as a merge HINT (``suggest_merge`` on the second row), with
    no model call and no merge - the reviewer decides (``VISIT_MERGE_HINTS``). Returns
    ``(rows, hinted)``, ``hinted`` counting only rows that were not already hinted. The caller's
    rows are not changed."""
    marked = set(hint_candidate_indices(rows))
    out, hinted = [], 0
    for i, row in enumerate(rows):
        if i in marked and not row.get("suggest_merge"):
            row = {**row, "suggest_merge": True}
            hinted += 1
        out.append(row)
    return out, hinted


def _row_line(name, row):
    return (
        f"{name}: pages {row['start']}-{row['end']}, category {row['category']}, "
        f"date {row['date']}, title: {row.get('title') or '-'}"
    )


def _edge_pages(row):
    """A row's first and last page, once each: a letterhead opens it, a signature ends it."""
    return sorted({row["start"], row["end"]})


def _same_author(pdf_path, prev_row, row):
    """Did one doctor or provider write both rows? Any failure, and any reply but YES, is False:
    a merge on missing evidence would hide a document."""
    settings = get_settings()
    try:
        prev_pages, row_pages = _edge_pages(prev_row), _edge_pages(row)
        parts: list[Part] = []
        for page in prev_pages + row_pages:
            parts += page_image_parts(pdf_path, page, page, 1)
        prompt = (
            _row_line("Document A", prev_row)
            + "\n"
            + _row_line("Document B", row)
            + "\n\n"
            + f"The first {len(prev_pages)} image(s) are document A (pages "
            + ", ".join(map(str, prev_pages))
            + f"); the next {len(row_pages)} are document B (pages "
            + ", ".join(map(str, row_pages))
            + ").\nDid the SAME doctor or provider write or sign both documents? Answer YES only "
            "when the pages clearly name the same provider for both - the same name in a "
            "signature, a letterhead, or a treating or requesting physician field. The same "
            "clinic, the same date or the same form is NOT enough: a clinic has several "
            "providers. If either document names no provider, or a name cannot be read, answer NO."
        )
        # Images first, the prompt last - it refers to the images by position, as the verify
        # pass's does.
        parts.append(TextPart(prompt))
        # The boundary family's stage: one setting moves both boundary checks to the same backend.
        response = provider_for_stage("verify").generate_choice(
            model=settings.model_for_stage("verify"),
            system=_SAME_AUTHOR_SYSTEM,
            parts=parts,
            choices=["YES", "NO"],
            temperature=0.0,
            stage="verify",
        )
    except JobCancelled:
        # The reviewer pressed Stop - not a model failure; it unwinds to the job runner.
        raise
    except Exception as exc:
        logger.warning("visit merge check failed at page %s: %s", row["start"], exc)
        return False
    return (response.text or "").strip() == "YES"


def _merged(first, second):
    """``second`` folded into ``first``: the visit document's category, title and classification,
    the first piece's start and its own merge hint, the second's end, flagged if either was."""
    keep = first if _role(first) == "visit" else second
    merged = dict(keep)
    merged["start"], merged["end"] = first["start"], second["end"]
    merged["suggest_merge"] = bool(first.get("suggest_merge"))
    if "x" in (str(first.get("flag")).strip().lower(), str(second.get("flag")).strip().lower()):
        merged["flag"] = "x"
    return merged


def merge_visit_pieces(pdf_path, rows, progress=None, workers=None, pool_timeout=None):
    """``rows`` with each confirmed candidate boundary merged away, and ``{"candidates": n,
    "merged": m}``. Rows stay a tiling of the record. A run of confirmed boundaries collapses into
    one row. A boundary the pool never answered keeps its split."""
    settings = get_settings()
    if workers is None:
        workers = settings.classify_workers
    if pool_timeout is None:
        pool_timeout = settings.pool_timeout(rows[-1]["end"] if rows else 0)

    def report(current, total):
        if progress is not None:
            progress("verifying", current, total)

    candidates = candidate_indices(rows)
    report(0, len(candidates))
    same = {}
    if candidates:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_same_author, pdf_path, rows[i - 1], rows[i]): i for i in candidates
            }
            done = 0
            try:
                for future in drain_pool(futures, pool_timeout):
                    same[rows[futures[future]]["start"]] = future.result()
                    done += 1
                    report(done, len(candidates))
            except PoolTimeout as pt:
                logger.warning(
                    "visit merge timed out after %ss; %d check(s) unanswered (splits kept)",
                    pool_timeout,
                    len(pt.unfinished),
                )

    out = []
    for row in rows:
        # Never into a lone RFA or work status: the check before it said no, so the row would
        # open on the form (see the module).
        if out and same.get(row["start"]) is True and _role(out[-1]) != "join":
            logger.info(
                "visit merge: pages %s-%s joined pages %s-%s",
                row["start"],
                row["end"],
                out[-1]["start"],
                out[-1]["end"],
            )
            out[-1] = _merged(out[-1], row)
            continue
        out.append(dict(row))
    return out, {"candidates": len(candidates), "merged": len(rows) - len(out)}


# --- the automatic merge (AUTO_MERGE_VISIT_PIECES) ------------------------------------------


def _visit_category(row):
    """The row's category when it is a visit document, else ``None`` (a join row, or neither)."""
    return str(row["category"]) if _role(row) == "visit" else None


def _goes_with_the_next_visit(rows, i, candidates, run_visit):
    """A join row (RFA, work status) after a category 5 run, followed by a category 1 visit it is
    also a candidate with, belongs to that category 1 visit - the export fold's order - so it is
    kept off the category 5 run. It cannot open the category 1 visit's row either, so it stays its
    own."""
    return (
        run_visit == "5"
        and i + 1 in candidates
        and _visit_category(rows[i + 1]) == "1"
        and _visit_category(rows[i]) is None
    )


def auto_merge_runs(rows):
    """The runs of row indices the automatic merge joins, each of two or more rows, ascending.

    Consecutive candidate boundaries (`candidate_indices`) chain into one run, except that a run
    never holds visit documents of two categories: a category 1 visit and a category 5 visit stay
    apart even when a work status or an RFA between them is a candidate with both. A run starts
    with a visit document: the work status or RFA the bridge keeps off a category 5 run stays its
    own row rather than open the category 1 visit's (see the module)."""
    candidates = set(candidate_indices(rows))
    runs, current, run_visit = [], [], None
    for i, row in enumerate(rows):
        visit = _visit_category(row)
        joins = (
            bool(current)
            and run_visit is not None
            and i in candidates
            and not (visit and run_visit and visit != run_visit)
            and not _goes_with_the_next_visit(rows, i, candidates, run_visit)
        )
        if joins:
            current.append(i)
            run_visit = run_visit or visit
        else:
            runs.append(current)
            current, run_visit = [i], visit
    runs.append(current)
    return [run for run in runs if len(run) > 1]


def _collapsed(rows, run):
    merged = dict(rows[run[0]])
    for j in run[1:]:
        merged = _merged(merged, rows[j])
    return merged


def auto_merge_visit_pieces(rows):
    """``rows`` with every run of `auto_merge_runs` collapsed into one row - the visit document's
    category, title and classification, the first piece's start and its merge hint, the last
    piece's end, flagged if any piece was - and ``{"runs": n, "merged": m}``. No model call.
    The caller's rows are not changed."""
    runs = auto_merge_runs(rows)
    first = {run[0]: run for run in runs}
    merged_away = {j for run in runs for j in run[1:]}
    out = []
    for i, row in enumerate(rows):
        if i in first:
            run = first[i]
            logger.info(
                "auto visit merge: pages %s-%s from %d pieces",
                row["start"],
                rows[run[-1]]["end"],
                len(run),
            )
            out.append(_collapsed(rows, run))
        elif i not in merged_away:
            out.append(dict(row))
    return out, {"runs": len(runs), "merged": len(merged_away)}


def auto_merge_pieces(rows):
    """``{(start, end): [piece, ...]}`` for every row the automatic merge makes from ``rows`` (the
    machine's pieces, `segment_rows`): what "Split back" restores on a reviewer row whose pages
    still match. Deterministic, so it is recomputed when the record is read rather than stored."""
    return {
        (int(rows[run[0]]["start"]), int(rows[run[-1]]["end"])): [dict(rows[j]) for j in run]
        for run in auto_merge_runs(rows)
    }


# --- the summary title of a row that opens on a form ---------------------------------------------


def newest_draft_pieces(session, document_id) -> list[dict]:
    """The machine's pieces of a record - the newest done segment job's `segment_rows`, in page
    order, each with its classification method - or ``[]`` when the record has none."""
    job_id = session.scalar(
        select(Job.id)
        .where(Job.document_id == document_id, Job.kind == "segment", Job.state == "done")
        .order_by(Job.id.desc())
        .limit(1)
    )
    if job_id is None:
        return []
    pieces = session.scalars(
        select(SegmentRow).where(SegmentRow.job_id == job_id).order_by(SegmentRow.idx)
    ).all()
    return [piece.as_row() | {"method": piece.method} for piece in pieces]


def visit_page_after_a_form(row, pieces) -> int | None:
    """The first page of the document a visit row goes on to, when the row OPENS on an RFA or a
    work status piece of the machine's draft (``pieces``) - else ``None``.

    The summary's title call reads the row's first page, so a visit row that opens on the form is
    titled as the form. Merges no longer make such rows (see the module), but a reviewer's hand
    merge still does, and on the live box reviewers made 275 of them from Gemini's drafts. Measured
    read-only there on 2026-11-24 (281 such visit rows, the deployed title call, real Gemini
    calls): the title named the work status or the RFA on 97 rows when it read the row's first
    page, and on 12 when its first image was this page instead; the reviewers' own titles named the
    form on 2 of the 127 they retyped, and agreed with the new title on 117 of them (62 before).
    Putting the visit's text first as well changed nothing (14)."""
    if str(row.get("category")) not in _HINT_VISIT_CATEGORIES:
        return None
    start, end = int(row["start"]), int(row["end"])
    first = next((piece for piece in pieces if int(piece["start"]) == start), None)
    if first is None or not joins_a_visit(first) or int(first["end"]) >= end:
        return None
    after = next(
        (
            piece
            for piece in pieces
            if start < int(piece["start"]) <= end and not joins_a_visit(piece)
        ),
        None,
    )
    return int(after["start"]) if after is not None else None


def seed_title_pages(session, document_id, rows) -> None:
    """Give each row that opens on a form piece its ``title_page`` (`visit_page_after_a_form`), as
    row data for `summarize_engine`, which is database-free. Rows that do not are left alone."""
    pieces = newest_draft_pieces(session, document_id)
    if not pieces:
        return
    for row in rows:
        page = visit_page_after_a_form(row, pieces)
        if page is not None:
            row["title_page"] = page
