"""Mayo-style OCR-text segmentation experiment (image -> text A/B).

Isolates ONE variable vs the production window segmenter: input modality. Instead of
sending each window's page IMAGES (oracles.window_segment), it sends the window's cached
OCR TEXT with the SAME production prompt, schema, temperature 0, and absolute-offset logic.

Mirrors the Mayo JAMIA Open 2026 pipeline (OCR text -> structured prompt -> off-the-shelf
Gemini -> segment/date/type), adapted to our larger bundles via two modes:
  - whole-case : one text call over all pages (closest to Mayo's holistic single call).
  - windowed   : reuse the image method's byte-budgeted windows + ownership merge, but
                 feed text -> controlled A/B against 1_windows with windowing held fixed.

OCR is the already-cached Tesseract text (cache/ocr/<case>.jsonl), so OCR cost is $0;
only the segmentation calls cost anything (text tokens, far cheaper than 12.5 MB images).

Usage:
  uv run python src/ocr_text_experiment.py --dry "Case 3"      # offline: assemble + count calls, NO Gemini
  uv run python src/ocr_text_experiment.py "Case 3"            # run whole-case + windowed on Case 3
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import images  # noqa: E402
from cases import by_id  # noqa: E402
from pipeline import load_pages, load_labels, starts_to_spans  # noqa: E402
from solutions import _byte_budgeted_windows  # noqa: E402
from metrics import exact_doc_f1, over_seg_ratio, mean_boundary_offset  # noqa: E402

# Production prompt/schema/parser + the client. Imported lazily-safe: importing these does
# NOT build a genai client or hit auth (that happens only on a real generate_json call).
sys.path.insert(0, r"P:\MRR_AI_Source\mrr-line_source")
from mrr_ai.services.gemini import (  # noqa: E402
    SEGMENT_RESPONSE_SCHEMA, SEGMENTATION_PROMPT, parse_segment_item,
)

_SEG_SYS = (
    "You are an expert medical-records clerk. You split scanned workers' compensation "
    "medical-record files into their component documents and report exact page ranges "
    "and metadata."
)
WINDOW_BUDGET_MB = 12.5   # match the image method's byte-budgeted windowing for the A/B
WINDOW_OVERLAP = 30


def _pages_for(case_id):
    """Cached OCR page text (1-based -> index 0..n-1), padded/truncated to the PDF page count."""
    n = images.page_count(by_id(case_id)["pdf"])
    pages, _ = load_pages(case_id)
    if pages is None:
        raise SystemExit(f"No OCR cache for {case_id}; run ocr_prep first.")
    if len(pages) < n:
        pages = pages + [""] * (n - len(pages))
    return pages[:n], n


def _window_text(pages, ws, we):
    """Page-delimited text for absolute pages [ws, we], labeled window-relative (1..k) to
    match the prompt's 'Page N = N-th page of THIS file' contract."""
    blocks = []
    for k, p in enumerate(range(ws, we + 1), start=1):
        body = (pages[p - 1] or "").strip()
        blocks.append(f"===== PAGE {k} =====\n{body}")
    return "\n\n".join(blocks)


def _window_chars(pages, ws, we):
    return sum(len(pages[p - 1] or "") for p in range(ws, we + 1))


def segment_text(pages, ws, we, cost):
    """Text twin of oracles.window_segment: one JSON call over the window's OCR text;
    returns absolute (start, end) spans (local 1..k offset by ws-1)."""
    from genai_client import generate_json  # local import: defers any client build to call time
    contents = [_window_text(pages, ws, we), SEGMENTATION_PROMPT]
    data = generate_json(contents, _SEG_SYS, cost, response_schema=SEGMENT_RESPONSE_SCHEMA) or []
    spans = []
    for item in data:
        try:
            s, e, *_ = parse_segment_item(item)
        except (KeyError, TypeError, ValueError):
            continue
        spans.append((s + ws - 1, e + ws - 1))
    return spans


def _ownership_merge(reports, windows, n):
    """Same ownership rule as sol1: window k owns starts in (ws_k, ws_{k+1}]; drop each
    window's own first-page artifact; page 1 is always a start."""
    starts = {1}
    for k, (ws, _we) in enumerate(windows):
        owned_cap = n if k == len(windows) - 1 else windows[k + 1][0]
        for a, _b in reports[k]:
            if ws < a <= owned_cap:
                starts.add(a)
    return starts_to_spans(sorted(starts), n)


def _score(pred_spans, gold_spans, n):
    gstarts = sorted({s for s, _ in gold_spans})
    pstarts = sorted({s for s, _ in pred_spans})
    tp = len(set(gstarts) & set(pstarts))
    rec = tp / len(gstarts) if gstarts else 0.0
    prec = tp / len(pstarts) if pstarts else 0.0
    return dict(
        docf1=exact_doc_f1(pred_spans, gold_spans),
        start_recall=rec, start_prec=prec,
        over_seg=over_seg_ratio(pred_spans, gold_spans),
        offset=mean_boundary_offset(pstarts, gstarts),
        n_pred=len(pred_spans),
    )


def _fmt(name, m, calls):
    return (f"{name:22} DocF1={m['docf1']:.3f} R={m['start_recall']:.3f} "
            f"P={m['start_prec']:.3f} over={m['over_seg']:.2f} off={m['offset']:.2f} "
            f"pred={m['n_pred']} calls={calls}")


def run_case(case_id, dry):
    pages, n = _pages_for(case_id)
    _, gold = load_labels(by_id(case_id)["label_csv"], n)
    windows = _byte_budgeted_windows(by_id(case_id)["pdf"], n, WINDOW_OVERLAP,
                                     int(WINDOW_BUDGET_MB * 1024 * 1024))
    total_calls = 1 + len(windows)   # whole-case (1) + one per window
    empty = sum(1 for p in pages if not (p or "").strip())

    print(f"\n### {case_id}: {n} pages, {len(gold)} gold docs")
    print(f"OCR: {empty}/{n} empty pages | whole-case text = {_window_chars(pages, 1, n):,} chars")
    print(f"windows (byte-budgeted, matches image method): {len(windows)} -> {windows}")
    print(f"PLANNED GEMINI CALLS: {total_calls}  (1 whole-case + {len(windows)} windowed)")
    if dry:
        print("[dry] no Gemini calls made.")
        return total_calls

    from genai_client import Cost, MODEL
    print(f"model={MODEL}")
    # whole-case (Mayo-style holistic)
    wc_cost = Cost()
    whole = segment_text(pages, 1, n, wc_cost)
    m_whole = _score(whole, gold, n)
    print(_fmt("text_whole_case", m_whole, 1) + f"  {wc_cost.summary()}")
    # windowed (A/B vs image 1_windows)
    win_cost = Cost()
    reports = [segment_text(pages, ws, we, win_cost) for ws, we in windows]
    win_spans = _ownership_merge(reports, windows, n)
    m_win = _score(win_spans, gold, n)
    print(_fmt("text_windows", m_win, len(windows)) + f"  {win_cost.summary()}")
    print("baseline (image, saved):  1_windows DocF1=0.61 R=1.00 P=0.69 over=1.45 | "
          "naive_chunk 0.57-0.60")
    return total_calls


def main(argv):
    dry = "--dry" in argv
    cases = [a for a in argv if not a.startswith("--")] or ["Case 3"]
    planned = sum(run_case(c, dry) for c in cases)
    print(f"\nTOTAL PLANNED CALLS across {len(cases)} case(s): {planned}")


if __name__ == "__main__":
    main(sys.argv[1:])
