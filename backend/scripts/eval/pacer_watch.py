"""Sample the pacer's send rate alongside the rejection rate, so p can be plotted against R.

WHY THIS EXISTS. Every constant in ``services/llm/pacing.py`` - the halving factor, the additive
step, the floor - is a guess about how the rejection rate responds to the send rate. Nothing in the
system pairs those two: ``genai_metrics`` keeps CUMULATIVE counters and ``pacing.snapshot()`` reports
an INSTANTANEOUS rate, so the relationship between them over time has never been recorded.

That gap is what makes the constants untunable. Modelling p as a fixed number (2026-08-14: 11.3% for
2.5-flash, 21.5% for 2.5-pro) predicts the controller settles around 2-5 rpm and makes "soften the
backoff" look free. It is not free: p rises with R, which is the entire reason an adaptive controller
exists. Only measurement can say by how much.

Read-only. Reads two Redis keyspaces, writes CSV to stdout, makes no model calls, touches no database
row, and changes no pacer state. Safe to run against production during a live job - which is the
point, since the interesting behaviour only appears under real load.

    docker compose exec -T api python scripts/eval/pacer_watch.py --interval 5 --minutes 60 > pacer.csv

Then plot ``p`` against ``req_rpm``: each row is one observation of the controller at a rate it chose
for itself, which is a sweep no deliberate experiment could produce safely on a shared quota.

Columns: elapsed_s, model, req_rpm, tok_rpm, accepted, rejected, p, wait_ms.
The counters are per-interval DELTAS; the rates are the instantaneous values at the sample.

Two columns are deliberately left BLANK rather than zero, because zero would be a lie in both cases:

* ``p``, for an interval with no calls. A zero-denominator interval is an absence of data, and 0.0
  there would invent an observation the run never made.
* ``req_rpm`` / ``tok_rpm``, when the meter has no stored rate. ``pacing.snapshot()`` reports only
  keys that exist, but ``_get_rate`` SEEDS AT THE CEILING when one is missing (they carry a 24h TTL,
  so an idle stretch expires them). Printing 0.000 would read as "throttled to a standstill" when the
  truth is the opposite: the next call starts at full speed.
"""

import argparse
import time

from app.services import genai_metrics
from app.services.llm import pacing


def _rates_by_model() -> dict[str, dict[str, float]]:
    """``pacing.snapshot()`` keys are "provider:model:meter"; regroup them as model -> meter -> rpm."""
    out: dict[str, dict[str, float]] = {}
    for key, rpm in pacing.snapshot().items():
        parts = key.split(":")
        if len(parts) < 3:
            continue
        meter = parts[-1]
        model = ":".join(parts[1:-1])  # a model name may itself contain a colon
        out.setdefault(model, {})[meter] = rpm
    return out


def _counters_by_model() -> dict[str, dict[str, int]]:
    return {model: dict(fields) for model, fields in genai_metrics.snapshot().items()}


def _delta(now: dict[str, int], before: dict[str, int], field: str) -> int:
    return int(now.get(field, 0)) - int(before.get(field, 0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    parser.add_argument("--minutes", type=float, default=60.0, help="how long to sample for")
    args = parser.parse_args()

    print("elapsed_s,model,req_rpm,tok_rpm,accepted,rejected,p,wait_ms", flush=True)
    previous = _counters_by_model()
    started = time.monotonic()
    deadline = started + args.minutes * 60.0

    while time.monotonic() < deadline:
        time.sleep(args.interval)
        elapsed = time.monotonic() - started
        rates, counters = _rates_by_model(), _counters_by_model()
        # Union of both sides: a model can be paced before its first counter lands, and can hold
        # counters after its rate key expires (24h TTL). Dropping either would silently lose rows.
        for model in sorted(set(rates) | set(counters)):
            meters = rates.get(model, {})
            now, before = counters.get(model, {}), previous.get(model, {})
            accepted = _delta(now, before, genai_metrics.OUTCOME_ACCEPTED)
            rejected = _delta(now, before, genai_metrics.OUTCOME_RATE_LIMITED)
            calls = accepted + rejected
            p = f"{rejected / calls:.4f}" if calls else ""
            req = f"{meters['req']:.3f}" if "req" in meters else ""
            tok = f"{meters['tok']:.3f}" if "tok" in meters else ""
            print(
                f"{elapsed:.1f},{model},{req},{tok},"
                f"{accepted},{rejected},{p},{_delta(now, before, genai_metrics.FIELD_WAIT_MS)}",
                flush=True,
            )
        previous = counters


if __name__ == "__main__":
    main()
