# Data and How to Expand It

## The format we need

One **case** = one folder containing:

1. **The input PDF** - the single merged medical-record file (the "MR").
2. **A label CSV** - one row per document found in that PDF:

```
start_page, end_page, category, doc_date, injury_date, manual_flag
22, 27, 1, 2/14/2024, 9/25/2023, x
28, 29, 3, 3/28/2024, -, -
```

| Column | Meaning |
|--------|---------|
| start_page | first page of the document (1-based) |
| end_page | last page of the document |
| category | document-type code (1-14, 100); see the Categories doc |
| doc_date | date on the document (or `-`) |
| injury_date | date of injury (or `-`) |
| manual_flag | `x` if it needs manual handling (bad scan/handwriting), else `-` |

For **A1 (boundary detection) we only need `start_page` and `end_page`.**
Category and dates matter for later experiments (A2 categorisation).

## What we have now

| Label source | Cases | Pages | Boundaries | Quality |
|---|---|---|---|---|
| Hand-typed CSV (`MR Samples/AI System Samples`) | 3 | 884 | 181 | clean, trusted |
| Converted from ROR links (`src/ror_to_csv.py`) | 8 | ~6,066 | ~670 | usable; 3 cases need a spot-check |
| **Total available** | **11** | **~6,950** | **~850** | |

Only 3 cases are clean today. The other 8 are auto-converted from their
"Record of Records" (ROR) hyperlinks and still need OCR + a spot-check.

> Why "cases" matter more than "pages": we must split train/test by whole case
> (see the glossary on LOO-CV). 11 cases is still small. An 80:20 split only
> becomes meaningful in the dozens-of-cases range; below that we use
> leave-one-case-out cross-validation.

## How to expand, ranked by value-for-effort

### 1. Convert the Record-of-Records cases we already have (done; needs finishing)
`src/ror_to_csv.py` reads each `.ror-linked.pdf` and turns its document
hyperlinks into start/end labels. This already grows us from 3 to 11 cases.
**To finish:** (a) OCR the 8 new input PDFs (`python src/ocr_prep.py "<case>"`),
and (b) spot-check the 3 over-range cases -
open the input PDF at a few extracted start pages and confirm a document really
begins there, to rule out a page-numbering offset.

### 2. Harvest the historical archive (biggest single win)
Gesco has already completed many of these reviews. **Every finished case is a
labeled example**: its original merged PDF plus the final page-index (a CSV, or a
linked ROR we can auto-convert). Ask whoever stores completed MRRs for the back
catalogue. This is the fastest path from ~11 to potentially dozens or hundreds of
cases, with no new labeling work.

### 3. Turn the human checkpoint into a data flywheel (most sustainable)
Going forward, **save every job's corrected CSV next to its input PDF** in the
standard folder format. The team already produces these corrections as part of
normal work; capturing them means the dataset grows on its own, for free, every
week. This is a tiny process/code change with the highest long-term payoff.

### 4. Synthetic recombination (augmentation that works today)
We know each document's page range in the 3 clean cases. We can **split each
case into its individual documents and re-stitch them in new random
orders/subsets** to manufacture many new merged PDFs whose boundaries are known
by construction. This multiplies the number of document *transitions* the model
sees (exactly what boundary detection learns from) without any new labeling. It
does not add new document *types*, so use it to complement real cases, not
replace them.

### 5. Fallbacks
- **Manual labeling:** for a high-value unlabeled PDF, a person marks start
  pages. Most expensive; only if 1-4 fall short. A tiny "click the start pages"
  tool would speed this up.
- **Public PSS datasets** (OpenPSS, Tobacco800): non-medical, but useful to
  validate the *method* and pre-train, since they are large and already labeled.

## Adding a case to the experiment

1. Put the input PDF (and its CSV, or a linked ROR) in a folder.
2. Register the case in `src/config.py` (or the ROR list in `src/ror_to_csv.py`).
3. `python src/ocr_prep.py "<case>"` to OCR it (cached, resumable).
4. `python src/train_eval.py` to retrain/evaluate with the larger dataset.

## A note on PHI

These are real records. Processing on internal machines is fine, but the
`cache/` and `outputs/` folders (which contain OCR'd text and page-level data)
are git-ignored so PHI never lands in version control or a shared artifact.
