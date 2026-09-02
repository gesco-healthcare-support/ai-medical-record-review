"""Word-level recall for an `ocr_max_long_edge_px` candidate, against an uncapped reference.

The instrument #237 asks for. `ocr_max_long_edge_px` is 0 - capping OFF - because a 3500px cap made
OCR 4.2x faster on oversized pages and lost 6.0% of their characters, and nothing in the tree could
say whether those characters were content or reflow. This scores what fraction of the WORDS an
uncapped render found survive a capped one, so that trade can be read.

It does not choose a cap. That is a decision these numbers make possible, and it is explicitly out
of scope here.

## Why word recall, and why a bag rather than a set

Character count cannot separate "lost a word" from "reflowed a line", and difflib similarity is
worse than useless on this question: it sat near 70% at DPI 135 because it punishes line reordering
and whitespace rather than measuring accuracy. Tokenising both sides and scoring survival makes
reflow free - a reordered line has lost nothing - and leaves only content.

Repeats are KEPT (a multiset, not a set). The earlier text-layer work scored word SETS, and a set
hides the loss that matters most here: a results table listing `normal` fifteen times still contains
the word `normal` after fourteen of its rows have gone. `set_recall` is reported alongside only to
show that gap when it opens.

Both directions, because measuring one is the mistake that made the text-layer experiment look like
a win for half a session. `recall` is reference words that survived; `precision` is candidate words
the reference also had. A lower DPI can INVENT tokens as well as drop them, so a cap scoring
0.95/0.60 is not the same animal as one scoring 0.95/0.99.

## Why only the pages a cap binds on

A cap lowers the DPI only where `cap * 72 / long_edge_pt` falls below the base, so at 3500px against
a 200 DPI base it does nothing whatever to a page under ~1260pt - every ordinary US Letter page
(792pt) included. Scoring those alongside the oversized ones drives recall toward 1.0 no matter how
badly the oversized pages fare, which is the same dilution that made character volume read 96-101%
on records whose word recall was far worse. So the arms are compared ONLY on pages where some arm
lowers the DPI, and the report says how many pages that was out of how many.

## Read the distribution, not the mean

The 6.0% that stopped this being enabled was a mean over 20 pages, one of which lost 59%. A mean is
the wrong summary for a metric whose failure mode is concentrated: a record does not care that
recall averaged 0.94 if the one page that fell to 0.41 was a lab panel. The summary therefore leads
with the worst page and the count below each floor, and prints the mean last, deliberately.

PHI: emits counts, ratios, page numbers, word LENGTHS and timings only. Never a word, never a line
of page text. `_shape` exists so the exclusive-word summary is aggregate by construction.

Usage:

    DATABASE_URL=postgresql+psycopg://x:y@127.0.0.1:5432/unused SECRET_KEY=x \\
      SECURITY_PASSWORD_SALT=x .venv/Scripts/python.exe -m scripts.eval.ocr_cap_word_recall \\
      --pdf /path/to/record.pdf --caps 3500,6500 --pages 20

Runs the real `ocr` extractors, so it needs Poppler and Tesseract. Settings requires DATABASE_URL to
be parseable even though nothing here opens the database - this script reads a file and spends CPU.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import statistics
import sys
import time
from collections import Counter

# Running this as a FILE puts scripts/eval on sys.path, not backend/, so `app` is not importable
# without this - the failure looks like a missing dependency and is really a path one.
#
# GUARDED, because the way this script is actually used is copied into the api container and run
# from /tmp, where there is no repo layout to walk up and `app` is already importable. Reaching for
# parents[2] there raises IndexError before the first line of real work - which is how this was
# found, and it is the same shape in every other script in this directory.
_HERE = pathlib.Path(__file__).resolve()
if len(_HERE.parents) > 2:
    sys.path.insert(0, str(_HERE.parents[2]))

from app.config import get_settings  # noqa: E402
from app.services import ocr  # noqa: E402

# Alphanumeric runs. Punctuation and case are dropped on purpose: an apostrophe or a capital lost to
# a lower DPI is not content lost, and counting it as such is the noise this metric exists to avoid.
_WORD = re.compile(r"[A-Za-z0-9]+")

# The floors the summary counts pages against. Not thresholds for a decision - the point is to show
# where the distribution sits, and a single floor would hide whether a failure is mild or total.
_FLOORS = (0.98, 0.90, 0.70)


def tokens(text: str) -> list[str]:
    """Lowercased alphanumeric runs, in order, WITH repeats kept.

    Order is preserved but nothing downstream uses it - the scoring is over multisets, which is what
    makes line reflow free. Kept as a list rather than a Counter so a caller can count words.
    """
    return [w.lower() for w in _WORD.findall(text or "")]


def dpi_for_edge(edge_pt: float, cap: int, base: int) -> int:
    """The DPI production would render a page of this long edge at, under this cap.

    Mirrors `ocr._dpi_for_page`'s arithmetic, and a test pins the two together by driving the real
    function: an instrument that models the code rather than the code's behaviour stops being an
    instrument the moment the code changes. CAP-ONLY, so an ordinary page always returns the base.
    """
    if cap <= 0 or edge_pt <= 0:
        return base
    return max(1, min(base, int(cap * 72 / edge_pt)))


def binding_pages(edges_pt, caps, base: int) -> dict[int, dict[int, int]]:
    """1-based pages where at least one cap lowers the DPI, mapped to that page's DPI per cap.

    The population the decision is actually about. A page no cap touches renders identically in
    every arm, so including it would add a guaranteed 1.0 to every recall and flatter whichever cap
    is worst - see the module docstring on dilution.
    """
    plan: dict[int, dict[int, int]] = {}
    for page, edge in enumerate(edges_pt, start=1):
        dpis = {cap: dpi_for_edge(edge, cap, base) for cap in caps}
        if any(dpi < base for dpi in dpis.values()):
            plan[page] = dpis
    return plan


def spread(candidates, size: int) -> list[int]:
    """`size` items spread evenly across `candidates`, deterministically.

    Spread rather than the first N: the front matter of these records is registration paperwork and
    is not representative of the clinical pages behind it. No randomness, so re-running a document
    scores the same pages and two runs are comparable.
    """
    if size <= 0 or not candidates:
        return []
    ordered = sorted(candidates)
    if len(ordered) <= size:
        return ordered
    step = len(ordered) / float(size)
    return sorted({ordered[min(len(ordered) - 1, int(i * step))] for i in range(size)})


def _shape(counter: Counter) -> dict:
    """Aggregate shape of a word multiset: how many, how long, how alphabetic.

    Numbers only, never the words - which is what keeps the exclusive-word report PHI-safe by
    construction rather than by the caller remembering. The shape is the signal that separates the
    two ways a side can hold words the other does not: speckle read as text is short and rarely
    purely alphabetic, while genuine lost content is neither.
    """
    words = list(counter.elements())
    if not words:
        return {"n": 0, "mean_len": 0.0, "alpha_share": 0.0}
    return {
        "n": len(words),
        "mean_len": round(sum(len(w) for w in words) / len(words), 2),
        "alpha_share": round(sum(1 for w in words if w.isalpha()) / len(words), 3),
    }


def score_page(reference: str, candidate: str) -> dict:
    """Word-level agreement between an uncapped reference render and a capped candidate.

    `recall` is the headline: reference words that survived. `precision` answers the other
    direction, which the text-layer experiment failed to ask - a render can gain tokens as well as
    lose them. `set_recall` and `char_ratio` are diagnostic company, not verdicts:

      - `set_recall` above `recall` means repeated words were thinned rather than removed, which a
        set-based metric would have scored as no loss at all.
      - `char_ratio` near 1.0 while `recall` is low is the exact trap this metric replaces: volume
        agreed 96-101% on pages whose words did not.

    An empty reference scores 1.0 - a page the uncapped render read as blank has nothing to lose, so
    calling that a total failure would put every genuinely blank page in the alarm bucket. Empty
    candidate against a non-empty reference is recall 0.0, which is the alarm working.
    """
    ref_list, cand_list = tokens(reference), tokens(candidate)
    ref, cand = Counter(ref_list), Counter(cand_list)
    kept = ref & cand
    overlap = sum(kept.values())
    ref_set, cand_set = set(ref), set(cand)
    return {
        "ref_words": len(ref_list),
        "cand_words": len(cand_list),
        "recall": 1.0 if not ref_list else overlap / len(ref_list),
        "precision": 1.0 if not cand_list else overlap / len(cand_list),
        "set_recall": 1.0 if not ref_set else len(ref_set & cand_set) / len(ref_set),
        "char_ratio": 1.0 if not reference else len(candidate) / len(reference),
        "lost": _shape(ref - cand),
        "gained": _shape(cand - ref),
    }


def summarize_arm(scores: list[dict]) -> dict:
    """Collapse per-page scores into the distribution, worst first.

    Worst and the below-floor counts lead because the failure mode is concentrated: the measurement
    that blocked this setting averaged 0.94 over a set containing a page at 0.41. `mean` is here for
    completeness and is the least informative number in the row.
    """
    if not scores:
        return {"pages": 0}
    recalls = [s["recall"] for s in scores]
    row = {
        "pages": len(scores),
        "worst": min(recalls),
        "median": statistics.median(recalls),
        "mean": statistics.fmean(recalls),
        "min_precision": min(s["precision"] for s in scores),
        "ref_words": sum(s["ref_words"] for s in scores),
        "lost_words": sum(s["lost"]["n"] for s in scores),
        "gained_words": sum(s["gained"]["n"] for s in scores),
    }
    for floor in _FLOORS:
        row[f"below_{floor}"] = sum(1 for r in recalls if r < floor)
    # Pooled word recall as well as the per-page mean: they differ whenever page word counts differ,
    # and the pooled one is what "6% of characters lost" was reaching for. Both are reported because
    # a dense page losing a tenth of its words and a sparse page losing all of them are the same
    # per-page mean and very different pooled figures.
    total = row["ref_words"]
    row["pooled_recall"] = (total - row["lost_words"]) / total if total else 1.0
    return row


def _ocr_at(pdf_path, page: int, dpi: int) -> tuple[str, float]:
    """OCR one page at an explicit DPI through the production functions, timed.

    Calls `_rasterize` and `_ocr_image` rather than reimplementing them, so the declared `--dpi`
    correction Tesseract needs at a reduced resolution is applied exactly as production applies it.
    Without that the capped arm would be scored against a handicap production does not carry.
    """
    started = time.time()
    images = ocr._rasterize(pdf_path, first_page=page, last_page=page, dpi=dpi)
    text = "".join(ocr._ocr_image(image, dpi=dpi) for image in images)
    return text, time.time() - started


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pdf", required=True, help="a PDF to measure (stays on disk, never copied)"
    )
    parser.add_argument(
        "--caps",
        default="3500,6500",
        help="candidate ocr_max_long_edge_px values, comma separated (default: the two measured)",
    )
    parser.add_argument(
        "--pages", type=int, default=20, help="how many binding pages to score (default 20)"
    )
    args = parser.parse_args(argv)

    pdf_path = pathlib.Path(args.pdf)
    if not pdf_path.is_file():
        print(f"no such file: {pdf_path}")
        return 2
    caps = [int(c) for c in args.caps.split(",") if c.strip()]
    if not caps:
        print("--caps named no values")
        return 2

    base = int(get_settings().ocr_base_dpi)
    edges = ocr._page_long_edges_pt(str(pdf_path))
    if not edges:
        print("could not read page sizes, so no cap can be planned for this file")
        return 2

    plan = binding_pages(edges, caps, base)
    print(f"base DPI {base}, {len(edges)} pages, caps {caps}")
    print(f"pages a cap would lower: {len(plan)} of {len(edges)}")
    if not plan:
        # Not a failure and worth saying plainly: this file has nothing for the setting to act on,
        # so it can neither support nor refute a cap.
        print("\nNo page is large enough for any of these caps to bind. This file cannot inform")
        print("the decision - measure one with oversized pages instead.")
        return 0

    pages = spread(list(plan), args.pages)
    print(f"scoring {len(pages)} of them\n")

    reference: dict[int, str] = {}
    ref_seconds_by_page: dict[int, float] = {}
    for page in pages:
        text, seconds = _ocr_at(str(pdf_path), page, base)
        reference[page] = text
        ref_seconds_by_page[page] = seconds
        print(f"  reference page {page}: {seconds:.1f}s, {len(tokens(text))} words", flush=True)
    ref_seconds = sum(ref_seconds_by_page.values())

    results: dict[int, dict] = {}
    for cap in caps:
        scores, seconds_total = [], 0.0
        print(f"\n--- cap {cap} ---", flush=True)
        for page in pages:
            dpi = plan[page][cap]
            if dpi >= base:
                # This cap does not bind on this page, so the render IS the reference. Scoring it
                # would add a free 1.0; spending an OCR pass on it would add a free speedup of 1.0x.
                continue
            text, seconds = _ocr_at(str(pdf_path), page, dpi)
            seconds_total += seconds
            score = score_page(reference[page], text)
            score["page"], score["dpi"] = page, dpi
            scores.append(score)
            print(
                f"  page {page} @ {dpi} DPI: recall {score['recall']:.3f} "
                f"precision {score['precision']:.3f} set {score['set_recall']:.3f} "
                f"chars {score['char_ratio']:.2f} ({seconds:.1f}s)",
                flush=True,
            )
        row = summarize_arm(scores)
        bound = [s["page"] for s in scores]
        row["seconds"] = round(seconds_total, 1)
        row["bound_pages"] = len(bound)
        # Reference time on the SAME pages, not on all of them: an arm that bound on 3 of 20 pages
        # compared against the whole reference would report a 7x "speedup" it never earned.
        row["ref_seconds"] = round(sum(ref_seconds_by_page[p] for p in bound), 1)
        results[cap] = row

    print("\n=== RESULT ===")
    print(f"reference: {len(pages)} pages in {ref_seconds:.1f}s at {base} DPI")
    print("\nWorst page and the below-floor counts are the summary. The mean is last on purpose.")
    keys = [
        "bound_pages",
        "worst",
        *[f"below_{f}" for f in _FLOORS],
        "min_precision",
        "pooled_recall",
        "median",
        "mean",
        "ref_words",
        "lost_words",
        "gained_words",
        "seconds",
    ]
    header = f"{'metric':16}" + "".join(f"{('cap ' + str(c)):>16}" for c in caps)
    print(header)
    for key in keys:
        line = f"{key:16}"
        for cap in caps:
            value = results[cap].get(key, "-")
            line += f"{value:>16.3f}" if isinstance(value, float) else f"{str(value):>16}"
        print(line)

    print(
        "\nSpeed is only comparable on the pages a cap actually bound, so each arm is set against"
    )
    print("the reference time for those same pages:")
    for cap in caps:
        row = results[cap]
        if not row["bound_pages"]:
            print(f"  cap {cap}: bound on no scored page")
            continue
        speedup = row["ref_seconds"] / row["seconds"] if row["seconds"] else 0.0
        print(
            f"  cap {cap}: {row['seconds']:.1f}s against {row['ref_seconds']:.1f}s "
            f"-> {speedup:.2f}x on {row['bound_pages']} page(s)"
        )

    print("\nChoosing a cap is out of scope (#237). What this run supports is a statement of the")
    print("form: at cap C the worst page kept R of its words, and N pages fell below F.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
