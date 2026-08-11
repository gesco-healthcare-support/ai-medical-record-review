"""Full A/B on real duplicate clusters: ORIGINAL vs HARDENED, to quantify the 3-change improvement.

ORIGINAL arm = temp 0.8 + CURRENT Tesseract + current category prompt (pre-Phase-1 production).
HARDENED arm = temp 0.0 + TUNED Tesseract (deskew+Otsu) + HARDENING_PREAMBLE + category prompt.

Per sub-document (row) it measures:
  - repeat-consistency: N repeat summaries of the SAME doc, mean pairwise difflib (problem #2).
  - faithfulness: LLM judge counts unsupported + self-contradicting statements vs the arm's own
    OCR source (problem #3).
Per cluster it measures:
  - copy-to-copy convergence: the first summary of each copy, mean pairwise difflib across copies
    (a good system gives near-identical summaries to re-scans of the same content).

The category prompt is resolved DB-first via catalog.get_prompt (production path). The ORIGINAL arm
reuses the current-Tesseract page cache from dedup_reocr; only the picked clusters get tuned OCR, so
cost is bounded. All Gemini calls route through generate_with_retry (limiter + backoff). Serial.

Run: python /tmp/ab_test.py --doc <id> --pdf /tmp/arroyo_full.pdf \
       --cache /tmp/out/dedup_pages.json --clusters "12,40;77,95" --repeats 3 --out /tmp/out/ab
"""

import argparse
import difflib
import itertools
import json
import os
import statistics

from google.genai import types

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Summary
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.prompts import prompts as FALLBACK
from app.services.summarize_engine import HARDENING_PREAMBLE

from ocr_variants import extract_tuned  # shared "tuned" definition (deskew+Otsu)

_JUDGE_SYS = (
    "You are a strict medical-record auditor. Given SOURCE text and a SUMMARY, list summary "
    'statements not supported by the SOURCE or contradicting it or each other. '
    'Reply ONLY JSON: {"unsupported":[...],"contradictions":[...]}.'
)


def _gen(model, system_msg, text, temp):
    resp = generate_with_retry(
        get_genai_client(), model=model, contents=text,
        config=types.GenerateContentConfig(temperature=temp, max_output_tokens=2048, system_instruction=system_msg),
    )
    return (resp.text or "").strip()


def _faith(model, source, summary):
    out = _gen(model, _JUDGE_SYS, f"SOURCE:\n{source}\n\nSUMMARY:\n{summary}", 0.0)
    try:
        c = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        d = json.loads(c)
        return len(d.get("unsupported", [])), len(d.get("contradictions", []))
    except Exception:
        return -1, -1


def _mean_pairwise(texts):
    pairs = list(itertools.combinations(texts, 2))
    if not pairs:
        return 1.0
    return round(statistics.mean(difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs), 3)


def _prompt_for(session, category):
    return catalog.get_prompt(session, "summary", category) or FALLBACK.get(
        f"category_{int(category):02d}" if str(category) != "100" else "category_100", FALLBACK["category_100"]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--cache", required=True, help="dedup_reocr _pages.json (current-Tesseract page text)")
    ap.add_argument("--clusters", required=True, help="semicolon-separated idx groups, e.g. '12,40;77,95'")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="/tmp/out/ab")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    with open(args.cache, encoding="utf-8") as f:
        page_cache = json.load(f)  # {"<page>": text}
    model = get_settings().summary_model
    clusters = [[int(x) for x in grp.split(",")] for grp in args.clusters.split(";")]

    sm = get_sessionmaker()
    report = []
    for cn, idxs in enumerate(clusters, 1):
        print(f"\n===== cluster {cn}: rows {idxs} =====", flush=True)
        with sm() as s:
            rows = {r.idx: r for r in s.query(Summary).filter(
                Summary.document_id == args.doc, Summary.idx.in_(idxs)).all()}
            meta = {i: {"start": rows[i].row_start, "end": rows[i].row_end,
                        "category": rows[i].row_category,
                        "prompt": _prompt_for(s, rows[i].row_category)} for i in idxs}

        cluster = {"cluster": cn, "rows": {}}
        orig_firsts, hard_firsts = [], []
        for i in idxs:
            m = meta[i]
            orig_text = "".join(page_cache[str(p)] for p in range(m["start"], m["end"] + 1))
            hard_text = extract_tuned(args.pdf, m["start"], m["end"])
            print(f"  row idx={i} pages={m['start']}-{m['end']} cat={m['category']} "
                  f"chars orig={len(orig_text)} tuned={len(hard_text)}", flush=True)

            orig = [_gen(model, m["prompt"], orig_text, 0.8) for _ in range(args.repeats)]
            hard = [_gen(model, HARDENING_PREAMBLE + m["prompt"], hard_text, 0.0) for _ in range(args.repeats)]
            o_uns, o_con = _faith(model, orig_text, orig[0])
            h_uns, h_con = _faith(model, hard_text, hard[0])
            orig_firsts.append(orig[0])
            hard_firsts.append(hard[0])

            cluster["rows"][i] = {
                "pages": f"{m['start']}-{m['end']}", "category": m["category"],
                "orig": {"repeat_consistency": _mean_pairwise(orig), "unsupported": o_uns,
                         "contradictions": o_con, "summaries": orig},
                "hardened": {"repeat_consistency": _mean_pairwise(hard), "unsupported": h_uns,
                             "contradictions": h_con, "summaries": hard},
            }
            print(f"    ORIGINAL repeat_consistency={cluster['rows'][i]['orig']['repeat_consistency']} "
                  f"unsupported={o_uns} contradictions={o_con}", flush=True)
            print(f"    HARDENED repeat_consistency={cluster['rows'][i]['hardened']['repeat_consistency']} "
                  f"unsupported={h_uns} contradictions={h_con}", flush=True)

        cluster["copy_convergence"] = {"orig": _mean_pairwise(orig_firsts),
                                       "hardened": _mean_pairwise(hard_firsts)}
        print(f"  COPY-TO-COPY convergence: orig={cluster['copy_convergence']['orig']} "
              f"hardened={cluster['copy_convergence']['hardened']}", flush=True)
        report.append(cluster)

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Human-readable rollup for the employee.
    lines = ["# A/B: ORIGINAL (temp 0.8, current OCR, current prompt) vs HARDENED "
             "(temp 0.0, tuned OCR, hardened prompt)\n",
             "repeat_consistency: 1.0 = identical across repeat runs (higher=more stable). "
             "unsupported/contradictions: judge counts vs the OCR source (lower=more faithful). "
             "copy_convergence: similarity of summaries across true re-scans (higher=better).\n"]
    for c in report:
        lines.append(f"\n## cluster {c['cluster']} (copy-convergence orig={c['copy_convergence']['orig']} "
                     f"hardened={c['copy_convergence']['hardened']})\n")
        lines.append("| row (pages) | arm | repeat_consistency | unsupported | contradictions |")
        lines.append("|---|---|---|---|---|")
        for i, r in c["rows"].items():
            lines.append(f"| idx {i} ({r['pages']}) | ORIGINAL | {r['orig']['repeat_consistency']} | "
                         f"{r['orig']['unsupported']} | {r['orig']['contradictions']} |")
            lines.append(f"| | HARDENED | {r['hardened']['repeat_consistency']} | "
                         f"{r['hardened']['unsupported']} | {r['hardened']['contradictions']} |")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {args.out}.json / .md", flush=True)


if __name__ == "__main__":
    main()
