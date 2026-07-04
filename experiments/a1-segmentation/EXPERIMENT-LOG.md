# Experiment Log (scoreboard)

Each row is one experiment. Full write-ups: `outputs/<exp_id>/report.md`.
Verdict bands and metric meanings: `docs/01-GLOSSARY.md`.

| Experiment | Date | Boundary-F1 | PR-AUC | Doc-F1 | Verdict |
|---|---|---|---|---|---|
| EXP-001-tfidf-lr (TF-IDF + Logistic Regression) | 2026-06-12 | 0.231 | 0.151 | 0.046 | AT CHANCE |
| EXP-002-embed-lr (MiniLM embeddings + Logistic Regression) | 2026-06-12 | 0.233 | 0.152 | 0.046 | AT CHANCE |

## Notes

- 2026-07-04 (later, same branch): sol1_overlapping_windows LIVE head-to-head on the same
  8 cases (~$0.12): seam severance ELIMINATED corpus-wide (16 severed docs -> 0, false
  seam cuts 17 -> 0; Case 1's two merges recovered), bF1 +0.02..+0.07 on clean-gold cases.
  Found sol1 defect: fixed 30pp overlap degenerates to 2-4pp crawl on dense regions
  (Case 2 tail, 259 KB/pp) -> union accumulates variance over-splits (-0.03 bF1, 2.3x cost);
  fix = overlap scaled to window + vote-merge. Full table:
  `outputs/sol1-diagnosis/COMPARISON.md`.
- 2026-07-04 (branch `experiment/segmentation-vertex`): per-case diagnosis of the ACTUAL
  production method (fixed 100-page chunks) on all 8 cases <= 500pp via
  `src/diagnose_naive.py` (29 Vertex calls, ~$0.08). Recall within +-2 pages = 0.90-1.00
  on every case; the failures are boundary localization scatter (+-1..3pp), seam
  severance (17/21 seam cuts false), the 20 MB inline cap on dense scans, rare short-doc
  merges, and gold defects (R4 ROR gold proven +5 pages off; bundle-granularity
  mismatch on R1/Manual Case 2). Full catalogue:
  `outputs/naive-diagnosis/ISSUES.md` (+ per-case report.md/pred.csv, gitignored).

- 2026-06-16 (branch `experiment/segmentation-prep`): Gemini-free prep for the PSS bake-off -
  test set wired to all 11 cases (3 clean + 8 ROR, gold validated; gold is not a clean
  partition - see `docs/plans/2026-06-16-segmentation-gemini-free-prep.md`), free cues
  strengthened + measured (`tune_cues.py`), Solution 4 hardened (cue pre-cuts + near-boundary
  confirmation), async bake-off path added, and markitdown OCR backend wired (config only).
  All validated offline via `bake_off.py selftest`; the scored bake-off awaits a paid Gemini tier.
