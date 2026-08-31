"""What would it COST to take `progress note` out of the PR-2 rule?

The rule claims 1,372 rows and only ~10 were ever corrected, so under acceptance-by-omission it is
right on the overwhelming majority of what it claims. Deleting an alternative therefore re-opens
every one of those rows to the cascade, and the question is not "is the rule ever wrong" (it is,
20 times) but "would the cascade do better or worse on the ~523 nobody objected to".

This measures exactly that: rows where `progress note` fires, an earlier rule does not, and the
reviewer LEFT the category alone - then asks arms A and B what they would say if the rule were gone.

Agreement with the accepted category is the score. A high number means the rule is doing work the
cascade would do anyway and can be removed cheaply; a low number means the rule is load-bearing and
removing it trades 10 fixes for hundreds of regressions.

No writes. Sampled in a stable order so the run is repeatable.
"""

import argparse
import collections
import re
import pathlib
import sys

# Running this as a FILE puts scripts/eval on sys.path, not backend/, so `app` is not importable
# without this. Same shape as segmentation_cap_ab.py; a hardcoded "/app" only works inside the
# container and this has to run from a checkout too.
_BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
from sqlalchemy import select  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.models import Document, Job, PageText, ReviewRow, SegmentRow, User  # noqa: E402
from app.services import classification  # noqa: E402
from app.services.segment_engine import _escalation_text  # noqa: E402
from classify_prompt_ab import (  # noqa: E402
    CREDENTIAL_INSTRUCTIONS,
    PRODUCTION_INSTRUCTIONS,
    call,
    majority,
)
from app.config import get_settings  # noqa: E402

PR2_INDEX = next(i for i, (p, _c) in enumerate(classification._RULES) if "pr-?2" in p.pattern)

parser = argparse.ArgumentParser(description="Cost of removing one alternative from the PR-2 rule.")
parser.add_argument(
    "--user-email",
    required=True,
    help="scope: whose documents to read. Required rather than defaulted - a shared box hosts "
    "several people's records and reading someone else's is not this script's business.",
)
parser.add_argument("--term", default="progress note", help="the rule alternative under test")
parser.add_argument("--sample", type=int, default=40)
parser.add_argument("--repeats", type=int, default=3, help="temperature 0 is not deterministic")
args = parser.parse_args()

TARGET = re.compile(args.term)
SAMPLE, REPEATS = args.sample, args.repeats
model = get_settings().classify_model


def earlier_rule_wins(title):
    if any(p.search(title) for p in classification._ADMIN_RULES):
        return True
    return any(p.search(title) for p, _c in classification._RULES[:PR2_INDEX])


session = get_sessionmaker()()
user = session.scalar(select(User).where(User.email == args.user_email))
if user is None:
    sys.exit(f"no user with email {args.user_email}")
candidates = []
for doc in session.scalars(select(Document).where(Document.user_id == user.id)):
    job = session.scalar(
        select(Job)
        .where(Job.document_id == doc.id, Job.kind == "segment", Job.state == "done")
        .order_by(Job.id.desc())
        .limit(1)
    )
    if job is None:
        continue
    said = {
        (r.start, r.end): r.category
        for r in session.scalars(select(SegmentRow).where(SegmentRow.job_id == job.id))
    }
    stored = {
        p.page: (p.text or "")
        for p in session.scalars(select(PageText).where(PageText.document_id == doc.id))
    }
    for row in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc.id)):
        title = (row.title or "").strip().lower()
        if not TARGET.search(title) or earlier_rule_wins(title):
            continue
        if said.get((row.start, row.end)) != row.category:
            continue  # corrected rows are already measured elsewhere; this is the accepted set
        text = _escalation_text(
            None, {"start": row.start, "end": row.end}, lambda p: stored.get(p, "")
        )
        if not text.strip():
            continue
        candidates.append((doc.id, row.idx, row.category, text))

candidates.sort(key=lambda c: (c[0], c[1]))
sample = candidates[:SAMPLE]
print(
    f"{len(candidates)} accepted `progress note` rows with page text; measuring {len(sample)}",
    flush=True,
)
score = collections.Counter()
answers = {"A": collections.Counter(), "B": collections.Counter()}
for n, (doc_id, idx, accepted, text) in enumerate(sample, 1):
    for arm, instructions in (
        ("A", PRODUCTION_INSTRUCTIONS),
        ("B", PRODUCTION_INSTRUCTIONS + "\n" + CREDENTIAL_INSTRUCTIONS),
    ):
        verdict = majority([call(text, instructions, model) for _ in range(REPEATS)])
        answers[arm][verdict] += 1
        score[(arm, verdict == accepted)] += 1
    if n % 5 == 0 or n == len(sample):
        print(f"  {n}/{len(sample)}", flush=True)

print(f"\nAll {len(sample)} rows are accepted at category 1 (the rule's answer).")
for arm in ("A", "B"):
    agree = score[(arm, True)]
    print(
        f"  arm {arm}: agrees with the accepted category on {agree}/{len(sample)} "
        f"({100 * agree / max(1, len(sample)):.0f}%)   answers={dict(answers[arm].most_common())}"
    )
