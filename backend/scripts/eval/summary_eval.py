"""Summary-quality eval harness (dev tooling, no DB writes).

Runs the real summarize path across configs on REAL duplicate sub-documents and reports:
  - CONSISTENCY: mean pairwise lexical similarity across N repeat runs of the same sub-doc
    (temperature's fingerprint - temp 0 should be ~1.0), plus copy-A-vs-copy-B similarity for a
    duplicate pair (the "same doc summarized differently" symptom).
  - FAITHFULNESS: an LLM judge (temp 0) counts statements in the summary NOT supported by the
    source text (hallucinations/contradictions) - measurable without a gold summary.

Axes are pluggable; this first pass fixes extraction=tesseract_current + prompt=current and sweeps
TEMPERATURE. OCR and prompt axes get added next.

Every Gemini call goes through generate_with_retry, so the Redis token-bucket limiter + full-jitter
backoff pace it and rides out any 429. Runs SERIALLY. Outputs JSON + a markdown report.

Run in the api container:  python /tmp/summary_eval.py --doc <id> --sets 3 --runs 3
"""

import argparse
import difflib
import json
import statistics

from google.genai import types

from app.db import get_sessionmaker
from app.models import Document, ReviewRow
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts as DEFAULT_PROMPTS
from app.services.summarize_engine import TITLE_PROMPT

TEMPS = [0.8, 0.2, 0.0]

_JUDGE_SYS = (
    "You are a strict medical-record auditor. You are given the SOURCE text of a document and a "
    "SUMMARY of it. List every statement in the SUMMARY that is NOT explicitly supported by the "
    "SOURCE (a fabrication) or that CONTRADICTS the SOURCE. Do not flag correct omissions. "
    "Respond as JSON: {\"unsupported\": [\"...\"], \"contradictions\": [\"...\"]}."
)


def _model():
    from app.config import get_settings

    return get_settings().summary_model


def _generate(system_msg, user_text, temperature):
    """One summary/title call at an explicit temperature (mirrors summarize_engine._generate)."""
    resp = generate_with_retry(
        get_genai_client(),
        model=_model(),
        contents=user_text,
        config=types.GenerateContentConfig(
            temperature=temperature, max_output_tokens=2048, system_instruction=system_msg
        ),
    )
    return (resp.text or "").strip()


def _resolve_prompt(session, category):
    """DB-first summary prompt for a category (mirrors the blueprint), else the hardcoded default."""
    prompt = catalog.get_prompt(session, "summary", str(category))
    if prompt:
        return prompt
    key = f"category_{int(category):02d}" if str(category) != "100" else "category_100"
    return DEFAULT_PROMPTS.get(key, DEFAULT_PROMPTS["category_100"])


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _mean_pairwise(texts):
    pairs = [(_ratio(texts[i], texts[j])) for i in range(len(texts)) for j in range(i + 1, len(texts))]
    return round(statistics.mean(pairs), 3) if pairs else 1.0


def _faithfulness(source, summary):
    """Return (unsupported_count, contradiction_count) via an LLM judge at temp 0."""
    out = _generate(_JUDGE_SYS, f"SOURCE:\n{source}\n\nSUMMARY:\n{summary}", 0.0)
    try:
        cleaned = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(cleaned)
        return len(data.get("unsupported", [])), len(data.get("contradictions", []))
    except Exception:
        return -1, -1  # judge parse failure; surfaced as -1 rather than a false clean bill


def _discover_dupes(session, doc_id, limit):
    rows = session.query(ReviewRow).filter(ReviewRow.document_id == doc_id).all()
    groups = {}
    for r in rows:
        groups.setdefault((r.date, r.title), []).append(r)
    dupes = [(k, v) for k, v in groups.items() if len(v) > 1]
    dupes.sort(key=lambda kv: (-len(kv[1]), kv[0][0]))
    return dupes[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--sets", type=int, default=3)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="/tmp/out/summary_eval")
    args = ap.parse_args()

    import os

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    report = []
    with get_sessionmaker()() as session:
        doc = session.get(Document, args.doc)
        pdf_path = doc.stored_path
        dupes = _discover_dupes(session, args.doc, args.sets)
        print(f"doc={args.doc} pages={doc.page_count} duplicate_sets={len(dupes)}")

        for (date, title), rows in dupes:
            rows = sorted(rows, key=lambda r: r.start)
            copy_a, copy_b = rows[0], rows[1]
            prompt = _resolve_prompt(session, copy_a.category)
            src_a = extract_text_from_selected_pages(pdf_path, list(range(copy_a.start, copy_a.end + 1)))
            src_b = extract_text_from_selected_pages(pdf_path, list(range(copy_b.start, copy_b.end + 1)))
            entry = {"title": title, "date": date, "category": copy_a.category,
                     "copyA_pages": f"{copy_a.start}-{copy_a.end}", "copyB_pages": f"{copy_b.start}-{copy_b.end}",
                     "ocr_chars_A": len(src_a), "ocr_chars_B": len(src_b), "configs": []}
            print(f"\n=== {title} ({date}) A={entry['copyA_pages']} B={entry['copyB_pages']} ===")

            for temp in TEMPS:
                runs_a = [_generate(prompt, src_a, temp) for _ in range(args.runs)]
                sum_b = _generate(prompt, src_b, temp)
                consistency = _mean_pairwise(runs_a)  # repeat-run stability at this temp
                a_vs_b = _ratio(runs_a[0], sum_b)      # do the two duplicate copies summarize alike?
                uns, con = _faithfulness(src_a, runs_a[0])
                cfg = {"temperature": temp, "repeat_consistency": consistency,
                       "copyA_vs_copyB": round(a_vs_b, 3), "unsupported_claims": uns,
                       "contradictions": con, "summary_A_run1": runs_a[0], "summary_B": sum_b}
                entry["configs"].append(cfg)
                print(f"  temp={temp}: repeat_consistency={consistency} A_vs_B={round(a_vs_b, 3)} "
                      f"unsupported={uns} contradictions={con}")
            report.append(entry)

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Markdown for human review
    lines = ["# Summary-quality eval - TEMPERATURE sweep (current OCR + current prompt)\n"]
    lines.append("Metrics: repeat_consistency (1.0=identical across runs) | copyA_vs_copyB "
                 "(lexical) | unsupported/contradictions (LLM judge vs source; lower=better).\n")
    for e in report:
        lines.append(f"\n## {e['title']} - {e['date']} (cat {e['category']}, "
                     f"A={e['copyA_pages']} / B={e['copyB_pages']})\n")
        lines.append("| temp | repeat_consistency | A_vs_B | unsupported | contradictions |")
        lines.append("|---|---|---|---|---|")
        for c in e["configs"]:
            lines.append(f"| {c['temperature']} | {c['repeat_consistency']} | {c['copyA_vs_copyB']} "
                         f"| {c['unsupported_claims']} | {c['contradictions']} |")
        for c in e["configs"]:
            lines.append(f"\n**temp {c['temperature']} - summary of copy A (run 1):**\n\n{c['summary_A_run1']}\n")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {args.out}.json and {args.out}.md")


if __name__ == "__main__":
    main()
