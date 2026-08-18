"""Aggregation for the segmentation boundary A/B: means, spreads, and the paired verdict.

Pure functions over plain dicts. No app imports, no I/O, no Vertex - which is the point. The
harness that produces the raw scores imports segment_engine, which pulls in the torch classifier and
takes roughly 45 seconds to load, so arithmetic living there is arithmetic nobody unit tests. The
numbers here decide whether a prompt change ships, so they are separated to be testable.

That separation is not hypothetical caution. Two comparisons on 2026-08-17 were reported as signal
and both reversed, each because a number was read without the context that qualified it:

  - One arm's TOTAL row covered five documents while another's covered six, because a window died on
    504 DEADLINE_EXCEEDED and the failed run was skipped while the total printed anyway. Two totals
    over different denominators look directly comparable and are not.
  - A 2-3 point precision gap was quoted from one run per arm, of a process later measured swinging
    23 points against itself on the same prompt and the same document (17 rows / 100% exact, then 13
    rows / 76.9%). Temperature 0 is not deterministic here.

So the two rules this module exists to enforce: a total is only summed over documents every arm
finished, and a gap is only called a difference when it exceeds the variation an arm shows against
itself.
"""

import math
import statistics

# The count metrics carried through from the harness's _score. exact_pct is handled separately: it is
# a ratio, so averaging it is not the same as recomputing it from the summed parts.
_METRICS = ("predicted", "exact", "over_split", "under_split", "misaligned")


def summarize(runs):
    """Collapse repeated runs into one mean-and-range entry per (document, arm).

    `runs` maps (document_id, arm) to the list of per-run score dicts the harness produced. Returns
    the same keys mapped to {n, truth, exact_pct_mean, <metric>: {mean, lo, hi}}.

    Range rather than standard deviation, deliberately: at three repeats the observed low and high
    are what the data actually supports, while a standard deviation from n=3 presents the same three
    numbers as a precision estimate they cannot carry.
    """
    out = {}
    for key, scores in runs.items():
        if not scores:
            continue
        entry = {"n": len(scores), "truth": scores[0]["truth"]}
        for metric in _METRICS:
            values = [s[metric] for s in scores]
            entry[metric] = {"mean": statistics.fmean(values), "lo": min(values), "hi": max(values)}
        entry["exact_pct_mean"] = statistics.fmean(s["exact_pct"] for s in scores)
        out[key] = entry
    return out


def comparable_documents(document_ids, arms, runs, repeats):
    """Split documents into those every arm completed in full, and those to exclude from any total.

    Returns (kept, dropped), where dropped carries {arm: missing_run_count} per document so the
    exclusion can be printed. A caller that sums over `kept` cannot produce the invalid TOTAL row
    described in the module docstring; one that ignores `dropped` at least has to ignore it out loud.
    """
    kept, dropped = [], []
    for document_id in document_ids:
        missing = {}
        for arm in arms:
            completed = len(runs.get((document_id, arm), []))
            if completed < repeats:
                missing[arm] = repeats - completed
        if missing:
            dropped.append((document_id, missing))
        else:
            kept.append(document_id)
    return kept, dropped


def totals(arms, document_ids, runs):
    """Per-arm sums of every metric over `document_ids`, averaged across each document's repeats.

    Each document contributes its MEAN across repeats rather than the sum, so a document run three
    times does not outweigh the same document run once, and the totals stay on the same scale as the
    truth row count regardless of --repeats.
    """
    out = {}
    for arm in arms:
        summed = dict.fromkeys(_METRICS, 0.0)
        covered = 0
        for document_id in document_ids:
            scores = runs.get((document_id, arm), [])
            if not scores:
                continue
            covered += 1
            for metric in _METRICS:
                summed[metric] += statistics.fmean(s[metric] for s in scores)
        exact_pct = (
            round(100.0 * summed["exact"] / summed["predicted"], 1) if summed["predicted"] else 0.0
        )
        # Built as a new dict rather than by adding keys to the accumulator: `documents` and
        # `exact_pct` are a count and a ratio, not metrics to be summed, and mixing them into the
        # same mapping the metric loop writes invites a later `for metric in row` over all of it.
        out[arm] = {**summed, "documents": covered, "exact_pct": exact_pct}
    return out


def boundary_votes(scores):
    """How often each page was called a document start across an arm's repeated runs.

    Returns {page: fraction of runs that started a document there}. Page 1 is dropped: every
    segmentation starts a document on the first page, so agreeing there is free and counting it
    inflates every arm identically while diluting the differences we are looking for.

    The fraction is the useful part. A page at 1.0 or 0.0 is a decision the model makes the same way
    every time; anything in between is a coin flip, and on this pipeline there are many of those -
    the same 20 pages produced 10, 11, 12 and 13 rows across seven calls.
    """
    if not scores:
        return {}
    pages = [{s for s, _e in run["spans"]} - {1} for run in scores]
    every = set().union(*pages) if pages else set()
    return {page: sum(page in p for p in pages) / len(pages) for page in every}


def unstable_boundaries(votes):
    """Pages the arm did not decide the same way every run - the noise, counted where it lives.

    A run-to-run range on a total says the output moved; this says WHERE it moved, which is the part
    a prompt change can actually be aimed at.
    """
    return sorted(page for page, share in votes.items() if 0.0 < share < 1.0)


def majority_boundaries(votes):
    """The arm's decision after repeats vote - a page counts as a start if MORE than half agree.

    This is what repeats buy beyond error bars: each boundary is decided by vote instead of by
    whichever single run happened to be sampled, so the comparison between arms runs on denoised
    decisions. Strictly greater than half, so a 50/50 split with an even number of runs is not a
    start - an unstable boundary should not be asserted on a tie.
    """
    return {page for page, share in votes.items() if share > 0.5}


def unscoreable_pages(truth_spans):
    """Pages inside the truth's own range that no reviewed span covers.

    Reviewer ground truth does not always tile: document 5966931a leaves pages 106-108 and 293-294
    uncovered out of 335. On those pages the truth is SILENT, not negative - the reviewer deleted or
    never assigned them - so a model boundary landing there is unscoreable rather than wrong. Counting
    it as a false positive penalises whichever arm happens to split in a hole in the answer key, and
    that document is already the most over-split of the set, so the bias is not harmless.
    """
    if not truth_spans:
        return set()
    covered = set()
    for start, end in truth_spans:
        covered.update(range(start, end + 1))
    first = min(s for s, _e in truth_spans)
    last = max(e for _s, e in truth_spans)
    return set(range(first, last + 1)) - covered


def boundary_score(predicted_starts, truth_spans):
    """Boundary-level precision and recall, in the units the pipeline actually pays in.

    A false positive is a page the model split that the reviewer did not: one merge click. A false
    negative is a boundary the reviewer wanted and the model missed: a document buried inside another
    record, which nobody sees again. Those are the two costs this harness has always tracked; scoring
    them per boundary rather than per whole span is what turns six documents' worth of comparison into
    one observation per boundary.

    Not offered as a canonical metric from the segmentation literature - it is justified here because
    fp and fn map one-to-one onto the two costs we care about, and because whole-span exact match
    throws away every near miss as equally wrong. Note the published caution that boundary metrics
    penalise a near miss twice, once as a false positive and once as a false negative; an off-by-one
    page is scored as two errors here, not a partial credit.
    """
    truth_starts = {s for s, _e in truth_spans} - {1}
    skip = unscoreable_pages(truth_spans)
    predicted = set(predicted_starts) - {1} - skip
    tp = len(predicted & truth_starts)
    fp = len(predicted - truth_starts)
    fn = len(truth_starts - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "truth_boundaries": len(truth_starts),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def paired_boundary_compare(baseline_starts, contender_starts, truth_spans):
    """McNemar counts over every boundary the two arms disagree about.

    Both arms are scored on the SAME pages, so the comparison is paired at the page level. Of the
    pages where exactly one arm is correct, `contender_only` is where the contender is right and the
    baseline wrong, and `baseline_only` is the reverse. Pages where both agree carry no information
    about which arm is better and are excluded - that is McNemar's test, and the exact two-sided
    binomial over those two counts is sign_test_p.

    Why this and not the per-document sign test: six documents cap the attainable p at 0.031, so a
    real difference could not be established even in principle. Boundaries number in the thousands
    across the same runs, at no extra cost.
    """
    truth_starts = {s for s, _e in truth_spans} - {1}
    # Same exclusion as boundary_score: a page the answer key does not cover cannot decide which arm
    # is right, so it must not enter a paired count either.
    skip = unscoreable_pages(truth_spans)
    base = set(baseline_starts) - {1} - skip
    cont = set(contender_starts) - {1} - skip
    considered = (base | cont | truth_starts) - skip
    baseline_only = contender_only = 0
    for page in considered:
        base_right = (page in base) == (page in truth_starts)
        cont_right = (page in cont) == (page in truth_starts)
        if base_right and not cont_right:
            baseline_only += 1
        elif cont_right and not base_right:
            contender_only += 1
    return {
        "baseline_only": baseline_only,
        "contender_only": contender_only,
        "p": sign_test_p(contender_only, baseline_only),
    }


def sign_test_p(wins, losses):
    """Two-sided exact binomial p for `wins` of wins+losses paired comparisons under p = 0.5.

    Ties are excluded by the caller, which is what the sign test does with them. Exact rather than
    normal-approximated because the counts here are single-digit numbers of documents, where the
    approximation does not hold.

    Worth knowing the ceiling before reading the result: with six documents the smallest attainable
    two-sided p is 2/64 = 0.031, so only a clean sweep clears 0.05, and five of six lands at 0.22.
    Anything short of unanimous is indicative at this sample size, not decisive.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    extreme = max(wins, losses)
    tail = sum(math.comb(n, k) for k in range(extreme, n + 1))
    return min(1.0, 2.0 * tail / (2**n))


def compare(baseline, contender, document_ids, summaries):
    """Paired per-document verdict on exact COUNT, plus a sign test across documents.

    Count, not percentage: truth is identical between arms for a given document, so counts are
    directly comparable, whereas a percentage also moves when an arm emits a different number of rows
    and can rise on a document where the arm found fewer boundaries.

    `noise` for a document is the WIDER of the two arms' observed exact ranges - the variation an arm
    shows against itself under identical conditions. A gap inside that band is not evidence.

    `clears` is None, not False, when either arm has fewer than two runs: a single run has no
    observed spread, and no observed spread is not the same as no noise. Reporting False would say
    "measured, did not clear"; None says "not measured". Collapsing those two is the specific mistake
    that produced the reversed calls this module documents.
    """
    rows = []
    wins = losses = ties = 0
    for document_id in document_ids:
        base = summaries.get((document_id, baseline))
        cont = summaries.get((document_id, contender))
        if base is None or cont is None:
            continue
        gap = cont["exact"]["mean"] - base["exact"]["mean"]
        noise = max(
            base["exact"]["hi"] - base["exact"]["lo"], cont["exact"]["hi"] - cont["exact"]["lo"]
        )
        measurable = base["n"] > 1 and cont["n"] > 1
        rows.append(
            {
                "document_id": document_id,
                "baseline_mean": base["exact"]["mean"],
                "contender_mean": cont["exact"]["mean"],
                "gap": gap,
                "noise": noise,
                "clears": (abs(gap) > noise) if measurable else None,
            }
        )
        if gap > 0:
            wins += 1
        elif gap < 0:
            losses += 1
        else:
            ties += 1
    return {
        "baseline": baseline,
        "contender": contender,
        "rows": rows,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "p": sign_test_p(wins, losses),
        "cleared": sum(1 for r in rows if r["clears"]),
        "unmeasured": sum(1 for r in rows if r["clears"] is None),
    }
