"""Budgeted oracle spot-check for the free-tier Gemini cap (~20 calls/day).

Runs a small, BALANCED sample of known-answer probes on Case 3 and reports directional
accuracy for both oracles. Every call is a real data point (no call wasted on a ping); the
first call doubles as the quota check. Interleaved so a partial run still has a mix.

  python src/sample_oracle.py [budget]   # default 18 (stays under the 20/day cap)
"""

import os
import sys

from google.genai import errors

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import images
import oracles
from cases import by_id
from genai_client import Cost
from pipeline import load_labels

BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 18
CASE = "Case 3"


def main():
    pdf = by_id(CASE)["pdf"]
    n = images.page_count(pdf)
    _, spans = load_labels(by_id(CASE)["label_csv"], n)
    spans = sorted(spans)
    starts = sorted({s for s, _ in spans})
    end_of = {p: e for s, e in spans for p in range(s, e + 1)}

    adj_new = [("adj", p, "NEW") for p in starts if p > 1][:5]
    adj_same = [("adj", (s + e) // 2, "SAME") for s, e in spans if e > s][:5]
    rng_same = [("rng", (s, min(e, s + 1)), "SAME_DOC") for s, e in spans if e > s][:5]
    rng_new = [("rng", (s, e + 1), "NEW_DOC") for s, e in spans if e + 1 <= n][:5]

    plan = []
    for quad in zip(adj_new, adj_same, rng_same, rng_new):
        plan.extend(quad)
    plan = plan[:BUDGET]

    cost = Cost()
    adj_res, rng_res, hit_quota = [], [], False
    try:
        for kind, arg, expected in plan:
            if kind == "adj":
                got = oracles.adjacent(pdf, arg, cost)
                adj_res.append((arg, expected, got))
            else:
                s, e = arg
                got = oracles.range_probe(pdf, s, e, cost)
                rng_res.append((s, e, expected, got))
    except errors.ClientError as exc:
        if getattr(exc, "code", None) == 429:
            hit_quota = True
        else:
            raise

    if not adj_res and not rng_res and hit_quota:
        print("QUOTA NOT RESET: first call returned 429 (free-tier daily cap). 0 data points.")
        return

    if adj_res:
        ok = sum(g == exp for _, exp, g in adj_res)
        print(f"\nADJACENT (NEW vs SAME): {ok}/{len(adj_res)} correct")
        for p, exp, g in adj_res:
            print(f"   page {p:>4}: expected {exp:<5} got {g}  {'ok' if g == exp else 'X'}")
    if rng_res:
        ok = sum(g == exp for _, _, exp, g in rng_res)
        print(f"\nRANGE-PROBE (SAME_DOC vs NEW_DOC): {ok}/{len(rng_res)} correct")
        for s, e, exp, g in rng_res:
            print(f"   doc@{s:>4} cand {e:>4}: expected {exp:<9} got {g}  {'ok' if g == exp else 'X'}")
    print(f"\ncost: {cost.summary()}")
    if hit_quota:
        print("(stopped early: hit the daily quota mid-run)")


if __name__ == "__main__":
    main()
