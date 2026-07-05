# Experiment Log (scoreboard)

Each row is one experiment. Full write-ups: `outputs/<exp_id>/report.md`.
Verdict bands and metric meanings: `docs/01-GLOSSARY.md`.

| Experiment | Date | Boundary-F1 | PR-AUC | Doc-F1 | Verdict |
|---|---|---|---|---|---|
| EXP-001-tfidf-lr (TF-IDF + Logistic Regression) | 2026-06-12 | 0.231 | 0.151 | 0.046 | AT CHANCE |
| EXP-002-embed-lr (MiniLM embeddings + Logistic Regression) | 2026-06-12 | 0.233 | 0.152 | 0.046 | AT CHANCE |
| SIGNALS-v1/v2 (local cues: pagenum/banner/header/dates/bytes/phash as verification-targeting signals; hard flags then continuous + LOO logistic combo) | 2026-07-04 | split-AUC 0.57-0.69 (bar 0.70) | - | - | NOT VIABLE |

## Notes

- 2026-07-05 (DocAI benchmark): Google Custom Splitter (zero-shot pretrained v1.6-pro,
  $5/1k pages) scored on all 8 eligible cases with the shared harness: statistical TIE
  with our sol1 on boundaries at ~100x the cost ($10.6 vs $0.10/suite), slightly worse
  recall (1-3 missed docs per clean case vs our 0-2), confidence <0.5 on every entity.
  Convergence finding: DocAI reproduces our methods' exact disagreement pages ->
  residual error is answer-key convention + genuine ambiguity, method-independent.
  Table: `outputs/sol1-diagnosis/COMPARISON.md`; adapter `src/docai_splitter.py`.

- 2026-07-04 (signals verdict): local structural signals are NOT VIABLE for verification
  targeting on these documents (pre-declared bar: consistent AUC >= 0.70). Best single
  feature was the 256-bit dHash at 0.76 on Case 3 but 0.58-0.59 elsewhere; dates_shared
  FLIPS direction between cases; fax banners are absent from these scans; LOO logistic
  combo peaked at 0.69/0.62/0.57. Same small-sample-promise-then-collapse pattern as
  EXP-002. Verification targeting stays on the model-self-contradiction suspicion
  (same-type+date, enclosure slivers), which remains measured recall-safe. Tooling:
  `src/signals.py` (eval + eval2). Answer keys: R4 shifted -5 (confirmed twice);
  R3/R2/Manual Case 3 offset-suspect (thin evidence, not applied); adjudication sheets
  in `outputs/key-repair/`.

- 2026-07-04 (fix round): overlap cap (window//3) kills the dense-crawl regression (Case 2:
  15 -> 7 calls, bF1 0.70 -> 0.74, best measured); overlap-zone vote ablated live -> defaults
  OFF (turns near-misses into outright merges); prompt start-page rules added but NO measurable
  effect (R3 scatter = gold-convention mismatch). Fast test subset fixed as Case 2 + Case 3 + R3.
  Details: `outputs/sol1-diagnosis/COMPARISON.md` (fix-round section).
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
