# Segmentation error triage - where the strict doc-F1 gap lives (2026-07-09)

Zero-Vertex-spend decomposition of the current segmenter's errors against **strict
doc-F1** (a document counts only if BOTH its start and end match gold exactly).
Answers: of the gap between achieved doc-F1 (~0.57-0.61) and the ceiling (0.91),
how much is each error class, and how much is recoverable.

Reproduce: `uv run python src/triage_errors.py` (imports the harness gold loader +
`metrics`; makes no Gemini calls). Recomputed metrics reconcile exactly with the
saved `outputs/naive-diagnosis/<case>/report.md` numbers.

## What was triaged

- **naive_chunk** saved predictions (`outputs/naive-diagnosis/<case>/pred.csv`),
  all three clean cases. naive_chunk sits within ~0.01 doc-F1 of the current
  `1_windows` method and shares its error family (recall ~1.0, over-seg ~1.5), so it
  proxies the live method where per-method `1_windows` predictions were not saved.
  Where the live method differs it is STRICTLY BETTER: the 2026-07-04 overlapping-
  window work eliminated seam severance corpus-wide and recovered Case 1's two
  merges, so live-method merges are likely <= the counts below.
- **sol2 adjacent per-page** (reconstructed from the oracle cache), Case 3 - the
  "detect the boundary once per page" method, for direct comparison.

Buckets use tol = +-2 pages for "near a gold boundary". Page numbers only (no PHI).

## Results

| Case | Gold docs | Strict doc-F1 | Exact-hit boundaries | Shifted +-2 | **Merged (unrecoverable)** | Over-split false starts | over-seg |
|---|---|---|---|---|---|---|---|
| Case 1 | 67 | 0.50 | 63/67 | 2 | **2** (pp 49, 269) | 32 far + ~23 near | 1.78 |
| Case 2 | 63 | 0.53 | 58/63 | 4 | **1** (p 352) | 21 far + ~13 near | 1.51 |
| Case 3 | 51 | 0.57 | 51/51 | 0 | **0** | 16 far + ~10 near | 1.53 |

"exact-TP docs" (both edges exact): 46/67, 42/63, 37/51.

### What-if recovery (start-set surgery, directional)

| Case | base | + snap +-2 shifts | + drop far over-splits | + both |
|---|---|---|---|---|
| Case 1 | 0.46 | 0.73 | 0.58 | **0.95** |
| Case 2 | 0.48 | 0.69 | 0.63 | **0.92** |
| Case 3 | 0.53 | 0.71 | 0.73 | **0.99** |

(These use start-tiled spans on both sides, so they ignore gold's own unlabeled
gaps and slightly overshoot the true 0.91 ceiling; treat as directional.)

### sol2 adjacent per-page (Case 3, 226 cached verdicts)

Strict doc-F1 **0.578**, start-recall 1.00, 0 merges, 14 over-splits - statistically
identical to the chunk method's 0.574. The per-page method finds every boundary
(recall 1.00) and then over-splits at the same rate. Doing boundary detection N
times TIES the window method; it does not beat it.

## Read

1. **The unrecoverable error - merges - is nearly absent.** Across 181 gold docs:
   3 genuine merges total (~1.7%); boundary recall 0.92-1.00. The live overlapping-
   window method recovered Case 1's two merges, so its merge count is likely 0-1
   across all three cases. The silent-summary-loss failure is effectively solved.
2. **Boundary localization is near-perfect.** 51/51, 63/67, 58/63 boundaries land
   exactly; only 0-4 per case are off by +-2. There is no off-by-one problem to chase.
3. **The entire strict doc-F1 gap is over-segmentation.** 119/95/78 predicted vs
   67/63/51 gold; precision 0.53/0.61/0.65. Remove the false splits and strict
   doc-F1 approaches the 0.91 ceiling. Nothing else moves the needle comparably.
4. **Part of the residual is gold convention, not model error.** Some over-splits
   land in unlabeled gold gaps (gold is not a strict partition), which is why the
   ceiling is 0.91, not 1.0.

## Implication for the strict-doc-F1 target

The only lever that raises strict doc-F1 is **cutting over-segmentation without
introducing merges**. `5_accumulate` already tried (more context to merge fragments)
and over-corrected into merges (recall 0.86). Two angles that structurally avoid
that failure and are untested:

- **OCR-text input** (the Mayo lever): many over-splits are the model misreading an
  embedded lab table / signature page / letterhead change as a new document from a
  downscaled image. Full-fidelity text carries continuation ("page 3 of 5") and date
  cues; plausibly cuts mid-document false splits without touching recall.
- **Targeted merge-confirmation pass**: merge only high-confidence adjacent
  fragments (unlike sol5's blanket accumulation), attacking over-splits directly
  while structurally unable to merge distant/distinct documents.

App segmentation/verification code remains unchanged; this is measurement only.
