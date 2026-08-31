"""#153: does telling the classifier to read the AUTHOR's credential beat the current prompt?

Scored against reviewer corrections - a `review_rows.category` that disagrees with the
`segment_rows` category for the same page range under the newest done segment job. That
disagreement is the label, and it is the only ground truth this task has or will have.

Three arms, and the middle one is the point:

  A   production `llm_classify` itself. Not a reimplementation - the real function - so arm A
      cannot drift from what ships.
  A'  this script's own call path with the production instruction block VERBATIM. A CONTROL: if it
      does not track A, the harness is measuring its own scaffolding rather than the prompt, and
      nothing arm B says can be believed.
  B   this script's call path plus the three additions Levon proposed on #153.

Two exclusions, both because including the rows would measure something the change cannot touch:

* **Rows a RULE decides.** `classify()` short-circuits on `match_rules(title)` before the model is
  called, so no prompt can move them.
* **Rows with no stored page text.** Production escalates on the row's first pages; with none, it
  classifies the title alone, which is the case #153 already showed carries no credential.

Repeats, because temperature 0 is NOT deterministic on this API. One pass per arm measures sampling
noise and reports it as an effect.

Read-only: it writes nothing to the database and makes no change to any row.

    docker compose exec -T api python scripts/eval/classify_prompt_ab.py \
        --user-email someone@example.com --repeats 3 --out /tmp/ab153.json
"""

import argparse
import collections
import difflib
import inspect
import json
import os
import pathlib
import sys
import time

from google.genai import types
from sqlalchemy import select

# Running this as a FILE puts scripts/eval on sys.path, not backend/, so `app` is not importable
# without this. Same shape as segmentation_cap_ab.py; a hardcoded "/app" only works inside the
# container and this has to run from a checkout too.
_BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))

from app.models import Document, Job, PageText, ReviewRow, SegmentRow, User  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import classification  # noqa: E402
from app.services.classification import _catalog_text, _allowed_ids, llm_classify, match_rules  # noqa: E402
from app.services.genai_client import get_genai_client  # noqa: E402
from app.services.genai_retry import generate_with_retry  # noqa: E402
from app.services.segment_engine import _escalation_text  # noqa: E402

# Verbatim from `llm_classify`. Duplicated on purpose rather than imported: arm A' exists to prove
# this harness reproduces production, and a control that shares the production object cannot fail
# in the way it is there to detect. The drift guard below is what keeps the duplicate honest.
PRODUCTION_INSTRUCTIONS = (
    "Classify the medical-record document below into exactly one category id from this "
    "list. Choose 100 only if none of the specific categories fit.\n"
    "Administrative and correspondence documents - routing slips, cover letters, emails and "
    "faxes, legal declarations, proofs of service, records requests and record indexes - are "
    "100 even when they mention a QME/AME or another document type, because they accompany "
    "that document rather than being it."
)

# The #153 proposal. Three additions and nothing else - no taxonomy change, no cascade change, no
# change to the escalation window. Each sentence traces to one thing Adam said:
#   1. "the easiest way to tell is by the author's title (DC for Chiro, L.Ac for acupuncture, PT
#      for Physical Therapy), then for PR-2 type reports it would be something like MD, PA, NP".
#      AUTHOR is the load-bearing word: 10 of 29 credential-bearing pages carry more than one
#      credential, and a regex over "any credential on the page" scored 0 of 5 against the labels.
#   2. "Maybe occasionally it could be Category 2, in which case the only way to tell would be if
#      it mentions an 'Initial Evaluation' or 'Maximum Medical Improvement'."
#   3. "Sometimes they don't have the titles, in which case the only way would be to actually read
#      the content." Stated explicitly so the model does not fall back on a referrer's credential.
CREDENTIAL_INSTRUCTIONS = (
    "When the document is a treatment encounter note, progress note or re-evaluation and the "
    "heading alone does not settle it, identify the AUTHOR of the report - the clinician who "
    "wrote or signed it, not a referring, requesting or merely mentioned provider - and use their "
    "credential to choose: DC, L.Ac, PT or OT means 5; MD, DO, PA or NP means 1.\n"
    "Regardless of the author's credential, choose 2 if the document states that it is an Initial "
    "Evaluation or reports Maximum Medical Improvement.\n"
    "If no author credential is legible, decide from the content of the report itself rather than "
    "from the credential of anyone else named on the page."
)

# The NULL arm. Same volume and register as CREDENTIAL_INSTRUCTIONS - 3 sentences, ~110 words -
# and it names no category id, no document type and no clinical signal. It is deliberately the kind
# of generic care-and-diligence text a prompt author writes without thinking, because that is the
# thing being controlled for.
#
# WHY IT EXISTS. Arm B's damage on untouched rows is concentrated somewhere its own content cannot
# explain: 11 of its 24 changes move AWAY from 100, and its commonest single moves are `100 -> 7`
# and `100 -> 10` - two categories arm B says nothing whatever about. Neither of its additions,
# credentials or the Initial-Evaluation clause, reaches 7 or 10. So the erosion may not be a
# property of THIS text at all but of adding descriptive instruction text to a constrained-enum
# prompt: more instruction, more willingness to commit, and the catch-all is where committing costs
# something.
#
# The arm splits that cleanly and it is the only arm that can:
#   * if B-null erodes 100 as well, the erosion is structural, narrowing arm B cannot fix it, and
#     every future addition to this prompt pays the same tax - which is a constraint worth knowing
#     whatever happens to #153.
#   * if B-null does NOT, the erosion is content-specific, and suppressing arm B's 100-ward moves
#     is well founded rather than a guess.
#
# It does not test the credential hypothesis and is not meant to; that one is gated on Adam and is
# rescored by his answer. This one is not.
NULL_INSTRUCTIONS = (
    "Read the document text carefully and in full before you decide, rather than stopping at the "
    "first phrase that looks decisive, and weigh the passages against one another before settling "
    "on an answer.\n"
    "Where the text is garbled or partly illegible, work from the passages that are legible and do "
    "not let a scanning artefact stand in for evidence; a word you cannot read is not evidence of "
    "anything, in either direction.\n"
    "Take whatever time you need on this classification. There is no benefit to answering quickly, "
    "a considered answer is worth more than a fast one, and it is better to weigh the document "
    "properly than to reach for the first answer that seems to fit."
)

ARMS = ("A", "A-prime", "B", "B-null")
# #153's own scope: the two headings measured to draw five different categories across one corpus.
AMBIGUOUS = ("encounter note", "re-evaluation", "reevaluation", "re evaluation", "progress note")


def _squash(text: str) -> str:
    """Whitespace-, quote- and escape-free, so a source comparison survives the line breaks that
    split a long literal across several adjacent string constants.

    The `\\n` removal is what makes the two sides comparable at all: in the constant below it is one
    real newline, which `split()` eats, while in the SOURCE it is the two characters backslash-n,
    which it does not. Without this the guard fires on every run and says the prompt has drifted
    when only the escaping differs.
    """
    return "".join(text.split()).replace('"', "").replace("'", "").replace("\\n", "")


def check_no_drift() -> None:
    """Fail loudly if production's prompt no longer contains what arm A' claims it does.

    Without this the control silently stops being a control: someone edits `llm_classify`'s prompt,
    A' keeps sending the OLD text, and the A vs A' gap - the one signal that says whether this
    harness is faithful - starts reporting a difference that has nothing to do with arm B.
    """
    source = _squash(inspect.getsource(classification.llm_classify))
    if _squash(PRODUCTION_INSTRUCTIONS) not in source:
        sys.exit(
            "PRODUCTION_INSTRUCTIONS no longer matches llm_classify's prompt. Update it from the "
            "source before trusting any number this script prints."
        )


def check_prompt_parity() -> None:
    """Prove arm A' sends production's EXACT bytes, instead of inferring it from outcomes.

    A statistical comparison cannot settle this. Temperature 0 is not deterministic on this API, so
    A and A' disagreeing on a handful of rows is equally consistent with sampling noise and with the
    two arms sending genuinely different prompts - and those have opposite implications for whether
    arm B's result means anything. Capturing the request that production would have sent removes the
    ambiguity: either the bytes match or they do not.

    No model call happens. The spy raises, and `llm_classify` catches every exception and returns
    None, so the probe ends inside production's own error path having spent nothing.
    """
    probe = "PROMPT PARITY PROBE"
    captured: dict = {}

    def spy(_client, *, model, contents, config):
        captured["contents"] = contents
        raise RuntimeError("parity probe - no call intended")

    original = classification.generate_with_retry
    classification.generate_with_retry = spy
    try:
        llm_classify(probe)
    finally:
        classification.generate_with_retry = original

    mine = build_prompt(probe, PRODUCTION_INSTRUCTIONS)
    theirs = captured.get("contents")
    if theirs is None:
        sys.exit("parity probe never reached generate_with_retry; the call path has changed")
    if theirs != mine:
        for line in difflib.unified_diff(
            theirs.splitlines(), mine.splitlines(), "production", "arm A-prime", lineterm="", n=1
        ):
            print(line, flush=True)
        sys.exit("arm A-prime does not reproduce production's prompt; see the diff above")
    print("  prompt parity: arm A-prime is byte-identical to production", flush=True)


def build_prompt(text: str, instructions: str) -> str:
    """Production's prompt shape exactly: instructions, the live catalog, the document."""
    return (
        f"{instructions}\n\n{_catalog_text()}\n\nDocument:\n{text}\n\nReturn only the category id."
    )


def call(text: str, instructions: str, model: str) -> str | None:
    """One classification through the same client, config and retry path production uses."""
    allowed = _allowed_ids()
    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="text/x.enum",
        response_schema={"type": "STRING", "enum": list(allowed)},
        system_instruction=(
            "You classify California workers'-compensation medical-record document types. "
            "Return exactly one category id from the allowed set."
        ),
    )
    try:
        response = generate_with_retry(
            get_genai_client(),
            model=model,
            contents=build_prompt(text, instructions),
            config=config,
        )
    except Exception as exc:
        print(f"    call failed: {type(exc).__name__}: {exc}", flush=True)
        return None
    category = (response.text or "").strip()
    return category if category in set(allowed) else None


def newest_segment_job(session, document_id):
    return session.scalar(
        select(Job)
        .where(Job.document_id == document_id, Job.kind == "segment", Job.state == "done")
        .order_by(Job.id.desc())
        .limit(1)
    )


def labelled_rows(
    session, user_id: int, only_ambiguous: bool, uncorrected: bool = False
) -> list[dict]:
    """Every row this prompt could actually reach, with its escalation text.

    Returns dicts rather than ORM rows so the model's answer, the reviewer's label and the exact
    text the model saw all travel together into the results file.

    ``uncorrected`` inverts the selection: rows the reviewer LEFT ALONE rather than corrected. Those
    carry no active label, and they are the missing half of this experiment. Scoring only corrected
    rows measures how often a prompt change fixes something and is structurally blind to how often it
    BREAKS something, because a row nobody complained about is never in the sample. That is the exact
    gap #153 names - "I cannot diff a prompt the way I can diff a regex" - and it is the number that
    decides whether a +N on corrections is worth having.

    Acceptance-by-omission is weaker evidence than a correction: a reviewer who did not change a row
    probably agreed with it, but may simply not have looked closely. Treat a disturbance here as a
    risk signal, not as a counted error.
    """
    documents = session.scalars(select(Document).where(Document.user_id == user_id)).all()
    out, skipped = [], collections.Counter()
    for document in documents:
        job = newest_segment_job(session, document.id)
        if job is None:
            continue
        model_answer = {
            (row.start, row.end): row.category
            for row in session.scalars(select(SegmentRow).where(SegmentRow.job_id == job.id))
        }
        stored = {
            page_text.page: (page_text.text or "")
            for page_text in session.scalars(
                select(PageText).where(PageText.document_id == document.id)
            )
        }
        for row in session.scalars(
            select(ReviewRow).where(ReviewRow.document_id == document.id).order_by(ReviewRow.idx)
        ):
            was = model_answer.get((row.start, row.end))
            if was is None or (was == row.category) != uncorrected:
                continue  # wrong side of the corrected/uncorrected split for this run
            if only_ambiguous and not any(word in (row.title or "").lower() for word in AMBIGUOUS):
                skipped["title not in scope"] += 1
                continue
            if match_rules(row.title):
                skipped["a rule decides it, the model is never called"] += 1
                continue
            # Production's own escalation, page-for-page: the row's first pages, blanks skipped,
            # capped at the same character budget. Reading from `stored` only - never OCR here.
            text = _escalation_text(
                None, {"start": row.start, "end": row.end}, lambda page: stored.get(page, "")
            )
            if not text.strip():
                skipped["no stored page text"] += 1
                continue
            out.append(
                {
                    "document": document.id,
                    "idx": row.idx,
                    "pages": [row.start, row.end],
                    "model_said": was,
                    "reviewer_said": row.category,
                    "ambiguous_title": any(word in (row.title or "").lower() for word in AMBIGUOUS),
                    "text": text,
                }
            )
    for reason, count in skipped.items():
        print(f"  excluded {count}: {reason}", flush=True)
    return out


def run_arm(arm: str, text: str, model: str) -> str | None:
    if arm == "A":
        return llm_classify(text, model=model)
    if arm == "A-prime":
        return call(text, PRODUCTION_INSTRUCTIONS, model)
    if arm == "B-null":
        return call(text, f"{PRODUCTION_INSTRUCTIONS}\n{NULL_INSTRUCTIONS}", model)
    return call(text, f"{PRODUCTION_INSTRUCTIONS}\n{CREDENTIAL_INSTRUCTIONS}", model)


def majority(answers: list[str | None]) -> str | None:
    real = [a for a in answers if a is not None]
    return collections.Counter(real).most_common(1)[0][0] if real else None


def score(results: list[dict], subset=None) -> dict:
    """Per-arm accuracy over the rows in ``subset`` (all of them when None)."""
    rows = [r for r in results if subset is None or subset(r)]
    out: dict = {"rows": len(rows)}
    for arm in ARMS:
        verdicts = [majority(r["answers"][arm]) for r in rows]
        out[arm] = {
            "correct": sum(v == r["reviewer_said"] for v, r in zip(verdicts, rows)),
            # A row where the repeats did not all agree. Reported because an arm that is right by
            # coin-flip is not right, and this is the only thing that separates the two.
            "unstable": sum(len(set(r["answers"][arm])) > 1 for r in rows),
            "matched_model": sum(v == r["model_said"] for v, r in zip(verdicts, rows)),
            # The catch-all rate. @adrian-g, on arm B's damage: "Any future A/B on this call should
            # measure the 100 rate." It is the axis B-null exists to compare - the arm's whole
            # question is whether ANY added text erodes 100 - so a score without it cannot answer it.
            "chose_100": sum(v == "100" for v in verdicts),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B the classification prompt on #153.")
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="smoke-test on the first N rows")
    parser.add_argument("--only-ambiguous", action="store_true", help="#153's titles only")
    parser.add_argument(
        "--uncorrected",
        type=int,
        default=0,
        help="instead: sample N rows the reviewer LEFT ALONE, to measure what arm B disturbs",
    )
    parser.add_argument("--model", default="")
    parser.add_argument(
        "--out",
        default="classify_prompt_ab.json",
        help="results file; relative to the cwd. NOT /app/instance - that is read-only in the image",
    )
    args = parser.parse_args()

    check_no_drift()
    check_prompt_parity()
    model = args.model or get_settings().classify_model
    session = get_sessionmaker()()
    user = session.scalar(select(User).where(User.email == args.user_email))
    if user is None:
        sys.exit(f"no user with email {args.user_email}")

    print(f"scope: user {user.id}, model {model}, {args.repeats} repeat(s) per arm", flush=True)
    results = labelled_rows(session, user.id, args.only_ambiguous, bool(args.uncorrected))
    if args.uncorrected:
        # ROUND-ROBIN across documents, not the first N in order. Taking the first N by
        # (document, idx) drew all 150 rows of the first run from FIVE documents, two of them
        # supplying 115 - and a disturbance rate measured on five records cannot carry an
        # extrapolation to the whole corpus, which is the one thing this number is for. Stable
        # rather than random because the run has to be repeatable and there is no seeded RNG here.
        by_document: dict = {}
        for row in sorted(results, key=lambda r: (r["document"], r["idx"])):
            by_document.setdefault(row["document"], []).append(row)
        results, ranks = [], 0
        while len(results) < args.uncorrected:
            batch = [rows[ranks] for rows in by_document.values() if len(rows) > ranks]
            if not batch:
                break
            results.extend(batch[: args.uncorrected - len(results)])
            ranks += 1
        print(f"  sampled across {len(by_document)} document(s)", flush=True)
    if args.limit:
        results = results[: args.limit]
    kind = "untouched" if args.uncorrected else "corrected"
    print(f"  {len(results)} {kind} row(s) the prompt can reach", flush=True)
    if not results:
        sys.exit("nothing to measure")

    started = time.time()
    for n, row in enumerate(results, 1):
        arms = ("A", "B") if args.uncorrected else ARMS
        row["answers"] = {
            arm: [run_arm(arm, row["text"], model) for _ in range(args.repeats)] for arm in arms
        }
        if n % 5 == 0 or n == len(results):
            print(f"  {n}/{len(results)}  {time.time() - started:.0f}s", flush=True)

    if args.uncorrected:
        moved = [r for r in results if majority(r["answers"]["B"]) != majority(r["answers"]["A"])]
        report = {
            "model": model,
            "repeats": args.repeats,
            "mode": "disturbance on rows the reviewer left alone",
            "rows": len(results),
            "b_moves_vs_a": len(moved),
            # A moves away from the answer the reviewer accepted. The closest thing to a regression
            # count this data can support.
            "b_leaves_accepted_answer": sum(
                majority(r["answers"]["B"]) != r["reviewer_said"] for r in results
            ),
            "a_leaves_accepted_answer": sum(
                majority(r["answers"]["A"]) != r["reviewer_said"] for r in results
            ),
            "moves": collections.Counter(
                f"{majority(r['answers']['A'])}->{majority(r['answers']['B'])} (accepted "
                f"{r['reviewer_said']})"
                for r in moved
            ).most_common(15),
        }
        for row in results:
            row["chars"] = len(row.pop("text"))
        with open(args.out, "w") as handle:
            json.dump({"report": report, "rows": results}, handle, indent=2)
        print(json.dumps(report, indent=2), flush=True)
        print(f"row-level detail (no PHI): {os.path.abspath(args.out)}", flush=True)
        return

    report = {
        "model": model,
        "repeats": args.repeats,
        "all_labels": score(results),
        "ambiguous_titles_only": score(results, lambda r: r["ambiguous_title"]),
        # Blast radius: rows where B's verdict differs from production's. The number every
        # categorization PR carries, and the one a prompt change could not previously produce.
        "b_moves_vs_a": sum(
            majority(r["answers"]["B"]) != majority(r["answers"]["A"]) for r in results
        ),
        "a_prime_matches_a": sum(
            majority(r["answers"]["A-prime"]) == majority(r["answers"]["A"]) for r in results
        ),
    }
    # `text` is raw OCR and therefore PHI, and nothing downstream of here needs it - the answers,
    # the label and the row's identity are the whole analysis. Dropping it means the results file is
    # non-PHI and can sit anywhere, rather than being a second copy of patient text outside the
    # database that someone has to remember to delete.
    for row in results:
        row["chars"] = len(row.pop("text"))
    with open(args.out, "w") as handle:
        json.dump({"report": report, "rows": results}, handle, indent=2)
    print(json.dumps(report, indent=2), flush=True)
    print(f"row-level detail (no PHI): {os.path.abspath(args.out)}", flush=True)


if __name__ == "__main__":
    main()
