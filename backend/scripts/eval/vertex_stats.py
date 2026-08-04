"""Read, reset, or watch the per-model Vertex call counters. Read-only against the DB; no model calls.

The counters come from app.services.genai_metrics, which records every ATTEMPT made through
generate_with_retry. Before this existed, a 429 that a retry later recovered from left no trace
anywhere, so "did rejections rise when we raised the concurrency?" could not be answered.

Bracket an experiment with it:

    python scripts/eval/vertex_stats.py --reset      # zero the counters
    ... run the workload ...
    python scripts/eval/vertex_stats.py              # read the result

or watch a run in progress, which is what the PIPELINE_WORKERS ramp wants:

    python scripts/eval/vertex_stats.py --watch 30   # print a delta every 30s

On the server, run it inside a container that already has the app env:

    docker compose exec -T api python scripts/eval/vertex_stats.py
"""

import argparse
import time

from app.services import genai_metrics

# Printed in this order because it reads as a funnel: what we asked for, what got through, what
# bounced. Anything else Redis holds is appended after these.
_ORDER = (
    genai_metrics.OUTCOME_ACCEPTED,
    genai_metrics.OUTCOME_RATE_LIMITED,
    genai_metrics.OUTCOME_SERVER_ERROR,
    genai_metrics.OUTCOME_TRANSPORT,
    genai_metrics.OUTCOME_EXHAUSTED,
)


def _attempts(fields) -> int:
    """Real requests to Vertex. Excludes `exhausted`, which marks a logical call, not an attempt."""
    return sum(int(fields.get(name, 0)) for name in genai_metrics.ATTEMPT_OUTCOMES)


def _render(snapshot, elapsed=None) -> str:
    if not snapshot:
        return "no counters recorded (workload has not run, or Redis is unreachable)"
    lines = []
    header = f"{'model':<28}{'attempts':>9}{'accepted':>9}{'429':>7}{'5xx':>6}{'transport':>10}"
    header += f"{'reject%':>9}{'wait_s':>9}"
    if elapsed:
        header += f"{'acc/min':>9}"
    lines.append(header)
    lines.append("-" * len(header))

    totals: dict[str, int] = {}
    for model in sorted(snapshot):
        fields = snapshot[model]
        for key, value in fields.items():
            totals[key] = totals.get(key, 0) + int(value)
        lines.append(_row(model, fields, elapsed))
    if len(snapshot) > 1:
        lines.append("-" * len(header))
        lines.append(_row("TOTAL", totals, elapsed))
    return "\n".join(lines)


def _row(label, fields, elapsed) -> str:
    attempts = _attempts(fields)
    accepted = int(fields.get(genai_metrics.OUTCOME_ACCEPTED, 0))
    limited = int(fields.get(genai_metrics.OUTCOME_RATE_LIMITED, 0))
    server = int(fields.get(genai_metrics.OUTCOME_SERVER_ERROR, 0))
    transport = int(fields.get(genai_metrics.OUTCOME_TRANSPORT, 0))
    wait_s = int(fields.get(genai_metrics.FIELD_WAIT_MS, 0)) / 1000.0
    # Rejection rate is over ATTEMPTS, not over accepted calls: a rejection costs a bucket token and
    # a worker-second whether or not the retry succeeds, so attempts is the honest denominator.
    reject_pct = (100.0 * limited / attempts) if attempts else 0.0
    out = f"{label:<28}{attempts:>9}{accepted:>9}{limited:>7}{server:>6}{transport:>10}"
    out += f"{reject_pct:>8.1f}%{wait_s:>9.0f}"
    if elapsed:
        out += f"{accepted / (elapsed / 60.0):>9.1f}"
    return out


def _delta(after, before):
    """after - before, field by field, so --watch reports the interval rather than all of history."""
    out = {}
    for model, fields in after.items():
        prior = before.get(model, {})
        diff = {k: int(v) - int(prior.get(k, 0)) for k, v in fields.items()}
        if any(diff.values()):
            out[model] = diff
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="zero all counters and exit")
    parser.add_argument(
        "--watch",
        type=int,
        metavar="SECONDS",
        help="print the delta every SECONDS until interrupted",
    )
    args = parser.parse_args()

    if args.reset:
        genai_metrics.reset()
        print("counters reset")
        return

    if not args.watch:
        print(_render(genai_metrics.snapshot()))
        return

    previous = genai_metrics.snapshot()
    print(f"watching every {args.watch}s; Ctrl-C to stop")
    try:
        while True:
            time.sleep(args.watch)
            current = genai_metrics.snapshot()
            print(f"\n--- last {args.watch}s ---")
            print(_render(_delta(current, previous), elapsed=args.watch))
            previous = current
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
