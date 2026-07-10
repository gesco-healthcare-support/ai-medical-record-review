# Verify/merge stage upgrade - design (2026-07-09)

Measurement-first design for cutting confident over-splits WITHOUT losing recall, by
strengthening the existing verify/merge pass. Rationale: over-segmentation is the whole
strict-doc-F1 gap (`ERROR-TRIAGE-RESULTS.md`); it is CONFIDENT and prompt-immune
(`PROMPT-LEVERS-RESULTS.md`); the recall-safe correction is split-then-merge
(`OVERSEG-SOLUTIONS-RESEARCH.md`). This is a DESIGN + OFFLINE-EXPERIMENT plan only. No app
code changes until the offline numbers justify it and Adrian approves.

## The current verify pass (baseline to beat)

`mrr_ai/services/verify_pass.py`: flags SUSPECT rows -> one two-image "is B a continuation
of A?" call each -> refuted boundaries become merge suggestions (auto=False) or merges
(auto=True). Recall-safe: unclear -> keep boundary. Measured (verify-diagnosis, 2.5-flash):
- Case 3: 18 suspects, 1 merged (over 1.53 -> 1.51), 0 true boundaries harmed.
- Case 2: 18 suspects, 3 merged (over 1.51 -> 1.46), 0 true boundaries harmed.

It is recall-safe but TIMID: its suspect net (short-fragment <=2pp OR same-category+same-date)
misses most over-splits, and it merges only a handful.

## What the over-splits actually are (from the triage)

Confident false starts, dominated by:
- embedded/attachment pages inside a report (lab tables, imaging summaries, work-status
  forms, copied letters) that look like a new document;
- letterhead/branding/signature/stamp pages inside one report;
- near-boundary scatter (a second start 1-2 pages from a real one);
- same-type same-day runs (some real per-visit splits, some over-splits);
- gold-convention (unlabeled gaps, bundle-vs-item) - NOT fixable by verify.

## Proposed upgrades (each independently measurable)

### U1. Widen the suspect net
Add triggers beyond short-fragment + same-cat+date, so more real over-splits get a check:
- any fragment whose first page is a likely embedded/attachment page (heuristic: no new
  encounter date, high layout similarity to the previous page);
- near-boundary fragments (a start within ~2 pages of another start);
- (bound cost) cap suspects per case; every added suspect is one cheap verify call.

### U2. Enrich the merge oracle with recall-safe, PHI-safe LOCAL signals
The verify decision is per-boundary, so OCR text helps HERE (unlike global segmentation).
Add as evidence to the yes/no continuation call:
- continuation-sentence: previous page ends mid-sentence and this page continues it;
- pagination continuity: "page N of M" sequence crossing the boundary;
- (optional) layout/letterhead similarity across the boundary.
These are the literature's merge signals (AOSM split-then-merge; multi-page post-processing).

### U3. Keep the recall-safety invariant
Unclear -> KEEP the boundary (never merge on weak evidence). The offline gate is
`true boundaries harmed = 0`; any recall damage fails the design.

## Offline experiment (no app change)

1. Take the segmentation predictions for Case 1/2/3 (saved naive/current-method rows).
2. Run current verify vs upgraded verify (U1+U2) with `auto=True` (measure the ceiling).
3. Report per case: over-seg before/after, strict doc-F1 before/after, and TRUE boundaries
   harmed (must be 0). Lead with recall damage.
4. Success = over-seg down + strict doc-F1 up + 0 boundaries harmed, beating current verify.
   Cost: one verify call per suspect (small images, cheap); state exact count before running.

## Open decisions (for Adrian)

- **Suspect-net aggressiveness:** conservative additions (embedded-page + near-boundary) vs
  all-adjacent-pairs (max recall of over-splits, more calls).
- **Signals first:** continuation-sentence is highest-value + cheapest + PHI-safe local text;
  layout-similarity needs more plumbing. Start with continuation + pagination?
- **auto vs suggest:** production stays suggest (one-click); experiment measures auto to see
  the achievable ceiling.
- **Target:** strict doc-F1 (Adrian's choice). Verify can only remove over-splits; it cannot
  fix localization or gold convention, so its ceiling is baseline + (recoverable over-splits).

## Non-goals / constraints
- No app-code change in this phase (harness experiment only).
- Fine-tuning (the only >0.9 lever, a VLM = self-hosting) is out of scope here.
