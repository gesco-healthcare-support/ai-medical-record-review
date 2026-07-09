"""Chain-of-thought prompt A/B: does reasoning-before-spans cut confident over-splits?

The loosened-tiebreak test showed over-segmentation is CONFIDENT, not ambiguity-driven.
CoT is the technique meant to catch confident errors: make the model reason about which
apparent boundaries are continuations (embedded tables, letterhead-inside-a-report,
signature pages, "page N of M", same-type same-day runs) BEFORE emitting page ranges.

Our production call emits JSON directly (no reasoning step). This variant adds an
"analysis" reasoning field ahead of the "documents" array, same image input, same
temp 0, same window method. Prompt lives HERE, not in the app.

Compared against the same-session baseline (loosen_prompt_experiment baseline = ~0.608 /
R 1.00 / over 1.45 on Case 3). Recall is the headline metric.

Usage:  uv run python src/cot_prompt_experiment.py            # Case 3
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import images  # noqa: E402
from cases import by_id  # noqa: E402
from pipeline import load_labels, starts_to_spans  # noqa: E402
from solutions import _byte_budgeted_windows  # noqa: E402
from metrics import exact_doc_f1, over_seg_ratio, mean_boundary_offset  # noqa: E402
from google.genai import types  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

sys.path.insert(0, r"P:\MRR_AI_Source\mrr-line_source")
from mrr_ai.services.gemini import (  # noqa: E402
    SEGMENT_RESPONSE_SCHEMA, SEGMENTATION_PROMPT, parse_segment_item,
)

WINDOW_BUDGET_MB = 12.5
WINDOW_OVERLAP = 30

# CoT prompt: reason first, then emit the same records. Replace the terminal
# "Return ONLY the JSON array." with an object contract + a reasoning directive.
_TAIL = "Return ONLY the JSON array."
_COT_TAIL = (
    "Before deciding page ranges, reason step by step in an \"analysis\" field: scan for runs "
    "of same-type pages, embedded attachments (lab tables, imaging summaries, work-status "
    "forms, copied letters), letterhead changes INSIDE a report, signature/stamp/notary/"
    "branding pages, and \"page N of M\" continuations, and decide which apparent boundaries "
    "are FALSE (a continuation of the current document) versus a genuine new document. Then "
    "output the final records in \"documents\". Return a JSON object of the form "
    "{\"analysis\": \"<your reasoning>\", \"documents\": [ ...one record per sub-document... ]}."
)
COT_PROMPT = SEGMENTATION_PROMPT.replace(_TAIL, _COT_TAIL)
assert COT_PROMPT != SEGMENTATION_PROMPT, "prompt tail not found -- prompt drifted"

COT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "analysis": {"type": "STRING", "description": "step-by-step boundary reasoning"},
        "documents": SEGMENT_RESPONSE_SCHEMA,
    },
    "required": ["analysis", "documents"],
    "propertyOrdering": ["analysis", "documents"],
}

_SEG_SYS = (
    "You are an expert medical-records clerk. You split scanned workers' compensation "
    "medical-record files into their component documents and report exact page ranges "
    "and metadata."
)


def _cot_window(pdf, ws, we, cost):
    from genai_client import generate_json
    reader, writer = PdfReader(pdf), PdfWriter()
    for p in range(ws - 1, we):
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    part = types.Part.from_bytes(data=buf.getvalue(), mime_type="application/pdf")
    obj = generate_json([part, COT_PROMPT], _SEG_SYS, cost, response_schema=COT_SCHEMA) or {}
    spans = []
    for item in (obj.get("documents") or []):
        try:
            s, e, *_ = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((s + ws - 1, e + ws - 1))
    return spans


def _ownership_merge(reports, windows, n):
    starts = {1}
    for k, (ws, _we) in enumerate(windows):
        owned_cap = n if k == len(windows) - 1 else windows[k + 1][0]
        for a, _b in reports[k]:
            if ws < a <= owned_cap:
                starts.add(a)
    return starts_to_spans(sorted(starts), n)


def run_case(case_id):
    from genai_client import Cost, MODEL
    pdf = by_id(case_id)["pdf"]
    n = images.page_count(pdf)
    _, gold = load_labels(by_id(case_id)["label_csv"], n)
    windows = _byte_budgeted_windows(pdf, n, WINDOW_OVERLAP, int(WINDOW_BUDGET_MB * 1024 * 1024))
    print(f"\n### {case_id}: {n} pages, {len(gold)} gold docs | windows={len(windows)} "
          f"| model={MODEL} | calls={len(windows)} (CoT only)")
    cost = Cost()
    reports = [_cot_window(pdf, ws, we, cost) for ws, we in windows]
    spans = _ownership_merge(reports, windows, n)
    gs, ps = sorted({s for s, _ in gold}), sorted({s for s, _ in spans})
    tp = len(set(gs) & set(ps))
    print(f"{'variant':12}{'DocF1':>8}{'RECALL':>8}{'Prec':>7}{'over':>7}{'off':>6}{'pred':>6}")
    print(f"{'cot':12}{exact_doc_f1(spans, gold):>8.3f}{tp / len(gs):>8.3f}"
          f"{tp / len(ps):>7.3f}{over_seg_ratio(spans, gold):>7.2f}"
          f"{mean_boundary_offset(ps, gs):>6.2f}{len(spans):>6}")
    print(f"{'baseline*':12}{0.608:>8.3f}{1.000:>8.3f}{0.689:>7.3f}{1.45:>7.2f}{0.00:>6.2f}{74:>6}")
    print("  *baseline = current prompt, same-session run (loosen_prompt_experiment)")
    print(f"cost: {cost.summary()}")


if __name__ == "__main__":
    for c in ([a for a in sys.argv[1:] if not a.startswith("--")] or ["Case 3"]):
        run_case(c)
