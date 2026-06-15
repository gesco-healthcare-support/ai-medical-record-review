# Glossary (every term and metric, in plain English)

Read this once and you can interpret any report in this project without help.

## The task

- **Page-stream segmentation (PSS):** the technical name for "split a stack of
  pages back into the separate documents it was made from."
- **Boundary / start page:** a page where a new document begins. This is the
  thing we are trying to predict. In a 300-page case with ~60 documents, ~60
  pages are "starts" and ~240 are "not starts."
- **Label:** the correct answer for a page (1 = start, 0 = not a start), taken
  from a human-made index. The model never sees labels for the pages it is
  tested on.

## How we score a model

We frame it as: for each page, the model outputs a probability that the page is
a start, and we compare its guesses to the human labels.

- **Precision:** of the pages the model *called* starts, what fraction really
  were. Low precision = it cries "new document!" too often (over-splitting).
- **Recall:** of the *real* starts, what fraction the model caught. Low recall =
  it misses real document beginnings (under-splitting, merging documents).
- **F1 / boundary-F1:** a single 0-1 score that balances precision and recall
  (their harmonic mean). Our headline per-page metric. Higher is better.
- **Threshold:** the probability cut-off for calling a page a "start" (e.g.,
  0.5). Lowering it catches more starts (higher recall) but adds false alarms
  (lower precision). We can tune it to trade one for the other.
- **PR-AUC (precision-recall area under curve):** how good the model's
  *ranking* of pages is, across all thresholds. **This is the fairest single
  number for an imbalanced problem like ours.** Compare it to the base rate.
- **Base rate:** the fraction of pages that are starts (~0.20 here). A model
  that guesses randomly scores a PR-AUC roughly equal to the base rate. So
  **PR-AUC of 0.21 when the base rate is 0.20 means the model learned nothing.**
- **Document-F1:** instead of scoring pages, score whole documents. A predicted
  document "counts" only if its start and end pages exactly match a real
  document. This is the closest metric to "did we actually reconstruct the
  documents." Higher is better.
- **Class imbalance:** starts are rare (~20%) vs not-starts (~80%). This is why
  plain accuracy is misleading (always guessing "not a start" scores 80%
  accuracy while being useless) and why we use F1 and PR-AUC instead.

## How we test fairly

- **Leave-one-case-out cross-validation (LOO-CV):** with only a few cases, we
  train on all-but-one case and test on the held-out one, rotating through every
  case. Crucially we split by **whole case**, never by page - because pages in
  the same case are similar, so mixing them across train/test would let the
  model "peek" and inflate the score.

## What "good" looks like (signal bands)

These bands turn PR-AUC-above-base-rate ("lift") into a verdict. **They are
rules of thumb we chose, not laws** - adjust them as we learn more.

| Lift over chance (PR-AUC - base rate) | Verdict | Meaning |
|---|---|---|
| >= 0.30 | STRONG | clearly learned to find document starts |
| 0.15 - 0.30 | MODERATE | a real but not-yet-reliable signal; worth iterating |
| 0.05 - 0.15 | WEAK | barely above guessing; not usable as-is |
| < 0.05 | AT CHANCE | no usable signal; no better than guessing |

For Document-F1 we read it as: < 0.30 poor, 0.30-0.60 partial, 0.60-0.80 good,
> 0.80 strong.

## The baselines we compare against

- **naive_chunk:** mark a start every 100 pages (the literal current chunking
  with no intelligence). A floor - any real model should beat this.
- **chunk_upper:** the real document starts PLUS forced cuts at every 100-page
  line. This is the current approach's *best possible* case (it assumes the AI
  finds every boundary perfectly within each block). It is an unrealistically
  high bar; its real use is to show how much damage the 100-page cutting alone
  does (it drops Document-F1 from 1.0 to ~0.85, i.e., ~15% of documents get
  sliced purely by the chunk lines).

## Methods / features

- **TF-IDF:** represents a page by which words appear and how distinctive they
  are. Fast and simple, but only sees surface words - no meaning, and it suffers
  when the text is noisy OCR.
- **Embedding:** represents a page as a vector of numbers capturing its
  *meaning*, produced by a pretrained language model. Usually much stronger than
  TF-IDF; the planned next experiment.
- **OCR (optical character recognition):** turning scanned page images into text
  (Tesseract). Our PDFs are scanned, so all text comes from OCR and inherits its
  errors - relevant because a "new document" is often signalled by layout the OCR
  flattens away.
