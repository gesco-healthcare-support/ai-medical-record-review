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

One run per arm is not a measurement. Temperature is 0, but the same prompt on the same document was
measured at 17 rows / 100% exact and then 13 rows / 76.9% - a 23-point swing from nothing but
run-to-run variation. Prompt edits move results by a few points, so a single run per arm cannot
resolve them: pass --repeats 3 or more and read the spread the summary prints. Prefer repeats over
breadth when the budget is fixed; more documents at one run each does not address this.

Usage. PYTHONPATH must include the backend root: these scripts import `app.*` but the package is
not installed into the venv, and Python puts only the SCRIPT's directory on sys.path, not the cwd.
Without it the run dies on `ModuleNotFoundError: No module named 'app'`.

    docker compose exec -T -e PYTHONPATH=/app api \
        python scripts/eval/segmentation_boundary_ab.py --list
    docker compose exec -T -e PYTHONPATH=/app api \
        python scripts/eval/segmentation_boundary_ab.py \
        --docs 2baa5747-...,d059fb11-... --arms control,merge_biased --repeats 3

A long run should be detached and polled rather than held open, and `python -u` passed: without
unbuffered output the log file stays empty until the process exits, which on a two-hour run is
indistinguishable from a hang.

    docker compose exec -d api sh -c "PYTHONPATH=/app python -u \
        scripts/eval/segmentation_boundary_ab.py --docs ... --arms ... --repeats 3 > /tmp/ab.log 2>&1"

Locally, from `backend/`: `PYTHONPATH=. uv run python scripts/eval/segmentation_boundary_ab.py`.
Expect ~45s of import before anything prints - segment_engine pulls in the torch classifier.

Cost: one Vertex call per window per document per arm (~3-5 windows for a 300-page file).
Classification and the verify pass are skipped by default, so this isolates the prompt's effect on
boundaries and keeps the run cheap; --stage full adds the verify pass.
"""

import argparse
import sys

import ab_stats  # same directory; Python puts the SCRIPT's dir on sys.path
import prompt_variants
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
    # WHOLE-PROMPT arms, ignoring the live text entirely. #104 rewrote the prompt in many places at
    # once, so no string replace reproduces it and a hand-built approximation would measure something
    # other than what shipped. See prompt_variants.py for provenance of each.
    "date_title": lambda _p: prompt_variants.DATE_TITLE_104,
    "encounter_date": lambda _p: prompt_variants.ENCOUNTER_DATE,
}

# Arms that PATCH the live prompt by string replacement, and so depend on _CONTROL_TIEBREAK still
# being present to find. Listed explicitly rather than derived as "everything but control", because
# the whole-prompt arms below substitute the text outright and do not care what the live prompt says
# - deriving the set would make them fail for a reason that does not apply to them.
_TRANSFORMING_ARMS = frozenset({"merge_biased", "evidence_gated"})

# One width for the per-run table and the TOTAL rows beneath it, so the two line up as one table.
_RUN_HEADER = (
    f"{'document':<38}{'arm':<14}{'rep':>4}{'rows':>6}{'truth':>7}{'exact':>7}"
    f"{'exact%':>8}{'over':>6}{'under':>7}{'misalign':>9}"
)


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
    # `spans` travels with the counts so boundaries can be scored per page downstream. The span counts
    # below treat every near miss as equally wrong, which discards most of what a run measures; see
    # ab_stats.boundary_score.
    result = {"predicted": len(predicted), "truth": len(truth), "spans": list(predicted)}
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
        # The WIDE net, pinned rather than inherited. `VERIFY_TRIGGERED_ONLY` is a live-box setting
        # meant to be flipped and measured (#177), and both arms of this A/B run through here - so
        # inheriting it would narrow the net under a comparison built to hold everything but the
        # prompt constant, and nothing in the output would say so. False, not the current default,
        # because every number this harness has produced was measured on the wide net.
        rows, _stats = segment_engine.verify_and_merge(pdf_path, rows, triggered_only=False)
    return [(int(r["start"]), int(r["end"])) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", help="comma-separated document ids")
    parser.add_argument("--arms", default="control,merge_biased")
    parser.add_argument("--stage", choices=("windows", "full"), default="windows")
    parser.add_argument("--list", action="store_true", help="list reviewed documents and exit")
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        metavar="N",
        help="runs per arm per document (default 1). Anything above 1 exposes the run-to-run "
        "variance that makes single runs unreadable: the same prompt on the same document was "
        "measured at 17 rows / 100%% exact and then 13 rows / 76.9%%. Cost scales linearly.",
    )
    args = parser.parse_args()

    if args.repeats < 1:
        sys.exit("--repeats must be at least 1")

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

    runs = _collect_runs(targets, arms, args.stage, args.repeats)
    _report(
        runs, {document_id: truth for document_id, _p, _n, truth in targets}, arms, args.repeats
    )


def _collect_runs(targets, arms, stage, repeats):
    """Run every arm `repeats` times per document, printing each run as it lands.

    Returns {(document_id, arm): [score, ...]}. Printed incrementally and flushed because a full run
    takes hours: a crash at document five should still leave the first four readable in the log.
    """
    print(_RUN_HEADER, flush=True)
    print("-" * len(_RUN_HEADER), flush=True)
    runs = {}
    for document_id, pdf_path, total_pages, truth in targets:
        for rep in range(1, repeats + 1):
            # Arms interleaved WITHIN each repeat, rather than one arm run to completion and then the
            # next. The comparison is paired, and this is what makes it paired: whatever the shared
            # Vertex bucket, the model version, and the box are doing in this minute hits both arms,
            # so drift across a multi-hour run cancels instead of loading entirely onto whichever arm
            # happened to run second.
            for arm in arms:
                score = _run_one(document_id, pdf_path, total_pages, truth, arm, stage, rep)
                if score is not None:
                    runs.setdefault((document_id, arm), []).append(score)
    return runs


def _run_one(document_id, pdf_path, total_pages, truth, arm, stage, rep):
    """One arm on one document once. Returns the score, or None if the run failed.

    A failure is not fatal and not silent: the document is dropped from every arm's total downstream,
    because a partial arm cannot be summed against a complete one.
    """
    prompt = ARMS[arm](SEGMENTATION_PROMPT)
    try:
        predicted = _segment_once(pdf_path, total_pages, prompt, stage)
    except Exception as exc:  # noqa: BLE001 - one bad window must not lose the whole run
        print(f"{document_id:<38}{arm:<14}{rep:>4}  FAILED: {exc}", flush=True)
        return None
    s = _score(predicted, truth)
    print(
        f"{document_id:<38}{arm:<14}{rep:>4}{s['predicted']:>6}{s['truth']:>7}"
        f"{s['exact']:>7}{s['exact_pct']:>7.1f}%{s['over_split']:>6}"
        f"{s['under_split']:>7}{s['misaligned']:>9}",
        flush=True,
    )
    return s


def _report(runs, truths, arms, repeats):
    """Everything after the last Vertex call: per-document spreads, comparable totals, the verdict.

    `truths` maps document_id to its reviewed spans, in the order the documents were run.
    """
    document_ids = list(truths)
    summaries = ab_stats.summarize(runs)
    kept, dropped = ab_stats.comparable_documents(document_ids, arms, runs, repeats)

    if repeats > 1:
        _print_per_document(document_ids, arms, summaries)
    _print_totals(arms, kept, dropped, runs)
    if len(arms) == 2:
        _print_verdict(arms, kept, summaries, repeats)
    # Last, because it is the strongest evidence in the report and should be the final word. The
    # whole-span verdict above compares ONE number per document, so at six documents it cannot reach
    # significance even in principle; this compares one decision per boundary from the same runs.
    _print_boundaries(kept, arms, runs, truths, repeats)

    print(
        "\nRead both directions: an arm that lowers `over` (fp) while raising `under` (fn) has traded "
        "a reviewer click for a document hidden inside another record. That is a loss, not a win."
    )


def _print_boundaries(document_ids, arms, runs, truths, repeats):
    """Boundary-level scoring: one observation per page decision instead of one per document.

    fp is a merge click the reviewer has to make; fn is a document buried inside another record. The
    paired test at the end pools every boundary the two arms disagree about, which is why it can reach
    a conclusion that six whole-document comparisons cannot.
    """
    if not document_ids:
        return
    print("\nBOUNDARY LEVEL - a page the reviewer marked as a document start, or did not:")
    header = (
        f"{'document':<38}{'arm':<14}{'truth':>6}{'tp':>5}{'fp':>5}{'fn':>5}"
        f"{'prec':>7}{'recall':>8}{'F1':>7}{'unstable':>10}"
    )
    print(header)
    print("-" * len(header))
    pooled = {
        arm: {"tp": 0, "fp": 0, "fn": 0, "truth_boundaries": 0, "unstable": 0} for arm in arms
    }
    decided = {arm: {} for arm in arms}
    for document_id in document_ids:
        for arm in arms:
            votes = ab_stats.boundary_votes(runs.get((document_id, arm), []))
            starts = ab_stats.majority_boundaries(votes)
            decided[arm][document_id] = starts
            unstable = len(ab_stats.unstable_boundaries(votes))
            b = ab_stats.boundary_score(starts, truths[document_id])
            for key in ("tp", "fp", "fn", "truth_boundaries"):
                pooled[arm][key] += b[key]
            pooled[arm]["unstable"] += unstable
            print(
                f"{document_id:<38}{arm:<14}{b['truth_boundaries']:>6}{b['tp']:>5}{b['fp']:>5}"
                f"{b['fn']:>5}{b['precision'] * 100:>6.1f}%{b['recall'] * 100:>7.1f}%"
                f"{b['f1'] * 100:>6.1f}%{unstable:>10}"
            )

    print("-" * len(header))
    for arm in arms:
        t = pooled[arm]
        prec = t["tp"] / (t["tp"] + t["fp"]) if (t["tp"] + t["fp"]) else 0.0
        rec = t["tp"] / (t["tp"] + t["fn"]) if (t["tp"] + t["fn"]) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(
            f"{'POOLED':<38}{arm:<14}{t['truth_boundaries']:>6}{t['tp']:>5}{t['fp']:>5}{t['fn']:>5}"
            f"{prec * 100:>6.1f}%{rec * 100:>7.1f}%{f1 * 100:>6.1f}%{t['unstable']:>10}"
        )
    if repeats > 1:
        total_unstable = sum(pooled[arm]["unstable"] for arm in arms)
        print(
            f"\n  `unstable` counts boundaries the arm did not decide the same way in all {repeats} "
            f"runs ({total_unstable} across all arms). That is the run-to-run noise, located: those "
            "pages are where a prompt change has something to bite on."
        )
    else:
        print(
            "\n  `unstable` is 0 by construction at --repeats 1 - with one run there is nothing to "
            "disagree with. It is not a claim that the boundaries are stable."
        )

    if len(arms) != 2:
        return
    baseline, contender = arms
    baseline_only = contender_only = 0
    for document_id in document_ids:
        pair = ab_stats.paired_boundary_compare(
            decided[baseline][document_id], decided[contender][document_id], truths[document_id]
        )
        baseline_only += pair["baseline_only"]
        contender_only += pair["contender_only"]
    p = ab_stats.sign_test_p(contender_only, baseline_only)
    print(
        f"\n  Paired over boundaries where exactly one arm is right: {contender} correct on "
        f"{contender_only}, {baseline} correct on {baseline_only}. McNemar exact p = {p:.4f}."
    )
    if baseline_only + contender_only == 0:
        print("  The two arms made identical boundary decisions everywhere - nothing to separate.")


def _print_per_document(document_ids, arms, summaries):
    """Mean and observed range per arm per document - the spread a single run cannot show."""
    print("\nPER DOCUMENT, mean across repeats with the observed range in brackets:")
    header = (
        f"{'document':<38}{'arm':<14}{'n':>3}{'exact':>7}{'range':>10}"
        f"{'exact%':>8}{'over':>7}{'under':>7}"
    )
    print(header)
    print("-" * len(header))
    for document_id in document_ids:
        for arm in arms:
            e = summaries.get((document_id, arm))
            if e is None:
                print(f"{document_id:<38}{arm:<14}{'0':>3}  no completed run")
                continue
            span = f"[{e['exact']['lo']}-{e['exact']['hi']}]"
            print(
                f"{document_id:<38}{arm:<14}{e['n']:>3}{e['exact']['mean']:>7.1f}{span:>10}"
                f"{e['exact_pct_mean']:>7.1f}%{e['over_split']['mean']:>7.1f}"
                f"{e['under_split']['mean']:>7.1f}"
            )


def _print_totals(arms, kept, dropped, runs):
    """Totals over the comparable set only, with every exclusion named rather than absorbed."""
    print(f"\nTOTAL over the {len(kept)} document(s) every arm completed in full:")
    # Column labels reprinted: the per-document block sits between here and the run table, so by this
    # point the reader has lost the header and a bare row of numbers is guesswork.
    print(_RUN_HEADER)
    print("-" * len(_RUN_HEADER))
    for arm, t in ab_stats.totals(arms, kept, runs).items():
        print(
            f"{'TOTAL':<38}{arm:<14}{'':>4}{t['predicted']:>6.0f}{'':>7}{t['exact']:>7.1f}"
            f"{t['exact_pct']:>7.1f}%{t['over_split']:>6.1f}{t['under_split']:>7.1f}"
            f"{t['misaligned']:>9.1f}"
        )
    if not dropped:
        return
    # Named, not silently omitted. A total that quietly covered a different set of documents per arm
    # is what made the 2026-08-17 comparison unusable, and an exclusion nobody prints is the same bug
    # wearing a fix.
    print(
        f"\nEXCLUDED from the total - {len(dropped)} document(s) where at least one arm did not "
        "finish, so no arm may count them:"
    )
    for document_id, missing in dropped:
        detail = ", ".join(f"{arm} short {count} run(s)" for arm, count in missing.items())
        print(f"  {document_id}  {detail}")


def _print_verdict(arms, document_ids, summaries, repeats):
    """State whether the arm gap clears the run-to-run noise, rather than leaving totals to imply it.

    Only for a two-arm run, because the paired sign test compares exactly two things. With more arms,
    re-run the pair that matters.
    """
    baseline, contender = arms
    v = ab_stats.compare(baseline, contender, document_ids, summaries)
    if not v["rows"]:
        return

    print(f"\nVERDICT, {contender} against {baseline}, paired per document on exact COUNT:")
    print(
        "gap is positive when {} is ahead. `beats noise` asks only whether the gap is".format(
            contender
        )
    )
    print(
        "bigger than the arm's own run-to-run spread - it does NOT say which arm won; read the sign."
    )
    line = (
        f"{'document':<38}{baseline:>16}{contender:>16}{'gap':>8}"
        f"{'self-noise':>12}{'beats noise':>13}"
    )
    print(line)
    print("-" * len(line))
    for row in v["rows"]:
        beats = {True: "yes", False: "no", None: "n/a"}[row["clears"]]
        print(
            f"{row['document_id']:<38}{row['baseline_mean']:>16.1f}{row['contender_mean']:>16.1f}"
            f"{row['gap']:>+8.1f}{row['noise']:>12}{beats:>13}"
        )

    decided = v["wins"] + v["losses"]
    print(
        f"\n{contender} ahead on {v['wins']} document(s), behind on {v['losses']}, tied on "
        f"{v['ties']}. Sign test across documents: p = {v['p']:.3f}."
    )
    if repeats < 2:
        print(
            "  Noise is UNMEASURED at --repeats 1: a single run has no observed spread, which is not "
            "the same as no noise. Nothing here separates signal from variance - re-run with "
            "--repeats 3 before drawing a conclusion."
        )
    else:
        ahead = sum(1 for r in v["rows"] if r["clears"] and r["gap"] > 0)
        behind = sum(1 for r in v["rows"] if r["clears"] and r["gap"] < 0)
        print(
            f"  Of {len(v['rows'])} document(s), {v['cleared']} show a gap bigger than the arm's own "
            f"spread: {ahead} favouring {contender} and {behind} favouring {baseline}. On the "
            f"remaining {len(v['rows']) - v['cleared']}, each arm varies against itself by at least "
            "as much as the two differ, so those are not evidence either way."
        )
    if v["p"] >= 0.05:
        # The floor is computed, not quoted, because it depends entirely on how many documents were
        # decided: at 3 documents even a clean sweep cannot beat 0.25, so "not significant" there says
        # nothing about the prompt and everything about the sample size.
        floor = ab_stats.sign_test_p(decided, 0)
        print(
            f"  p >= 0.05, so the direction is not established. Before reading that as a near miss, "
            f"note the ceiling: with {decided} decided document(s) the smallest attainable p is "
            f"{floor:.3f}, reached only by a clean sweep."
            + ("" if floor < 0.05 else " No result at this sample size can reach 0.05 at all.")
        )


if __name__ == "__main__":
    main()
