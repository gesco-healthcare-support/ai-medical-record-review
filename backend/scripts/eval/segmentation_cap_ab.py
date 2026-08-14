"""Score production segmentation against hand-labelled gold, with and without the page cap.

This is the recall A/B that PR #96 shipped without. WINDOW_MAX_PAGES bounds how many pages ride in
one vision call, which fixed records that could never finish - but it also changes where window
seams fall, and a seam is where over-segmentation appears. Whether that costs boundary accuracy is
an empirical question, and this answers it on a case that has hand-typed ground truth.

Runs the REAL run_segmentation, not a reimplementation: same windowing, same prompt, same verify
pass, same per-sub-document reads. So a green result here is evidence about what the tester will
actually get, not about a test harness.

Arms are (cap, label) pairs. A cap of 10000 never binds, so it reproduces pre-#96 behaviour exactly
and is the BEFORE arm; the default 100 is what production runs today.

PHI: prints span counts, timings and metric values only. No titles, no dates, no record text.

Usage:
    python scripts/eval/segmentation_cap_ab.py ["Case 3"] [10000,100]

Needs DATABASE_URL set to anything parseable (run_segmentation is DB-free but Settings requires it),
the repo-root .env for Vertex credentials, and the classifier extra:

    DATABASE_URL=postgresql+psycopg://x:y@127.0.0.1:5432/mrr_dev_only \\
      uv run --extra classifier --env-file ../.env python scripts/eval/segmentation_cap_ab.py
"""

from __future__ import annotations

import pathlib
import sys
import time

# Running this as a FILE puts scripts/eval on sys.path, not backend/, so `app` is not importable
# without this - the failure looks like a missing dependency and is really a path one.
_BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))

# The gold labels, the case registry and the scoring live in the segmentation experiment, which is
# the only place hand-typed ground truth exists. Same cross-tree import oracles.py:30 already uses.
sys.path.insert(1, str(_BACKEND.parent / "experiments" / "a1-segmentation" / "src"))

import cases  # noqa: E402
import metrics  # noqa: E402
import pipeline  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.segment_engine import run_segmentation  # noqa: E402

DEFAULT_CASE = "Case 3"
DEFAULT_ARMS = [10000, 100]


def _progress(stage, current, total):
    """Keep a long run legible; a silent 20-minute command is indistinguishable from a hung one."""
    if total and (current == total or current % 10 == 0):
        print(f"    [{stage}] {current}/{total}", flush=True)


def _score(pred_spans, gold_spans, n):
    gold_starts = [s for s, _ in gold_spans]
    pred_starts = [s for s, _ in pred_spans]
    boundary = metrics.boundary_metrics(
        metrics.starts_to_boundary_mask(gold_starts, n),
        metrics.starts_to_boundary_mask(pred_starts, n),
    )
    partition = metrics.partition_validity(pred_spans, n)
    return {
        "docs": len(pred_spans),
        "boundary_recall": boundary["recall"],
        "boundary_precision": boundary["precision"],
        "boundary_f1": boundary["f1"],
        "exact_doc_f1": metrics.exact_doc_f1(pred_spans, gold_spans),
        "weighted_doc_f1": metrics.weighted_doc_f1(pred_spans, gold_spans),
        "over_seg_ratio": metrics.over_seg_ratio(pred_spans, gold_spans),
        # A near-miss boundary is a very different failure from a missing one, and exact_doc_f1
        # cannot tell them apart - it scores both as zero.
        "mean_offset_pages": metrics.mean_boundary_offset(pred_starts, gold_starts),
        # Coverage is the one hard invariant: every page must land in exactly one sub-document.
        # A capped run creating a gap or an overlap would be a real defect, not a quality dip.
        "gap_pages": partition["gap_pages"],
        "overlap_pages": partition["overlap_pages"],
    }


def main(argv):
    case_id = argv[0] if argv else DEFAULT_CASE
    arms = [int(x) for x in argv[1].split(",")] if len(argv) > 1 else DEFAULT_ARMS

    case = cases.by_id(case_id)
    pages = len(PdfReader(case["pdf"]).pages)
    _, gold = pipeline.load_labels(case["label_csv"], pages)
    settings = get_settings()

    print(f"case={case_id} pages={pages} gold_subdocs={len(gold)}")
    print(
        f"model={settings.genai_model} budget_mb={settings.window_budget_mb} overlap={settings.window_overlap}"
    )

    results = {}
    for cap in arms:
        settings.window_max_pages = cap  # get_settings is cached, so the engine sees this
        label = "uncapped (pre-#96)" if cap >= pages else f"cap={cap}"
        print(f"\n--- {label} ---", flush=True)
        started = time.time()
        try:
            rows = run_segmentation(case["pdf"], pages, progress=_progress)
        except Exception as exc:  # noqa: BLE001 - a failed arm is the result, not a crash
            print(
                f"  FAILED after {time.time() - started:.0f}s: {type(exc).__name__}: {str(exc)[:120]}"
            )
            results[label] = None
            continue
        elapsed = time.time() - started
        spans = sorted((r["start"], r["end"]) for r in rows)
        scored = _score(spans, gold, pages)
        scored["seconds"] = round(elapsed, 1)
        results[label] = scored
        print(f"  {elapsed:.0f}s -> {scored['docs']} sub-documents (gold {len(gold)})")

    print("\n=== RESULT ===")
    keys = [
        "docs",
        "seconds",
        "boundary_recall",
        "boundary_precision",
        "boundary_f1",
        "exact_doc_f1",
        "weighted_doc_f1",
        "over_seg_ratio",
        "mean_offset_pages",
        "gap_pages",
        "overlap_pages",
    ]
    labels = [k for k in results if results[k] is not None]
    print(f"{'metric':20}" + "".join(f"{k:>22}" for k in labels))
    for key in keys:
        row = f"{key:20}"
        for label in labels:
            value = results[label][key]
            row += f"{value:>22.3f}" if isinstance(value, float) else f"{str(value):>22}"
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
