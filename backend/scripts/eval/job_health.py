"""Is the pipeline healthy? The obvious query says yes-but-worryingly and it is wrong (#218).

`SELECT state, count(*) FROM jobs GROUP BY state` invites exactly one reading - "everything that is
not `done` went wrong" - and three of the five terminal states are not failures at all:

  - `interrupted` is a job a restart orphaned. Measured 2026-08-31: 12 of 18 fell on a single day in
    July and none had appeared in the seven weeks since. That is the signature of deploys, not of a
    pipeline dropping one job in eight.
  - `cancelled` is a reviewer pressing Stop. A success.
  - `needs_attention` is a run that finished and NAMED the sub-documents it could not do. That is
    the resumable design working, not a fault.

So the naive number overstated the problem - "27.4% did not complete cleanly" - and simultaneously
hid the one real signal, transient Vertex failures, inside two categories of non-problem. This
script prints the naive reading and the corrected one side by side, because the point is not to
publish a better number but to make the worse one visibly wrong.

The taxonomy itself is `app.worker.failures.job_outcome`, deliberately not reimplemented here: it is
the persisted half of the same distinction `classify_failure` makes at runtime, and a query carrying
its own copy would drift from the code that writes the rows.

Denominators, both of which the naive query gets wrong:

  - `in_flight` jobs are EXCLUDED. A rate over jobs that have not finished moves as they finish.
  - Every uploaded copy of a PDF is counted, and that is correct HERE: this measures the pipeline's
    behaviour on runs it actually performed, so a record processed twice really did occupy the
    pipeline twice. That is the opposite of the corpus rule in `corpus.py`, which governs statements
    about DOCUMENTS - see the note at the bottom of the output.

FILTER BEFORE READING ANYTHING AS CURRENT. A corpus-wide count answers "what is in the data", never
"what does the code do" - and the two differ here. All seven raw-vendor-message failures on the box
are from 6-15 July with `build_sha` NULL, i.e. before friendly messages and before build stamping;
quoting them as current behaviour would have been wrong. `--build` and `--since` exist for that, and
`--build` is the sharper of the two.

PHI: prints job counts, states, dates, kinds and the app's own error CONSTANTS. Never a title, never
a page, never a filename. The RAW-message list is the one place vendor text is echoed; it is
truncated, and by definition contains no PHI because it is an API error rather than record content.

Usage:

    DATABASE_URL=... SECRET_KEY=x SECURITY_PASSWORD_SALT=x \\
      .venv/Scripts/python.exe -m scripts.eval.job_health [--by-kind] [--build 55c0048]
"""

from __future__ import annotations

import argparse
import collections
from datetime import datetime
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
if len(_HERE.parents) > 2:
    sys.path.insert(0, str(_HERE.parents[2]))

from app.errors import GENERIC_USER_MESSAGE  # noqa: E402
from app.worker.failures import JOB_OUTCOMES, is_failure, job_outcome  # noqa: E402

# The states the naive reading treats as "did not complete cleanly". Kept explicitly so the report
# can show that number rather than describing it - a corrected figure with nothing to correct is
# unpersuasive to whoever has the old one in a backlog entry.
_NAIVE_BAD_STATES = ("error", "interrupted", "cancelled", "needs_attention")


def classify(rows) -> dict:
    """Bucket `(state, error)` pairs into the outcome taxonomy, with both readings computed.

    `rows` is any iterable of objects or tuples carrying `state` and `error`. Returns the counts,
    the naive rate the plain state query implies, and the failure rate over FINISHED jobs only.

    Pure, so the arithmetic that decides whether anyone worries is testable without a database -
    the separation `ab_stats.py` exists to enforce, for the same reason.
    """
    outcomes: collections.Counter[str] = collections.Counter()
    states: collections.Counter[str] = collections.Counter()
    unrecognized: collections.Counter[str] = collections.Counter()
    for row in rows:
        state, error = (row.state, row.error) if hasattr(row, "state") else (row[0], row[1])
        outcome = job_outcome(state, error)
        outcomes[outcome] += 1
        states[state] += 1
        if outcome == "failed_unknown" and not (error or "").startswith(GENERIC_USER_MESSAGE):
            # The message itself, so a new error type appears as itself instead of being folded into
            # a bucket it does not belong to.
            #
            # The GENERIC message is excluded because it is RECOGNIZED - it is what
            # `user_facing_message` returns when it does not know either, so it is a correctly
            # classified unknown rather than a gap in this taxonomy. Listing it here said "teach
            # failures.py about this" about a message failures.py already knows, on the first real
            # run. Anything that reaches this list is a string `user_facing_message` never
            # produces, which means it did not come through it - see the note in `render`.
            unrecognized[(error or "").strip()[:90] or f"<no message, state={state}>"] += 1

    total = sum(outcomes.values())
    finished = total - outcomes["in_flight"]
    failures = sum(count for outcome, count in outcomes.items() if is_failure(outcome))
    naive_bad = sum(states[state] for state in _NAIVE_BAD_STATES)
    return {
        "total": total,
        "finished": finished,
        "outcomes": dict(outcomes),
        "states": dict(states),
        "unrecognized": dict(unrecognized),
        "failures": failures,
        # Over ALL jobs, which is what the plain state query does - part of why it misleads.
        "naive_rate": naive_bad / total if total else 0.0,
        "failure_rate": failures / finished if finished else 0.0,
    }


def render(summary: dict, label: str = "all jobs") -> str:
    """The report. Naive reading first, then what the states actually say."""
    lines = [f"=== {label}: {summary['total']} jobs, {summary['finished']} finished ==="]
    if not summary["total"]:
        return lines[0] + "\n  (none)"

    lines.append("")
    lines.append(
        f"  the plain state query implies: {summary['naive_rate']:.1%} "
        f"'did not complete cleanly'  <- WRONG, see below"
    )
    lines.append(
        f"  failures over FINISHED jobs:   {summary['failure_rate']:.1%} "
        f"({summary['failures']} of {summary['finished']})"
    )
    lines.append("")

    width = max(len(name) for name in JOB_OUTCOMES)
    for outcome, meaning in JOB_OUTCOMES.items():
        count = summary["outcomes"].get(outcome, 0)
        if not count:
            continue
        share = count / summary["total"]
        mark = "FAIL" if is_failure(outcome) else "    "
        lines.append(f"  {mark} {outcome:<{width}} {count:5d}  {share:6.1%}  {meaning}")

    absent = [o for o in JOB_OUTCOMES if not summary["outcomes"].get(o)]
    if absent:
        lines.append(f"\n  none at all: {', '.join(absent)}")

    if summary["unrecognized"]:
        # These did NOT come through `user_facing_message`, which never returns anything but its own
        # constants. So each one is a raw vendor string that reached `Job.error` by another path and
        # is being shown to a reviewer - the opposite of what tasks.py:215 promises.
        lines.append(
            "\n  RAW messages that never came through user_facing_message (a reviewer sees these):"
        )
        for message, count in sorted(summary["unrecognized"].items(), key=lambda kv: -kv[1]):
            lines.append(f"    {count:5d}  {message}")

    lines.append(
        "\n  Counts every uploaded copy, deliberately: this describes RUNS the pipeline performed."
        "\n  Any statement about documents instead must deduplicate by sha256 - see corpus.py."
    )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - I/O wrapper around the tested functions above
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--kind", default=None, help="restrict to one job kind (segment, summarize, ...)"
    )
    ap.add_argument("--since", default=None, help="only jobs created on or after this date")
    ap.add_argument("--build", default=None, help="only jobs stamped with this build_sha prefix")
    ap.add_argument(
        "--by-kind", action="store_true", help="one report per kind as well as the total"
    )
    args = ap.parse_args()

    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import Job

    query = select(Job.state, Job.error, Job.kind)
    if args.kind:
        query = query.where(Job.kind == args.kind)
    if args.since:
        # Parsed, not passed through: `created_at` is a naive DateTime, and Postgres refuses
        # `timestamp >= varchar` outright rather than coercing - so the string form silently never
        # worked. It raised on the first real use, which is the only reason it is not still here.
        query = query.where(Job.created_at >= datetime.fromisoformat(args.since))
    if args.build:
        query = query.where(Job.build_sha.startswith(args.build))

    with get_sessionmaker()() as session:
        rows = session.execute(query).all()

    scope = args.kind or "all kinds"
    if args.since:
        scope += f" since {args.since}"
    print(render(classify([(r.state, r.error) for r in rows]), scope))

    if args.by_kind:
        for kind in sorted({r.kind for r in rows}):
            subset = [(r.state, r.error) for r in rows if r.kind == kind]
            print()
            print(render(classify(subset), kind))


if __name__ == "__main__":
    main()
