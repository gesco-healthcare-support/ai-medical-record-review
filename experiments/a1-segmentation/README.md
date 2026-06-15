# A1 Spike: Page-Stream Segmentation (document-boundary detection)

A throwaway experiment to decide whether a per-page "starts a new document?"
classifier can recover sub-document boundaries in a merged medical-record PDF
with a **clear margin over the current fixed-100-page chunking**. If yes, it
justifies investing in the real thing; if no, we learn that cheaply.

## Problem (A1)

The production pipeline splits a merged PDF into fixed 100-page chunks before
asking Gemini to find sub-documents, so any document spanning a chunk boundary
is mis-split. The research-backed fix is **Page Stream Segmentation (PSS)**: a
per-page binary classifier ("start of new document: yes/no"), which makes
gaps/overlaps unrepresentable by construction.

## Data

Real CSV-labeled cases from `MR Samples/AI System Samples` (PHI; internal use).
Each case = one scanned input PDF + a human page-range CSV
(`start, end, category, doc_date, injury_date, manual_flag`). A page's label is
"is it a `start` page in the CSV?". Scanned PDFs have no text layer, so pages are
OCR'd with Tesseract first.

| Case | Pages | Gold documents |
|------|-------|----------------|
| Case 1 | 294 | 67 |
| Case 2 | 363 | 63 |
| Case 3 | 227 | 51 |

Only 3 labeled cases -> **leave-one-case-out CV** (an 80:20 split is meaningless
at N=3). 8 more cases exist with `.ror-linked.pdf` indexes; parsing those to
extend the dataset is the follow-up if this spike looks promising.

## Method (first model)

`TF-IDF over (previous page + current page) -> LogisticRegression`
(class_weight balanced for the ~20% positive rate). TF-IDF is the fast first
feature set (no GPU/torch); the feature step is swappable so sentence-embedding
models can be benchmarked next.

Baselines that bracket the current approach:
- `naive_chunk`  - a boundary every 100 pages (literal fixed chunking).
- `chunk_upper`  - gold boundaries + forced cuts at chunk edges = the current
  approach's BEST case. Beating its Document-F1 is the bar.

Metrics: per-page boundary precision/recall/F1, PR-AUC, and exact-span
Document-F1.

## Run

```
PY="P:/MRR_AI_Source/mrr-line_source/.venv/Scripts/python.exe"
"$PY" src/ocr_prep.py      # step 1: OCR -> cache/ocr/<case>.jsonl (resumable)
"$PY" src/train_eval.py    # step 2: train + evaluate -> outputs/results.csv
```

## Caveats

- Scanned input -> features come from imperfect OCR, so A1 quality is coupled to
  OCR quality (A3). If OCR-text underperforms, image/layout features are the
  next lever.
- 3 cases is small; results are directional, not definitive.
- Uses the production app's venv plus `scikit-learn`/`matplotlib` for speed; a
  clean isolated venv is a productionization step.
