"""A/B the segmentation tiebreak against reviewer-corrected boundaries. No DB writes.

The question: SEGMENTATION_PROMPT deliberately over-splits. Its tiebreak says "when you are
genuinely unsure ... START A NEW RECORD. A reviewer merges a false split in one click, but a
document hidden inside another record is never seen again." That was the right trade when reviewer
time was cheap. Measured 2026-08-03, boundary corrections are 20.2% of rows pooled across builds and
96.1% of them are MERGES - the reviewer joining rows the model split apart - so the trade is now
being paid for in the scarcest resource in the pipeline.

Ground truth is free and already exists: `review_rows` holds boundaries a human corrected, and
`segment_rows` holds what the model originally produced. This script re-runs segmentation offline
and scores it against the reviewed boundaries.

It never writes to the database. That is not a nicety - the reviewed boundaries ARE the ground
truth, and re-running segmentation through the normal worker path would overwrite them.

Both directions are reported, because they are not symmetric:
  - OVER-SPLIT costs reviewer time (a merge click), which is what we are trying to reduce.
  - UNDER-SPLIT hides a document inside another record, which is the failure the current wording
    exists to prevent and is far more expensive than a click.
An arm that reduces over-splitting while introducing under-splits has not won. Read both columns.

Usage. PYTHONPATH must include the backend root: these scripts import `app.*` but the package is
not installed into the venv, and Python puts only the SCRIPT's directory on sys.path, not the cwd.
Without it the run dies on `ModuleNotFoundError: No module named 'app'`.

    docker compose exec -T -e PYTHONPATH=/app api \
        python scripts/eval/segmentation_boundary_ab.py --list
    docker compose exec -T -e PYTHONPATH=/app api \
        python scripts/eval/segmentation_boundary_ab.py \
        --docs 2baa5747-...,d059fb11-... --arms control,merge_biased

Locally, from `backend/`: `PYTHONPATH=. uv run python scripts/eval/segmentation_boundary_ab.py`.
Expect ~45s of import before anything prints - segment_engine pulls in the torch classifier.

Cost: one Vertex call per window per document per arm (~3-5 windows for a 300-page file).
Classification and the verify pass are skipped by default, so this isolates the prompt's effect on
boundaries and keeps the run cheap; --stage full adds the verify pass.
"""

import argparse
import sys

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Document, Job, ReviewRow
from app.services import segment_engine
from app.services.gemini import SEGMENTATION_PROMPT
from app.services.genai_client import get_genai_client
from app.services.windows import byte_budgeted_windows

# The exact sentence being tested. Kept as a literal so a drift in gemini.py makes this script fail
# loudly rather than silently A/B-ing two identical prompts.
_CONTROL_TIEBREAK = (
    "- Tiebreak: when you are genuinely unsure whether a page starts a new document or continues "
    "the previous one, START A NEW RECORD. A reviewer merges a false split in one click, but a "
    "document hidden inside another record is never seen again."
)

# Inverts the default while REQUIRING a positive reason to split, rather than merely saying "do not
# split". A bare inversion would trade one blind bias for another; naming the evidence keeps the
# genuine starts (which the prompt already lists as strong signals) and drops only the guesses.
_MERGE_BIASED_TIEBREAK = (
    "- Tiebreak: when you are genuinely unsure whether a page starts a new document or continues "
    "the previous one, CONTINUE THE CURRENT RECORD. Start a new record only when you can point to "
    "a positive start signal on the page: a new letterhead together with a new document title, a "
    "new visit or encounter date, a new author or facility, or the first page of a form. Absent "
    "one of those, the page continues the document before it."
)

# The exact wording #108 shipped and #114 reverted, so that change can be measured ALONE. #104 and
# #108 went out together and were scored together, which is why the 2026-08-17 regression (surplus
# rows 43 -> 63, under-splits 0 -> 6 over six documents) could not be attributed to either one.
#
# It differs from _MERGE_BIASED_TIEBREAK above in exactly one respect, and that is the hypothesis
# under test: this drops "when you are genuinely unsure" entirely. That trigger asks the model to
# report its own uncertainty, and the confidence-enum trial noted under SEGMENT_RESPONSE_SCHEMA in
# gemini.py measured it answering "high" on 231 of 232 rows including every known near-miss. A rule
# keyed to a state the model cannot report does not fire at borderline cases; it applies whenever
# splitting is available at all. The merge_biased arm keeps that trigger and only inverts its
# direction, so running both separates "the direction was wrong" from "the trigger was wrong".
_EVIDENCE_GATED_TIEBREAK = (
    "- Default when a page is hard to place: it CONTINUES the record already open, unless you can "
    "name a specific start signal visible on it - one of the strong signals above, a new encounter "
    "date, or a different title. Name that signal before you split; when you cannot name one, the "
    "page continues.\n"
    "- One nameable start signal is enough to split. Weigh the two mistakes unequally: a false split "
    "costs a reviewer one merge click, while a document buried inside another record is never seen "
    "again. So do not withhold a split that has evidence behind it - the bar is visible evidence on "
    "the page, not how confident you feel."
)

ARMS = {
    "control": lambda p: p,
    "merge_biased": lambda p: p.replace(_CONTROL_TIEBREAK, _MERGE_BIASED_TIEBREAK),
    "evidence_gated": lambda p: p.replace(_CONTROL_TIEBREAK, _EVIDENCE_GATED_TIEBREAK),
}

# Arms that REWRITE the prompt, and so depend on _CONTROL_TIEBREAK still being present to rewrite.
# `control` does not, which is why it stays runnable after the prompt moves on: scoring the prompt as
# it currently stands against the reviewed boundaries is a valid measurement on its own, and the
# stored segment_rows already hold what the previous prompt produced for the same documents.
_TRANSFORMING_ARMS = frozenset(ARMS) - {"control"}


def _ground_truth(session, document_id):
    """Reviewed (start, end) spans for a document, in page order."""
    rows = session.scalars(
        select(ReviewRow).where(ReviewRow.document_id == document_id).order_by(ReviewRow.idx)
    ).all()
    return [(r.start, r.end) for r in rows]


def _reviewed_documents(session):
    """Documents whose boundaries a human actually changed - the only usable ground truth.

    A document nobody reviewed matches its own model output perfectly and would score every arm at
    100%, diluting the result toward "no difference". Detected by comparing the latest successful
    segment job's rows against the current review rows.
    """
    out = []
    documents = session.scalars(select(Document).order_by(Document.created_at.desc())).all()
    for document in documents:
        job = session.scalars(
            select(Job)
            .where(Job.document_id == document.id, Job.kind == "segment", Job.state == "done")
            .order_by(Job.finished_at.desc())
        ).first()
        if job is None:
            continue
        model_spans = {(r.start, r.end) for r in job.segment_rows}
        truth = set(_ground_truth(session, document.id))
        if truth and model_spans and model_spans != truth:
            out.append((document, len(model_spans), len(truth)))
    return out


def _score(predicted, truth):
    """Compare predicted spans to reviewer-corrected spans.

    exact        - predicted span is exactly a reviewed span (the only unambiguously right answer)
    over_split   - predicted span sits INSIDE a reviewed span: the reviewer would merge it
    under_split  - predicted span CONTAINS more than one reviewed span: a document is hidden inside
                   another record, the expensive failure
    misaligned   - partial overlap, neither containment nor a match
    """
    truth_set = set(truth)
    result = {"predicted": len(predicted), "truth": len(truth)}
    exact = over = under = mis = 0
    for start, end in predicted:
        if (start, end) in truth_set:
            exact += 1
            continue
        if any(ts <= start and end <= te for ts, te in truth):
            over += 1
            continue
        contained = sum(1 for ts, te in truth if start <= ts and te <= end)
        if contained > 1:
            under += 1
            continue
        mis += 1
    result.update({"exact": exact, "over_split": over, "under_split": under, "misaligned": mis})
    result["exact_pct"] = round(100.0 * exact / len(predicted), 1) if predicted else 0.0
    return result


def _segment_once(pdf_path, total_pages, prompt, stage):
    """Run the window pass with `prompt` and return absolute-page spans. No DB writes.

    The prompt is injected by rebinding the module-level name segment_engine reads, which is the
    only seam available without changing production code for a test's convenience.
    """
    settings = segment_engine.get_settings()
    client = get_genai_client()
    windows = byte_budgeted_windows(
        pdf_path,
        total_pages,
        settings.window_overlap,
        int(settings.window_budget_mb * 1024 * 1024),
        settings.window_max_pages,
    )
    original = segment_engine.SEGMENTATION_PROMPT
    segment_engine.SEGMENTATION_PROMPT = prompt
    try:
        # Sequential on purpose: this is a measurement, and concurrent windows would contend with
        # whatever else is running for the same Vertex bucket, confounding the comparison.
        reports = [segment_engine._window_rows(pdf_path, ws, we, client) for ws, we in windows]
    finally:
        segment_engine.SEGMENTATION_PROMPT = original

    rows = segment_engine.merge_window_rows(reports, windows, total_pages)
    if stage == "full":
        for row in rows:
            row.setdefault("category", "100")
        rows, _stats = segment_engine.verify_and_merge(pdf_path, rows)
    return [(int(r["start"]), int(r["end"])) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated document ids")
    parser.add_argument("--arms", default="control,merge_biased")
    parser.add_argument("--stage", choices=("windows", "full"), default="windows")
    parser.add_argument("--list", action="store_true", help="list reviewed documents and exit")
    args = parser.parse_args()

    requested = {a.strip() for a in args.arms.split(",") if a.strip()}
    # Only the rewriting arms need the literal to still be there. Gating the check on them keeps the
    # loud failure where it protects something (a rewrite that silently no-ops) without blocking a
    # control-only run, which is a valid measurement of the prompt exactly as it stands today.
    if requested & _TRANSFORMING_ARMS and _CONTROL_TIEBREAK not in SEGMENTATION_PROMPT:
        sys.exit(
            f"arms {sorted(requested & _TRANSFORMING_ARMS)} rewrite the control tiebreak, but that "
            "sentence is no longer in SEGMENTATION_PROMPT (it changed on 2026-08-17) - so the "
            "rewrite would no-op and the A/B would silently measure nothing. Update "
            "_CONTROL_TIEBREAK, or run --arms control to score the current prompt as it stands."
        )

    with get_sessionmaker()() as session:
        if args.list or not args.docs:
            print("documents with reviewer-corrected boundaries (usable as ground truth):")
            for document, model_rows, truth_rows in _reviewed_documents(session):
                print(
                    f"  {document.id}  pages={document.page_count:>5}  "
                    f"model_rows={model_rows:>4}  reviewed_rows={truth_rows:>4}"
                )
            if not args.docs:
                return

        targets = []
        for document_id in [d.strip() for d in args.docs.split(",") if d.strip()]:
            document = session.get(Document, document_id)
            if document is None:
                sys.exit(f"unknown document {document_id}")
            targets.append(
                (
                    document.id,
                    document.stored_path,
                    document.page_count,
                    _ground_truth(session, document.id),
                )
            )

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        if arm not in ARMS:
            sys.exit(f"unknown arm {arm}; choose from {sorted(ARMS)}")

    header = (
        f"{'document':<38}{'arm':<14}{'rows':>6}{'truth':>7}{'exact':>7}"
        f"{'exact%':>8}{'over':>6}{'under':>7}{'misalign':>9}"
    )
    print(header)
    print("-" * len(header))
    totals = {
        arm: {"exact": 0, "over_split": 0, "under_split": 0, "misaligned": 0, "predicted": 0}
        for arm in arms
    }

    for document_id, pdf_path, total_pages, truth in targets:
        for arm in arms:
            prompt = ARMS[arm](SEGMENTATION_PROMPT)
            try:
                predicted = _segment_once(pdf_path, total_pages, prompt, args.stage)
            except Exception as exc:  # noqa: BLE001 - one bad document must not lose the whole run
                print(f"{document_id:<38}{arm:<14}FAILED: {exc}")
                continue
            s = _score(predicted, truth)
            for key in totals[arm]:
                totals[arm][key] += s[key]
            print(
                f"{document_id:<38}{arm:<14}{s['predicted']:>6}{s['truth']:>7}{s['exact']:>7}"
                f"{s['exact_pct']:>7.1f}%{s['over_split']:>6}{s['under_split']:>7}"
                f"{s['misaligned']:>9}"
            )

    print("-" * len(header))
    for arm in arms:
        t = totals[arm]
        pct = round(100.0 * t["exact"] / t["predicted"], 1) if t["predicted"] else 0.0
        print(
            f"{'TOTAL':<38}{arm:<14}{t['predicted']:>6}{'':>7}{t['exact']:>7}{pct:>7.1f}%"
            f"{t['over_split']:>6}{t['under_split']:>7}{t['misaligned']:>9}"
        )
    print(
        "\nRead both directions: an arm that lowers `over` while raising `under` has traded a "
        "reviewer click for a document hidden inside another record. That is a loss, not a win."
    )


if __name__ == "__main__":
    main()
