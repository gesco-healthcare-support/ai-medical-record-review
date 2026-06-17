"""Shared experiment pipeline for A1 boundary detection.

One code path for every experiment: load data from the case registry, run
leave-one-case-out cross-validation with a pluggable *featurizer*, score against
the same baselines, and write the self-explaining report. To add an experiment,
write a featurizer (see featurizers.py) and call run() with it.

A featurizer is any object with sklearn-style methods:
    .fit_transform(pairs) -> X     (pairs = list of (current_page_text, prev_page_text))
    .transform(pairs)     -> X
TF-IDF fits on the training fold; embeddings are stateless. The pipeline never
mixes pages from one case across train/test (split is by whole case).
"""
import csv
import json
import os
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_fscore_support

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_lib
from cases import by_id
from config import CHUNK_SIZE, EXP_ROOT, OCR_CACHE

# ----- data -----

def load_pages(case_id):
    """Return (list of page text indexed 0..n-1, n) from the OCR cache, or (None, 0)."""
    path = os.path.join(OCR_CACHE, f"{case_id}.jsonl")
    if not os.path.exists(path):
        return None, 0
    pages = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            pages[d["page"]] = d["text"]
    n = max(pages)
    return [pages.get(i, "") for i in range(1, n + 1)], n


def load_labels(label_csv, n):
    """Return (per-page 0/1 start labels, sorted gold (start,end) spans)."""
    starts, spans = set(), []
    with open(label_csv, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            try:
                s, e = int(row[0]), int(row[1])
            except ValueError:
                continue
            if 1 <= s <= n:
                starts.add(s)
                spans.append((s, min(e, n)))
    y = np.array([1 if p in starts else 0 for p in range(1, n + 1)], dtype=int)
    return y, sorted(spans)


def make_pairs(pages):
    """Each page paired with its previous page: (current_text, previous_text)."""
    return [(pages[i], pages[i - 1] if i > 0 else "") for i in range(len(pages))]


# ----- metric helpers -----

def starts_to_spans(starts, n):
    sp = sorted(p for p in set(starts) if 1 <= p <= n)
    return [(s, (sp[i + 1] - 1) if i + 1 < len(sp) else n) for i, s in enumerate(sp)]


def prf(y_true, y_pred):
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, average="binary", pos_label=1, zero_division=0)
    return p, r, f


def doc_f1(pred_spans, gold_spans):
    g, p = set(gold_spans), set(pred_spans)
    tp = len(g & p)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def available(case_ids):
    """Keep only cases whose OCR cache exists (so partial OCR still runs)."""
    return [c for c in case_ids if os.path.exists(os.path.join(OCR_CACHE, f"{c}.jsonl"))]


# ----- the experiment runner -----

def run(exp_id, title, method_desc, make_featurizer, case_ids,
        model_key="model@oracleF1"):
    cids = available(case_ids)
    if len(cids) < 2:
        raise SystemExit(f"Need >=2 OCR'd cases; have {cids}. Run ocr_prep first.")

    data = {}
    for cid in cids:
        pages, n = load_pages(cid)
        y, gold = load_labels(by_id(cid)["label_csv"], n)
        data[cid] = dict(pairs=make_pairs(pages), y=y, n=n, gold=gold)
        print(f"{cid}: {n} pages, {int(y.sum())} boundaries "
              f"({100 * y.mean():.1f}% positive), {len(gold)} docs")

    # leave-one-case-out CV
    prob = {}
    for test in cids:
        tr_pairs, tr_y = [], []
        for cid in cids:
            if cid == test:
                continue
            tr_pairs += data[cid]["pairs"]
            tr_y += list(data[cid]["y"])
        feat = make_featurizer()
        Xtr = feat.fit_transform(tr_pairs)
        clf = LogisticRegression(max_iter=2000, class_weight="balanced")
        clf.fit(Xtr, np.array(tr_y))
        prob[test] = clf.predict_proba(feat.transform(data[test]["pairs"]))[:, 1]

    all_true = np.concatenate([data[c]["y"] for c in cids])
    all_prob = np.concatenate([prob[c] for c in cids])
    pr_auc = float(average_precision_score(all_true, all_prob))

    rows = []
    for thr_name, thr in [("model@0.5", 0.5), ("model@oracleF1", None)]:
        if thr is None:
            grid = np.unique(all_prob)
            thr = float(max(grid, key=lambda t: prf(all_true, (all_prob >= t).astype(int))[2]))
        pred = (all_prob >= thr).astype(int)
        p, r, f = prf(all_true, pred)
        dfs = [doc_f1(starts_to_spans([i + 1 for i, v in enumerate(prob[c] >= thr) if v],
                                      data[c]["n"]), data[c]["gold"]) for c in cids]
        rows.append(dict(method=thr_name, prec=p, recall=r, bf1=f, pr_auc=pr_auc,
                         doc_f1=float(np.mean(dfs)), note=f"thr={thr:.3f}"))

    def baseline(name, starts_fn):
        ys, preds, dfs = [], [], []
        for c in cids:
            n = data[c]["n"]
            st = set(starts_fn(c, n))
            preds.append(np.array([1 if (i + 1) in st else 0 for i in range(n)]))
            ys.append(data[c]["y"])
            dfs.append(doc_f1(starts_to_spans(list(st), n), data[c]["gold"]))
        p, r, f = prf(np.concatenate(ys), np.concatenate(preds))
        rows.append(dict(method=name, prec=p, recall=r, bf1=f, pr_auc=float("nan"),
                         doc_f1=float(np.mean(dfs)), note=""))

    baseline("naive_chunk", lambda c, n: [1] + list(range(CHUNK_SIZE + 1, n + 1, CHUNK_SIZE)))
    baseline("chunk_upper", lambda c, n: set(s for s, _ in data[c]["gold"])
             | set(range(CHUNK_SIZE + 1, n + 1, CHUNK_SIZE)))

    print(f"\n{'method':16}{'prec':>7}{'rec':>7}{'bF1':>7}{'PRAUC':>7}{'DocF1':>7}")
    for r in rows:
        auc = f"{r['pr_auc']:.3f}" if r['pr_auc'] == r['pr_auc'] else "  n/a"
        print(f"{r['method']:16}{r['prec']:7.3f}{r['recall']:7.3f}{r['bf1']:7.3f}{auc:>7}{r['doc_f1']:7.3f}")

    base_rate = float(all_true.mean())
    data_desc = (f"{len(cids)} cases, leave-one-case-out cross-validation, "
                 f"{len(all_true)} pages total.")
    out_dir, label = report_lib.write_report(exp_id, title, method_desc, data_desc,
                                              base_rate, rows, model_key, EXP_ROOT)
    print(f"\nVerdict: {label}")
    print(f"Report:  {os.path.join(out_dir, 'report.md')}")
    return rows
