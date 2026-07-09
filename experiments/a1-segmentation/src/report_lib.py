"""Turn experiment metrics into a self-explaining report + a running scoreboard.

Goal: nobody should have to ask a person to interpret a result. Given the
metrics, this module writes (1) a plain-English verdict a non-technical reader
can follow, and (2) the technical numbers for a data scientist, with every term
defined in docs/01-GLOSSARY.md. It also upserts a one-line summary into
EXPERIMENT-LOG.md so progress is visible at a glance.

All thresholds live in SIGNAL_BANDS / DOC_BANDS so they are easy to find and
tune; they are rules of thumb, documented in the glossary.
"""
import csv
import os
from datetime import datetime

# (minimum lift over chance, LABEL, plain meaning). Checked high-to-low.
SIGNAL_BANDS = [
    (0.30, "STRONG", "the model has clearly learned to find document starts"),
    (0.15, "MODERATE", "a real but not-yet-reliable signal worth iterating on"),
    (0.05, "WEAK", "barely above guessing; not usable as-is"),
    (-1e9, "AT CHANCE", "no usable signal -- no better than random guessing"),
]
DOC_BANDS = [
    (0.80, "strong"), (0.60, "good"), (0.30, "partial"), (-1e9, "poor"),
]


def _band(value, bands):
    for thr, *rest in bands:
        if value >= thr:
            return rest[0] if len(rest) == 1 else tuple(rest)
    return bands[-1][1:]


def signal_label(pr_auc, base_rate):
    """Map ranking quality (PR-AUC vs base rate) to a verdict label + meaning."""
    lift = pr_auc - base_rate
    label, meaning = _band(lift, SIGNAL_BANDS)
    return label, meaning, lift


def build_verdict(model_row, rows_by_name, base_rate):
    """Return (one_line, detail_lines, next_step) in plain English."""
    label, meaning, lift = signal_label(model_row["pr_auc"], base_rate)
    doc_band = _band(model_row["doc_f1"], DOC_BANDS)
    chunk_upper = rows_by_name.get("chunk_upper", {})
    beats_upper = model_row["doc_f1"] > chunk_upper.get("doc_f1", 1.0)

    one_line = {
        "STRONG": "This approach works.",
        "MODERATE": "This approach shows promise but is not reliable yet.",
        "WEAK": "This approach is only slightly better than guessing - not usable yet.",
        "AT CHANCE": "This approach does NOT work - it is no better than guessing.",
    }[label]

    detail = [
        f"Ranking quality (PR-AUC) is {model_row['pr_auc']:.2f} versus a "
        f"{base_rate:.2f} random-guess baseline, a lift of {lift:+.2f} -> {meaning}.",
        f"Per-page boundary-F1 is {model_row['bf1']:.2f} "
        f"(precision {model_row['prec']:.2f}, recall {model_row['recall']:.2f}).",
        f"Document-F1 is {model_row['doc_f1']:.2f} ({doc_band}); the current "
        f"approach's best case (chunk_upper) is {chunk_upper.get('doc_f1', float('nan')):.2f}.",
    ]
    if not beats_upper and label in ("AT CHANCE", "WEAK"):
        detail.append("It does not approach the current approach's quality.")

    next_step = {
        "STRONG": "Validate on more cases, then plan productionisation.",
        "MODERATE": "Iterate features/threshold and add data before deciding.",
        "WEAK": "Try richer features (word embeddings) and add more cases.",
        "AT CHANCE": "Change the inputs: try word embeddings instead of TF-IDF, "
                     "and/or page-image/layout features, and expand the dataset.",
    }[label]
    return one_line, detail, next_step, label


def write_report(exp_id, title, method_desc, data_desc, base_rate, rows,
                 model_key, exp_root):
    """Write outputs/<exp_id>/{report.md,results.csv} and update EXPERIMENT-LOG.md.

    rows: list of dicts with keys method, prec, recall, bf1, pr_auc, doc_f1, note.
    model_key: which row is the headline model (e.g. 'model@oracleF1').
    """
    out_dir = os.path.join(exp_root, "outputs", exp_id)
    os.makedirs(out_dir, exist_ok=True)
    by_name = {r["method"]: r for r in rows}
    model_row = by_name[model_key]
    one_line, detail, next_step, label = build_verdict(model_row, by_name, base_rate)
    today = datetime.now().strftime("%Y-%m-%d")

    # results.csv
    with open(os.path.join(out_dir, "results.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["method", "boundary_precision", "boundary_recall",
                    "boundary_f1", "pr_auc", "doc_f1", "note"])
        for r in rows:
            auc = "" if r["pr_auc"] != r["pr_auc"] else f"{r['pr_auc']:.4f}"  # nan-safe
            w.writerow([r["method"], f"{r['prec']:.4f}", f"{r['recall']:.4f}",
                        f"{r['bf1']:.4f}", auc, f"{r['doc_f1']:.4f}", r.get("note", "")])

    # report.md
    table = ["| method | precision | recall | boundary-F1 | PR-AUC | Doc-F1 | note |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        auc = "n/a" if r["pr_auc"] != r["pr_auc"] else f"{r['pr_auc']:.3f}"
        table.append(f"| {r['method']} | {r['prec']:.3f} | {r['recall']:.3f} | "
                     f"{r['bf1']:.3f} | {auc} | {r['doc_f1']:.3f} | {r.get('note','')} |")

    md = f"""# {exp_id}: {title}

_Generated {today}. Terms are defined in [docs/01-GLOSSARY.md](../../docs/01-GLOSSARY.md)._

## Verdict in one sentence

**{one_line}**

## What we tried

{method_desc}

## Data

{data_desc} Base rate (fraction of pages that start a document): **{base_rate:.2f}**.

## Results

{chr(10).join(table)}

## What the numbers mean (plain English)

""" + "\n".join(f"- {d}" for d in detail) + f"""

## Recommended next step

{next_step}

## How to read this

- The headline model row is **`{model_key}`**.
- `naive_chunk` and `chunk_upper` are reference baselines (see the glossary).
- PR-AUC is the fairest single score; compare it to the base rate above. A
  PR-AUC near the base rate means "learned nothing".
"""
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    _update_log(exp_root, exp_id, today, title, model_row, label)
    return out_dir, label


def _update_log(exp_root, exp_id, date, title, model_row, label):
    """Upsert a one-line summary into outputs/_log.csv and render EXPERIMENT-LOG.md."""
    log_csv = os.path.join(exp_root, "outputs", "_log.csv")
    cols = ["exp_id", "date", "title", "boundary_f1", "pr_auc", "doc_f1", "verdict"]
    rows = {}
    if os.path.exists(log_csv):
        with open(log_csv, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["exp_id"]] = r
    rows[exp_id] = dict(exp_id=exp_id, date=date, title=title,
                        boundary_f1=f"{model_row['bf1']:.3f}",
                        pr_auc=f"{model_row['pr_auc']:.3f}",
                        doc_f1=f"{model_row['doc_f1']:.3f}", verdict=label)
    with open(log_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])

    md = ["# Experiment Log (scoreboard)", "",
          "Each row is one experiment. Full write-ups: `outputs/<exp_id>/report.md`.",
          "Verdict bands and metric meanings: `docs/01-GLOSSARY.md`.", "",
          "| Experiment | Date | Boundary-F1 | PR-AUC | Doc-F1 | Verdict |",
          "|---|---|---|---|---|---|"]
    for k in sorted(rows):
        r = rows[k]
        md.append(f"| {r['exp_id']} ({r['title']}) | {r['date']} | "
                  f"{r['boundary_f1']} | {r['pr_auc']} | {r['doc_f1']} | {r['verdict']} |")
    with open(os.path.join(exp_root, "EXPERIMENT-LOG.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
