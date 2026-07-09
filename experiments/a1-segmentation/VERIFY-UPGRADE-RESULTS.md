# Verify/merge upgrade - offline results (2026-07-09)

Tests how much over-segmentation an aggressive, enriched merge pass recovers WITHOUT
harming recall. Input = saved segmentation predictions (naive-diagnosis pred.csv). For
EVERY adjacent row pair, an enriched continuation oracle (images of the boundary pages +
their OCR text for continuation-sentence / "page N of M" signals + metadata) decides
"is B a continuation of A?" Recall-safe invariant: unclear -> keep. Design:
`VERIFY-UPGRADE-DESIGN.md`. Reproduce: `src/verify_upgrade_experiment.py` (resumable via
verdict_cache; ~$0.04 total, 289 verify calls). App code unchanged.

## Aggressive AUTO-merge - unsafe in every case

| Case | input DocF1 / R / over | auto DocF1 / R / over | real docs harmed |
|---|---|---|---|
| Case 3 | 0.574 / 1.00 / 1.53 | 0.602 / 0.98 / 1.41 | 1 [56] |
| Case 1 | 0.495 / 0.94 / 1.78 | 0.491 / 0.85 / 1.43 | 6 [31,53,138,153,196,284] |
| Case 2 | 0.532 / 0.92 / 1.51 | 0.633 / 0.84 / 1.21 | 5 [162,284,295,316,327] |

Auto-merge harmed recall in ALL 3 cases (1/6/5 real documents silently lost) for inconsistent
doc-F1 changes (+0.028, -0.004, +0.101). CONFIRMED net-negative and unsafe - which is exactly
why production runs verify in SUGGEST mode. The ~1-3% false-merge rate matches the app team's
prior measurement.

## SUGGEST mode - the recall-safe value (0 data loss)

| Case | suggestions | correct (clicks saved) | wrong (declined) | precision | over-split coverage |
|---|---|---|---|---|---|
| Case 3 | 6 | 5 | 1 | 0.83 | 19% (5/26) |
| Case 1 | 23 | 17 | 6 | 0.74 | 31% (17/55) |
| Case 2 | 19 | 14 | 5 | 0.74 | 39% (14/36) |

Recall-safe (nothing auto-merged; false flags are declined). The enriched AGGRESSIVE net
catches 5-17 over-splits/case - a real jump over current-verify's ~1-3 (verify-diagnosis) -
cutting that many reviewer merge-clicks, at 74-83% precision (~1-6 flags to decline/case).
Coverage is modest: ~20-40% of the input over-splits (and part of the uncaught remainder is
gold-convention, not truly mergeable).

## Read - the verify upgrade helps the PRODUCT, not the autonomous metric

- **Auto-merge cannot raise strict doc-F1 safely:** it is recall-catastrophic (1/6/5 real
  docs lost) for inconsistent gains. Not viable.
- **Suggest mode is recall-safe but human-in-the-loop:** it cuts reviewer clicks (best Case 1:
  17 saved) but by definition does not move the AUTONOMOUS doc-F1 (a human applies the merges).
- **Fundamental tension:** improving strict doc-F1 autonomously REQUIRES applying merges,
  which risks recall. So the merge stage's value is reviewer-effort, not autonomous doc-F1.
- **Whole-arc convergence:** the current segmenter is near its practical ceiling; over-seg is
  confident + prompt-immune + text-immune; the recall-safe merge value is click reduction. A
  materially higher AUTONOMOUS number needs a VLM fine-tune (self-hosting initiative).

## If pursued in the app (not this phase)
The recall-safe deployment is a WIDER suspect net in the existing verify_pass, kept in
SUGGEST mode (auto=False) - it would raise correctly-flagged over-splits from ~1-3 to ~5-17
per case, cutting reviewer clicks, with ~1-6 false suggestions to decline per case (no data
loss). Adrian-gated; app code unchanged here.
