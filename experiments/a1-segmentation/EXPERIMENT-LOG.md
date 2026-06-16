# Experiment Log (scoreboard)

Each row is one experiment. Full write-ups: `outputs/<exp_id>/report.md`.
Verdict bands and metric meanings: `docs/01-GLOSSARY.md`.

| Experiment | Date | Boundary-F1 | PR-AUC | Doc-F1 | Verdict |
|---|---|---|---|---|---|
| EXP-001-tfidf-lr (TF-IDF + Logistic Regression) | 2026-06-12 | 0.231 | 0.151 | 0.046 | AT CHANCE |
| EXP-002-embed-lr (MiniLM embeddings + Logistic Regression) | 2026-06-12 | 0.233 | 0.152 | 0.046 | AT CHANCE |

## Notes

- 2026-06-16 (branch `experiment/segmentation-prep`): Gemini-free prep for the PSS bake-off -
  test set wired to all 11 cases (3 clean + 8 ROR, gold validated; gold is not a clean
  partition - see `docs/plans/2026-06-16-segmentation-gemini-free-prep.md`), free cues
  strengthened + measured (`tune_cues.py`), Solution 4 hardened (cue pre-cuts + near-boundary
  confirmation), async bake-off path added, and markitdown OCR backend wired (config only).
  All validated offline via `bake_off.py selftest`; the scored bake-off awaits a paid Gemini tier.
