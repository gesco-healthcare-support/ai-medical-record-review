"""Loosened-prompt A/B: does the deliberate recall-first tiebreak drive over-segmentation?

Tests the #1 root cause from the error triage: the production SEGMENTATION_PROMPT tells the
model to START A NEW RECORD when unsure (recall-first, over-splits on purpose). This flips
JUST that tiebreak toward CONTINUE-unless-clear-signal and re-runs the IMAGE window method,
measuring the precision/recall tradeoff vs the current prompt on the same windows, same
session (controls for temp-0 variance).

The loosened prompt lives HERE, not in the app: it is the app prompt with one clause
replaced, asserted to have changed. App segmentation code is unchanged.

Recall is the headline metric - loosening risks merges (the unrecoverable error).

Usage:  uv run python src/loosen_prompt_experiment.py            # Case 3 (default)
        uv run python src/loosen_prompt_experiment.py "Case 1" "Case 2"
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import images  # noqa: E402
import oracles  # noqa: E402  (baseline window_segment uses the app prompt)
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

# --- build the loosened prompt: flip ONLY the tiebreak (assert the swap took) ---
_ORIG_TIEBREAK = ("START A NEW RECORD. A reviewer merges a false split in one click, but a "
                  "document hidden inside another record is never seen again.")
_LOOSE_TIEBREAK = ("CONTINUE the current document, unless there is a clear new-document signal "
                   "(a new letterhead with a new document title, the first page of a form, or a "
                   "new encounter/visit date). Only start a new record when such a signal is present.")
LOOSENED_PROMPT = SEGMENTATION_PROMPT.replace(_ORIG_TIEBREAK, _LOOSE_TIEBREAK)
assert LOOSENED_PROMPT != SEGMENTATION_PROMPT, "tiebreak clause not found -- prompt text drifted"

_SEG_SYS = (
    "You are an expert medical-records clerk. You split scanned workers' compensation "
    "medical-record files into their component documents and report exact page ranges "
    "and metadata."
)


def _window_spans(pdf_path, ws, we, prompt, cost):
    """window_segment with a swappable prompt (image input, same schema/offset as production)."""
    from genai_client import generate_json
    reader, writer = PdfReader(pdf_path), PdfWriter()
    for p in range(ws - 1, we):
        writer.add_page(reader.pages[p])
    buf = io.BytesIO()
    writer.write(buf)
    part = types.Part.from_bytes(data=buf.getvalue(), mime_type="application/pdf")
    data = generate_json([part, prompt], _SEG_SYS, cost, response_schema=SEGMENT_RESPONSE_SCHEMA) or []
    spans = []
    for item in data:
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


def _score(pred, gold, n):
    gs, ps = sorted({s for s, _ in gold}), sorted({s for s, _ in pred})
    tp = len(set(gs) & set(ps))
    return dict(docf1=exact_doc_f1(pred, gold),
                recall=tp / len(gs) if gs else 0.0,
                prec=tp / len(ps) if ps else 0.0,
                over=over_seg_ratio(pred, gold),
                off=mean_boundary_offset(ps, gs),
                n_pred=len(pred))


def _run_variant(pdf, windows, n, prompt, label):
    from genai_client import Cost
    cost = Cost()
    reports = [_window_spans(pdf, ws, we, prompt, cost) for ws, we in windows]
    spans = _ownership_merge(reports, windows, n)
    return spans, cost


def run_case(case_id):
    from genai_client import MODEL
    pdf = by_id(case_id)["pdf"]
    n = images.page_count(pdf)
    _, gold = load_labels(by_id(case_id)["label_csv"], n)
    windows = _byte_budgeted_windows(pdf, n, WINDOW_OVERLAP, int(WINDOW_BUDGET_MB * 1024 * 1024))
    print(f"\n### {case_id}: {n} pages, {len(gold)} gold docs | windows={len(windows)} "
          f"| model={MODEL} | calls={2 * len(windows)} (baseline + loosened)")

    base_spans, base_cost = _run_variant(pdf, windows, n, SEGMENTATION_PROMPT, "baseline")
    loose_spans, loose_cost = _run_variant(pdf, windows, n, LOOSENED_PROMPT, "loosened")
    mb, ml = _score(base_spans, gold, n), _score(loose_spans, gold, n)

    hdr = f"{'variant':10}{'DocF1':>8}{'RECALL':>8}{'Prec':>7}{'over':>7}{'off':>6}{'pred':>6}"
    print(hdr)
    for name, m in (("baseline", mb), ("loosened", ml)):
        print(f"{name:10}{m['docf1']:>8.3f}{m['recall']:>8.3f}{m['prec']:>7.3f}"
              f"{m['over']:>7.2f}{m['off']:>6.2f}{m['n_pred']:>6}")
    dR, dP, dF = ml['recall'] - mb['recall'], ml['prec'] - mb['prec'], ml['docf1'] - mb['docf1']
    print(f"delta (loosened - baseline): recall {dR:+.3f}  prec {dP:+.3f}  docF1 {dF:+.3f}")
    print(f"cost: baseline {base_cost.summary()} | loosened {loose_cost.summary()}")
    if dR < 0:
        print(f"NOTE: recall DROPPED {dR:+.3f} -> loosening merged real documents (unrecoverable).")


def main(argv):
    for c in (argv or ["Case 3"]):
        run_case(c)


if __name__ == "__main__":
    main([a for a in sys.argv[1:] if not a.startswith("--")])
