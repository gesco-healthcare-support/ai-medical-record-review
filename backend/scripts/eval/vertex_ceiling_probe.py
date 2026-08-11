"""Find the serviceable request rate for one Gemini model on Vertex. Needs no documents, no DB.

The question this answers: the app runs ONE global token bucket at VERTEX_MAX_RPM for every model,
so 2.5-flash traffic is throttled by 2.5-pro's scarcity. Splitting that bucket per model needs a
number for each, and flash's ceiling has never been measured. 20 rpm was chosen for the blended
workload by watching failures, not by probing.

Method: ramp the number of concurrent in-flight calls, holding each level for a fixed number of
calls, and record accepted vs 429 plus the achieved rate. The app's own rate limiter is BYPASSED
(vertex_max_rpm=0) on purpose - the goal is Vertex's ceiling, not ours.

Payload is deliberately tiny and identical across levels: a fixed one-line prompt with a small
output cap, so the variable under test is request RATE, not token volume. That makes this cheap, and
it means the result is a rate ceiling rather than a throughput-in-tokens figure.

Confound to keep in mind: dynamic shared quota is per project, so anything else running against the
same project during the probe competes with it. Run it when the server is idle, and treat a result
gathered during a live job as a floor rather than a ceiling.

Usage, from `backend/` with PYTHONPATH set:

    PYTHONPATH=. uv run python scripts/eval/vertex_ceiling_probe.py --model gemini-2.5-flash
    PYTHONPATH=. uv run python scripts/eval/vertex_ceiling_probe.py --model gemini-2.5-pro \
        --levels 1,2,4 --calls-per-level 12

Cost is exactly `sum(levels rounded up to calls-per-level)` calls, printed before it starts.
"""

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.genai import errors, types

from app.config import get_settings
from app.services.genai_client import get_genai_client

# One short, deterministic request. Not medical text: this measures transport and quota behaviour,
# and sending PHI to a throughput probe would be indefensible when a constant does the same job.
_PROMPT = "Reply with the single word: ok"
_MAX_OUTPUT_TOKENS = 8


def _config(model: str) -> types.GenerateContentConfig:
    """Per-model generation config.

    2.5-pro is a thinking model and REJECTS thinking_budget=0 with a 400, which is how two silent
    failures were introduced before (see summary_doi). So pro gets the app's summary budget and
    everything else gets the app's default; a probe that 400s would report a ceiling of zero.
    """
    settings = get_settings()
    budget = settings.summary_thinking_budget if "pro" in model else settings.gemini_thinking_budget
    return types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=budget),
    )


def _one_call(client, model):
    """One un-retried call. Returns (outcome, seconds).

    Deliberately NOT routed through generate_with_retry: a retry would hide the rejection that is
    the entire measurement, and the retry's backoff would make the achieved rate meaningless.
    """
    started = time.monotonic()
    try:
        client.models.generate_content(
            model=model, contents=[_PROMPT], config=_config(model)
        )
        return "accepted", time.monotonic() - started
    except errors.ClientError as exc:
        code = getattr(exc, "code", None)
        if code == 429:
            return "rate_limited", time.monotonic() - started
        return f"client_{code}", time.monotonic() - started
    except errors.ServerError as exc:
        return f"server_{getattr(exc, 'code', '5xx')}", time.monotonic() - started
    except Exception as exc:  # noqa: BLE001 - a probe must report, not crash
        return f"error_{type(exc).__name__}", time.monotonic() - started


def _run_level(client, model, concurrency, calls):
    """Hold `concurrency` calls in flight until `calls` have completed. Returns a result dict."""
    outcomes: dict[str, int] = {}
    latencies: list[float] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one_call, client, model) for _ in range(calls)]
        for future in as_completed(futures):
            outcome, seconds = future.result()
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            latencies.append(seconds)
    elapsed = time.monotonic() - started
    accepted = outcomes.get("accepted", 0)
    limited = outcomes.get("rate_limited", 0)
    return {
        "concurrency": concurrency,
        "calls": calls,
        "elapsed": elapsed,
        "accepted": accepted,
        "rate_limited": limited,
        "other": calls - accepted - limited,
        "accepted_per_min": (accepted / elapsed * 60.0) if elapsed else 0.0,
        "attempted_per_min": (calls / elapsed * 60.0) if elapsed else 0.0,
        "reject_pct": (100.0 * limited / calls) if calls else 0.0,
        "p50_latency": statistics.median(latencies) if latencies else 0.0,
        "outcomes": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--levels", default="1,2,4,8", help="comma-separated concurrency levels")
    parser.add_argument("--calls-per-level", type=int, default=12)
    parser.add_argument(
        "--stop-at-reject",
        type=float,
        default=40.0,
        help="abandon the ramp once a level rejects this %% or more (default 40)",
    )
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    total = len(levels) * args.calls_per_level
    print(f"model={args.model}  levels={levels}  calls/level={args.calls_per_level}")
    print(f"this will make up to {total} Vertex calls (short prompt, {_MAX_OUTPUT_TOKENS} output tokens)")
    if not args.yes:
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            sys.exit("aborted")

    # Bypass the app's limiter: we are measuring Vertex's ceiling, not our own configured one.
    # get_settings is cached, so mutating the cached instance is what actually takes effect.
    settings = get_settings()
    previous_rpm = settings.vertex_max_rpm
    settings.vertex_max_rpm = 0
    client = get_genai_client()

    header = (
        f"{'conc':>5}{'calls':>7}{'secs':>7}{'accepted':>10}{'429':>6}{'other':>7}"
        f"{'reject%':>9}{'acc/min':>9}{'p50 s':>8}"
    )
    print()
    print(header)
    print("-" * len(header))
    try:
        results = []
        for concurrency in levels:
            result = _run_level(client, args.model, concurrency, args.calls_per_level)
            results.append(result)
            print(
                f"{result['concurrency']:>5}{result['calls']:>7}{result['elapsed']:>7.1f}"
                f"{result['accepted']:>10}{result['rate_limited']:>6}{result['other']:>7}"
                f"{result['reject_pct']:>8.1f}%{result['accepted_per_min']:>9.1f}"
                f"{result['p50_latency']:>8.1f}"
            )
            if result["other"]:
                print(f"      other outcomes: {result['outcomes']}")
            if result["reject_pct"] >= args.stop_at_reject:
                print(f"      stopping: rejection rate hit {args.stop_at_reject}%")
                break
    finally:
        settings.vertex_max_rpm = previous_rpm

    if results:
        best = max(results, key=lambda r: r["accepted_per_min"])
        print()
        print(
            f"peak accepted rate {best['accepted_per_min']:.1f}/min at concurrency "
            f"{best['concurrency']} with {best['reject_pct']:.1f}% rejected"
        )
        print(
            "Read this as a ceiling for THIS model at THIS moment. Dynamic shared quota is a shared "
            "pool, so it moves with what everyone else on the project - and on the wider pool - is "
            "doing."
        )


if __name__ == "__main__":
    main()
