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
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import get_settings
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


def candidate_indices(rows):
    """Indices ``i`` whose boundary with row ``i - 1`` the visit MERGE asks about, ascending."""
    return [i for i in range(1, len(rows)) if _is_candidate(rows[i - 1], rows[i])]


def hint_candidate_indices(rows):
    """Indices ``i`` whose boundary with row ``i - 1`` gets a visit HINT: the merge's candidates,
    plus a PR-4 piece's (see the module), ascending."""
    return [
        i
        for i in range(1, len(rows))
        if _is_candidate(rows[i - 1], rows[i], _HINT_VISIT_CATEGORIES, _HINT_MIXED_PAIRS)
    ]


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
        if out and same.get(row["start"]) is True:
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
