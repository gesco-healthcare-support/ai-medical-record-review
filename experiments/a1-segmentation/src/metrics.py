"""Evaluation metrics for page-stream segmentation.

Three families, chosen from the PSS literature + this project's needs:

1. Per-page boundary classification ("is page i the start of a new document?"):
   precision / recall / F1 (the PSS standard), plus imbalance-aware metrics for the ~20%
   positive rate -- Matthews correlation (MCC), Cohen's kappa, balanced accuracy -- and
   PR-AUC for threshold-free scoring.
2. Document spans (the business unit -- a row in the CSV): exact-span Document-F1 (strict:
   both start AND end must match) and length-weighted Document-F1 (OpenPSS-style: getting a
   long QME report right counts more than a 1-page form).
3. Segmentation-distance: WindowDiff and Pk (Pevzner & Hearst 2002) -- forgive near-misses
   that exact-span F1 punishes; lower is better. WindowDiff validated against NLTK's example.

Plus project-specific diagnostics: over/under-segmentation ratio (ties to B7), partition
validity (no gaps/overlaps -- R2), and mean boundary localization error.
"""

import bisect

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    cohen_kappa_score,
    matthews_corrcoef,
    precision_recall_fscore_support,
)

# ----- per-page boundary classification -----


def boundary_metrics(y_true, y_pred):
    """P/R/F1 + imbalance-aware metrics for the binary 'is-a-start' page label."""
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", pos_label=1, zero_division=0
    )
    two_classes = len(set(y_true.tolist())) > 1
    return {
        "precision": float(p),
        "recall": float(r),
        "f1": float(f),
        "mcc": float(matthews_corrcoef(y_true, y_pred)) if two_classes else float("nan"),
        "kappa": float(cohen_kappa_score(y_true, y_pred)),
        "balanced_acc": float(balanced_accuracy_score(y_true, y_pred)),
    }


def pr_auc(y_true, y_score):
    return float(average_precision_score(np.asarray(y_true), np.asarray(y_score)))


# ----- document spans -----


def _span_len(span):
    return span[1] - span[0] + 1


def exact_doc_f1(pred_spans, gold_spans):
    """F1 where a document counts only if BOTH start and end match exactly."""
    gold, pred = set(map(tuple, gold_spans)), set(map(tuple, pred_spans))
    tp = len(gold & pred)
    prec = tp / len(pred) if pred else 0.0
    rec = tp / len(gold) if gold else 0.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def weighted_doc_f1(pred_spans, gold_spans):
    """Length-weighted exact-span F1 (OpenPSS-style): long docs carry more weight."""
    gold, pred = set(map(tuple, gold_spans)), set(map(tuple, pred_spans))
    matched = gold & pred
    gold_w = sum(_span_len(s) for s in gold)
    pred_w = sum(_span_len(s) for s in pred)
    match_w = sum(_span_len(s) for s in matched)
    rec = match_w / gold_w if gold_w else 0.0
    prec = match_w / pred_w if pred_w else 0.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def over_seg_ratio(pred_spans, gold_spans):
    """#predicted / #gold documents. >1 = over-segmenting, <1 = under-segmenting (B7)."""
    return len(pred_spans) / len(gold_spans) if gold_spans else float("nan")


def partition_validity(spans, n):
    """Check predicted spans tile pages 1..n with no gaps or overlaps (R2)."""
    covered = [0] * (n + 2)
    for s, e in spans:
        for p in range(max(1, s), min(n, e) + 1):
            covered[p] += 1
    gaps = sum(1 for p in range(1, n + 1) if covered[p] == 0)
    overlaps = sum(1 for p in range(1, n + 1) if covered[p] > 1)
    return {"gap_pages": gaps, "overlap_pages": overlaps, "valid": gaps == 0 and overlaps == 0}


def mean_boundary_offset(pred_starts, gold_starts):
    """Mean distance (pages) from each gold start to the nearest predicted start."""
    ps = sorted(set(pred_starts))
    if not ps or not gold_starts:
        return float("nan")
    offsets = []
    for g in gold_starts:
        i = bisect.bisect_left(ps, g)
        cands = []
        if i < len(ps):
            cands.append(abs(ps[i] - g))
        if i > 0:
            cands.append(abs(ps[i - 1] - g))
        offsets.append(min(cands))
    return float(np.mean(offsets))


# ----- segmentation-distance (WindowDiff / Pk) -----


def starts_to_boundary_mask(starts, n):
    """Length n-1 mask; index p-1 is 1 iff a new document starts at page p+1 (a cut
    between page p and p+1). Same convention for ref and hyp so the metrics are comparable."""
    s = set(starts)
    return [1 if (p + 1) in s else 0 for p in range(1, n)]


def default_k(n, num_docs):
    """WindowDiff/Pk window = half the mean segment (document) length, >=1."""
    return max(1, round(0.5 * n / num_docs)) if num_docs else 1


def windowdiff(ref, hyp, k):
    """Pevzner & Hearst (2002). 0/1 boundary masks of equal length; lower is better."""
    assert len(ref) == len(hyp)
    windows = len(ref) - k + 1
    if windows <= 0:
        return 0.0
    total = sum(min(1, abs(sum(ref[i : i + k]) - sum(hyp[i : i + k]))) for i in range(windows))
    return total / windows


def pk(ref, hyp, k):
    """Pk (Beeferman et al.); penalizes when the two windows disagree on containing a
    boundary. 0/1 boundary masks of equal length; lower is better."""
    assert len(ref) == len(hyp)
    windows = len(ref) - k + 1
    if windows <= 0:
        return 0.0
    err = sum(
        int((sum(ref[i : i + k]) > 0) != (sum(hyp[i : i + k]) > 0)) for i in range(windows)
    )
    return err / windows


if __name__ == "__main__":
    # Validate WindowDiff against NLTK's documented example (windowdiff(s1,s2,3) == 0.30).
    s1 = [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0]
    s2 = [0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0]
    wd = windowdiff(s1, s2, 3)
    assert abs(wd - 0.30) < 1e-9, f"windowdiff self-test failed: {wd}"
    assert windowdiff(s1, s1, 3) == 0.0
    print("metrics self-test OK (windowdiff matches NLTK example = 0.30)")
