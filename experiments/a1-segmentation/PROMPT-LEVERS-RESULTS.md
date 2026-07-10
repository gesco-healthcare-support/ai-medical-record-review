# Prompt-level over-segmentation levers - tested, NULL (2026-07-09)

Follow-up to the error triage (over-seg is the whole strict-doc-F1 gap) and the loosened-
tiebreak finding (over-seg is CONFIDENT, not ambiguity-driven). Question: can any PROMPT
technique cut the confident over-splits without losing recall? Searched the literature,
tested the viable PHI-safe candidates on the IMAGE window method, Case 3. Prompts live in
the harness, not the app; app segmentation code unchanged. Reproduce:
`src/loosen_prompt_experiment.py`, `src/cot_prompt_experiment.py`.

## Results (Case 3, 227pp / 51 docs, same-session A/B, temp-0 variance ~±0.05)

| Prompt variant | DocF1 | Recall | Prec | over-seg | pred |
|---|---|---|---|---|---|
| baseline (production prompt) | 0.608 | 1.000 | 0.689 | 1.45 | 74 |
| loosened tiebreak (continue-unless-clear) | 0.603 | 1.000 | 0.680 | 1.47 | 75 |
| chain-of-thought (reason then spans) | 0.603 | 1.000 | 0.680 | 1.47 | 75 |

All deltas within noise. Over-seg pinned at ~1.45-1.47 across all three. NULL.

## What the research surfaced (and its status for us)

Searched prompt-engineering for LLM document segmentation over-splitting. Most remedies are
things we already do (grouping framing, continuation cues, new-document-signal rules,
structured JSON + range validation, split-then-merge). Two were genuinely untested:
- **merge-when-uncertain bias** -> tested (loosened tiebreak) -> NULL.
- **chain-of-thought reasoning before boundaries** -> tested -> NULL.
Remaining candidate (few-shot IMAGE examples of correct groupings) is blocked: real page
images are PHI. Text-described negative examples are already partly in the prompt's NOT-starts
list.

## Read - prompting is not the lever

Four independent lines converge:
1. Two null experiments today (loosen, CoT): over-seg unmoved.
2. App team's prior finding, in `mrr_ai/services/verify_pass.py`: "Prompting is
   measured-immune, so the fix is verification."
3. Removed per-row confidence enum: model self-reported "high" on 231/232 rows incl. every
   near-miss -> the model is confidently wrong, so reasoning/tiebreak prompts do not reach it.
4. Literature: zero-shot prompting ceiling (GPT-4o 0.703) vs fine-tune 0.967; split-then-merge
   is the recommended over-fragmentation correction.

WHY CoT/loosening fail: the over-splits are CONFIDENT (the model believes a letterhead change
or a same-type same-day page is a clear new document). A tiebreak only fires "when unsure";
CoT reasoning rationalizes the confident call rather than reversing it. The decision must be
CORRECTED with independent per-boundary evidence (the verify/merge stage), or the model must
be retrained (fine-tune) - not re-prompted.

Caveats: Case 3 only, single runs, deltas within temp-0 noise -> "no measurable signal", not
"proven zero". More aggressive loosening (relaxing the same-type/letterhead rules) could move
over-seg but directly risks merges (recall) - the sol5 tension.

## Decision

No prompt solution found. Per plan -> move to designing the verify/merge stage (the
recall-safe split-then-merge correction), per `OVERSEG-SOLUTIONS-RESEARCH.md`. Fine-tuning
(the only >0.9 lever) remains the VLM/self-hosting initiative.
