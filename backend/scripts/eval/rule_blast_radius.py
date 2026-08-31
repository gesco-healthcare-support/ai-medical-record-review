"""Read-only blast-radius diff: what if the PR-2 rule stopped claiming therapy-shaped headings?

The rule is /\bpr-?2\b|progress report|progress note|office visit|follow ?-? ?up|work status/ -> 1.
It overrides 12 reviewer corrections. This asks which ALTERNATIVE inside it is responsible, and how
many rows corpus-wide each one carries - the same shape of number every categorization PR has
carried, which is exactly what a prompt change cannot produce.

No model calls. Regex over stored titles only.
"""

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
from app.models import Document, Job, ReviewRow, SegmentRow  # noqa: E402
from app.services import classification  # noqa: E402

ALTS = [
    "\bpr-?2\b",
    "progress report",
    "progress note",
    "office visit",
    "follow ?-? ?up",
    "work status",
]
PR2 = re.compile("|".join(ALTS))

PR2_INDEX = next(
    i for i, (pattern, _c) in enumerate(classification._RULES) if "pr-?2" in pattern.pattern
)
print(f"PR-2 rule is _RULES[{PR2_INDEX}] -> {classification._RULES[PR2_INDEX][1]}")
print("  /" + classification._RULES[PR2_INDEX][0].pattern + "/")


def earlier_rule_wins(title):
    """True if some rule BEFORE the PR-2 one already claims this title (first-match-wins)."""
    if any(pattern.search(title) for pattern in classification._ADMIN_RULES):
        return True
    return any(pattern.search(title) for pattern, _c in classification._RULES[:PR2_INDEX])


session = get_sessionmaker()()
owner = collections.Counter()  # which alternative first claims the title
labelled = collections.Counter()  # among reviewer-corrected rows
rows_seen = set()
for doc in session.scalars(select(Document)):
    job = session.scalar(
        select(Job)
        .where(Job.document_id == doc.id, Job.kind == "segment", Job.state == "done")
        .order_by(Job.id.desc())
        .limit(1)
    )
    said = {}
    if job is not None:
        said = {
            (r.start, r.end): r.category
            for r in session.scalars(select(SegmentRow).where(SegmentRow.job_id == job.id))
        }
    for row in session.scalars(select(ReviewRow).where(ReviewRow.document_id == doc.id)):
        title = (row.title or "").strip().lower()
        if not PR2.search(title) or earlier_rule_wins(title):
            continue
        which = next((a for a in ALTS if re.search(a, title)), "?")
        owner[which] += 1
        was = said.get((row.start, row.end))
        if was is not None and was != row.category:
            labelled[(which, row.category)] += 1

print("ROWS CORPUS-WIDE the PR-2 rule claims, by which alternative fires FIRST:")
for alt, n in owner.most_common():
    print(f"  {n:>5}  /{alt}/")
print(f"  {sum(owner.values()):>5}  total")
print("\nAmong those, the REVIEWER-CORRECTED ones (alternative -> what the reviewer wanted):")
for (alt, want), n in labelled.most_common():
    print(
        f"  x{n:<3} /{alt}/ -> reviewer wanted {want}"
        + ("   <-- rule already right" if want == "1" else "")
    )
print(f"  {sum(labelled.values())} corrected rows touched by this rule")
