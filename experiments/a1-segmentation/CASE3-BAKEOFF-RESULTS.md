# Segmentation bake-off - Case 3 results (2026-07-08)

Head-to-head of the current production segmenter against the four candidate methods, on
**Case 3** (227 pages, 51 ground-truth documents, clean hand-labeled gold). Run on Vertex
`gemini-2.5-flash`, one call at a time under 429/hang guards. **App code was not changed** -
this only measures the alternatives.

## The table

| Method | Doc-F1 | Recall | Precision | calls |
|---|---|---|---|---|
| `1_windows` (current) | 0.61 | 1.00 | 0.69 | 2 |
| `naive_chunk` (old) | 0.60 | 1.00 | 0.67 | 3 |
| `2_adjacent` (brute force) | 0.58 | 1.00 | 0.66 | 226 |
| `5_accumulate` (the accumulate idea) | 0.46 | 0.86 | 0.62 | 226 |
| `4b_range_probe` (binary search) | 0.44 | 0.65 | 0.75 | 243 |

Reference ceiling (`chunk_upper`, gold starts + forced chunk edges): Doc-F1 0.91, bF1 0.99.

## Full metrics

| Method | Doc-F1 | bF1 | Recall | Precision | over-seg | WindowDiff | calls | $ |
|---|---|---|---|---|---|---|---|---|
| `1_windows` (current) | 0.61 | 0.82 | 1.00 | 0.69 | 1.45 | 0.191 | 2 | 0.010 |
| `naive_chunk` (old) | 0.60 | 0.80 | 1.00 | 0.67 | 1.49 | 0.200 | 3 | 0.009 |
| `2_adjacent_image` (brute force) | 0.58 | 0.80 | 1.00 | 0.66 | 1.51 | 0.209 | 226 | 0.013 |
| `5_accumulate` | 0.46 | 0.72 | 0.86 | 0.62 | 1.39 | 0.240 | 226 | 0.022 |
| `4b_range_probe_cued` (binary search) | 0.44 | 0.69 | 0.65 | 0.75 | 0.86 | 0.240 | 243 | 0.015 |

## What each method is

- **`1_windows` (current method):** overlapping byte-budgeted page windows with seam
  ownership; one segmentation call per window. This is what the app runs today.
- **`naive_chunk`:** the older non-overlapping 100-page chunks (hard cut at every edge).
- **`2_adjacent_image` (brute force):** ask page-by-page "is this a NEW document vs the
  previous page?" One call per page.
- **`5_accumulate`:** greedy - grow a document and ask "does the candidate belong to the
  document so far?" (first + middle + preceding + candidate page images). One call per page.
- **`4b_range_probe_cued` (binary search):** galloping + binary search for each document's
  end page, with cue pre-cuts and a confirmation check.

## Read

- **The current sliding-window method wins** - highest Doc-F1, perfect recall (loses no
  documents), best precision among the recall-1.00 methods, best WindowDiff, and **~100x
  fewer calls** (2 vs 226-243).
- **Recall is the metric that matters most:** a missed boundary merges two documents and
  silently drops a summary (unrecoverable), whereas an over-split is fixed with one click.
- **`5_accumulate` was a negative result:** the document-level context merged away 14% of
  real documents (recall 0.86) *and* did not improve precision (0.62 vs brute force's 0.66).
- **`4b_range_probe` lost 35% of documents** (recall 0.65) - the binary search plus its
  merge-confirmation is too eager to merge.
- Every per-page / probe method underperforms the chunk/window family on this case.

## Caveats

- **Case 3 only.** The ranking matches the broader diagnosis, but a second case (ideally at
  a low-congestion time) would confirm it generalizes.
- Temperature-0 run-to-run variance is ~+-0.05 bF1.
- Guard/robustness details and the full run narrative are in
  `experiments/a1-segmentation/EXPERIMENT-LOG.md` (2026-07-08 entry). Committed in `0cfbf42`.
