"""Prompt-hardening test (phase 3 of the eval). Temp fixed at 0.0.

Compares the CURRENT per-category prompt against a FACTUALITY-HARDENED variant (current prompt +
a shared anti-hallucination preamble) on real sub-documents, measuring faithfulness (unsupported /
contradicting statements) via an LLM judge against the source text. Answers: does prompt hardening
alone cut the contradictions that persist at temp 0, or do we still need a verify pass?

All Gemini calls route through generate_with_retry (Redis limiter + backoff). Serial. No DB writes.
Run: python /tmp/prompt_test.py --doc <id> --sets 3
"""

import argparse
import json
import os

from google.genai import types

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Document, ReviewRow
from app.services import catalog
from app.services.genai_client import get_genai_client
from app.services.genai_retry import generate_with_retry
from app.services.ocr import extract_text_from_selected_pages
from app.services.prompts import prompts as DEFAULT_PROMPTS

# Factuality-hardening preamble. Each rule states WHY so it survives prompt edits (prompt-writing
# rubric). Prepended to the category prompt; the category-specific format rules still apply after.
HARDENING = (
    "CRITICAL FACTUALITY RULES (a medical-legal report depends on these):\n"
    "- Use ONLY information explicitly stated in the text below. Do NOT infer, assume, extrapolate, "
    "or add anything that is not written - inference is how errors enter the record.\n"
    "- If a detail is absent, OMIT it. Never guess or fill a gap, and never write a point and then "
    "say 'not specified'.\n"
    "- Copy dates, percentages, measurements, ratings, and medication names/doses EXACTLY as "
    "written; do not round, convert, or paraphrase a number.\n"
    "- Do NOT contradict yourself: every statement in your summary must be consistent with the "
    "source and with your other statements.\n"
    "- If the text is illegible, ambiguous, or internally contradictory, omit that point rather "
    "than resolving it by guessing.\n\n"
)

_JUDGE_SYS = (
    "You are a strict medical-record auditor. Given SOURCE text and a SUMMARY, list summary "
    'statements not supported by the SOURCE (fabrications) or contradicting it. '
    'JSON: {"unsupported": ["..."], "contradictions": ["..."]}.'
)


def _model():
    return get_settings().summary_model


def _gen(system_msg, user_text, temp=0.0):
    resp = generate_with_retry(
        get_genai_client(), model=_model(), contents=user_text,
        config=types.GenerateContentConfig(temperature=temp, max_output_tokens=2048, system_instruction=system_msg),
    )
    return (resp.text or "").strip()


def _resolve_prompt(session, category):
    p = catalog.get_prompt(session, "summary", str(category))
    if p:
        return p
    key = f"category_{int(category):02d}" if str(category) != "100" else "category_100"
    return DEFAULT_PROMPTS.get(key, DEFAULT_PROMPTS["category_100"])


def _faith(source, summary):
    out = _gen(_JUDGE_SYS, f"SOURCE:\n{source}\n\nSUMMARY:\n{summary}", 0.0)
    try:
        c = out.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        d = json.loads(c)
        return len(d.get("unsupported", [])), len(d.get("contradictions", []))
    except Exception:
        return -1, -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", required=True)
    ap.add_argument("--sets", type=int, default=3)
    ap.add_argument("--out", default="/tmp/out/prompt_test")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    report = []
    with get_sessionmaker()() as session:
        doc = session.get(Document, args.doc)
        path = doc.stored_path
        rows = session.query(ReviewRow).filter(ReviewRow.document_id == args.doc).all()
        groups = {}
        for r in rows:
            groups.setdefault((r.date, r.title), []).append(r)
        dupes = sorted([(k, v) for k, v in groups.items() if len(v) > 1],
                       key=lambda kv: (-len(kv[1]), kv[0][0]))[: args.sets]

        for (date, title), rs in dupes:
            r = sorted(rs, key=lambda x: x.start)[0]
            base = _resolve_prompt(session, r.category)
            src = extract_text_from_selected_pages(path, list(range(r.start, r.end + 1)))
            if not src.strip():
                continue
            print(f"\n=== {title} ({date}) pages {r.start}-{r.end} ===")
            variants = {"current": base, "hardened": HARDENING + base}
            entry = {"title": title, "date": date, "category": r.category, "variants": {}}
            for name, prompt in variants.items():
                summ = _gen(prompt, src)
                uns, con = _faith(src, summ)
                entry["variants"][name] = {"unsupported": uns, "contradictions": con, "summary": summ}
                print(f"  {name}: unsupported={uns} contradictions={con}")
            report.append(entry)

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    lines = ["# Prompt-hardening test (temp 0.0) - current vs factuality-hardened\n",
             "unsupported/contradictions: LLM judge vs source text (lower = better).\n"]
    for e in report:
        lines.append(f"\n## {e['title']} - {e['date']} (cat {e['category']})\n")
        lines.append("| prompt | unsupported | contradictions |\n|---|---|---|")
        for n, v in e["variants"].items():
            lines.append(f"| {n} | {v['unsupported']} | {v['contradictions']} |")
        for n, v in e["variants"].items():
            lines.append(f"\n**{n}:**\n\n{v['summary']}\n")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\nwrote {args.out}.json / .md")


if __name__ == "__main__":
    main()
