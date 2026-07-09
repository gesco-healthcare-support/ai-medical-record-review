# Resume: MRR AI segmentation — verify upgrade + git consolidation (handoff 2026-07-09)

## How to work
Helping Adrian, sole dev at Gesco (medical evaluations for workers/unions, LA). Follow global
CLAUDE.md rules: research before asserting (label confidence), ASCII only, ask before key
decisions via the AskUserQuestion modal, discuss findings before building, keep diffs small,
never fabricate. NEVER launch Workflows/agent fan-outs without stating scale + explicit modal
yes (a deep-research run once ate a whole usage window). Prefer direct targeted reads.

## The app
MRR AI ("Medical Record Review"), Flask (Python 3.12, uv) at
**P:\MRR_AI_Source\mrr-line_source**. A QME/AME evaluator uploads one large SCANNED
medical-record PDF (a merged bundle of many sub-documents, 200-2000+ pages). Pipeline:
segment into sub-documents (exact page ranges) -> categorize -> review/correct UI
(/review/<id>) -> summarize -> export to Word. Segmentation is the upstream bottleneck
(Page Stream Segmentation). Current segmenter = overlapping byte-budgeted page-image windows
on Vertex gemini-2.5-flash, then a verify/merge pass.

## GIT STATE (critical - read first)
Repo: GitHub **gesco-healthcare-support/ai-medical-record-review**.
- **main**: frozen at `abe0fae` (2026-06-16). Does NOT contain the segmentation pipeline or
  the UI rework.
- **experiment/segmentation-vertex** (`4842422`): main + the pipeline (48 commits). PUSHED to
  origin this session (was 36 ahead, now in sync).
- **feat/user-accounts-multidoc** (current checked-out branch): pipeline + Evaluators UI rework
  + accounts + multidoc + this session's experiment artifacts. **74 commits ahead of main.**
  PUSHED to origin this session (was LOCAL-ONLY / never backed up; now in sync). Tip commits
  this session: `89c70d6` (experiment scripts) + the docs commit after it.
- **BACKUP DONE 2026-07-09**: both branches on origin. ~3.5 weeks of work (Jun 15 - Jul 8) is
  safe. Nothing at risk. Working tree was clean at handoff (this file is the only new change).

### PENDING: staged PRs to main (Adrian chose "staged PRs: pipeline, then UI/accounts")
- PR 1: `experiment/segmentation-vertex` -> main (48 commits = the pipeline).
- PR 2: `feat/user-accounts-multidoc` -> main (the 24 UI/accounts commits), AFTER PR 1 merges.
- **OPEN DECISION (Adrian to make): squash vs merge-commit.** The branches are STACKED (feat is
  built on experiment/segmentation-vertex). If PR 1 SQUASH-merges, PR 2 needs a rebase +
  force-push of feat to stay clean. If PR 1 uses a MERGE-COMMIT, PR 2 cleanly shows only the 24
  UI commits (recommended for stacked branches; intentional exception to the squash default).
  PR 2 is UI-touching -> needs screenshots per pr-format.md. No PR opened yet; do NOT merge to
  main without Adrian's explicit go.

## The segmentation investigation this session (measure-and-decide, all findings)
Target metric chosen by Adrian: **strict doc-F1** (exact start+end match). All artifacts under
`experiments/a1-segmentation/`, committed. ~$0.23 total Vertex spend.

1. **Error triage** (`ERROR-TRIAGE-RESULTS.md`, `src/triage_errors.py`, zero spend): the strict
   doc-F1 gap is ENTIRELY over-segmentation. Merges (unrecoverable) ~0 (2/1/0 across Case
   1/2/3); localization near-perfect; the per-page "detect boundary N times" method TIES the
   window method (0.578 vs 0.574).
2. **OCR-text / Mayo replication** (`OCR-TEXT-EXPERIMENT-RESULTS.md`, `src/ocr_text_experiment.py`,
   ~$0.15): NEGATIVE. Whole-case holistic text collapses recall (0.66/0.73 on Case 1/2 - merges
   1/4-1/3 of docs); windowed text only on-par; text did NOT cut over-seg -> segmentation is a
   VISION task. Text is ~1.7x the tokens of images (not cheaper).
3. **Prompt levers** (`PROMPT-LEVERS-RESULTS.md`, `src/loosen_prompt_experiment.py` +
   `src/cot_prompt_experiment.py`, ~$0.04): NULL. Loosening the recall-first tiebreak = null;
   chain-of-thought = null. Over-splits are CONFIDENT (not ambiguity-driven), so prompting does
   not reach them. Converges with the app's prior "prompting is measured-immune" note in
   verify_pass.py + the removed confidence enum + literature (zero-shot ceiling).
4. **Over-seg solutions research** (`OVERSEG-SOLUTIONS-RESEARCH.md`, no spend): SOTA = "over-split
   then merge" (AOSM), which the app ALREADY does. Fine-tuning clears >0.9 on TABME++ but that is
   TEXT; ours is VISION -> must be a VLM fine-tune = the self-hosting initiative (~$48-65k). CRF
   low value. Gold-convention (bundle-vs-item + unlabeled gaps) caps the ceiling at 0.91.
5. **Verify/merge upgrade** (`VERIFY-UPGRADE-DESIGN.md` + `VERIFY-UPGRADE-RESULTS.md`,
   `src/verify_upgrade_experiment.py`, ~$0.04, 289 calls): aggressive enriched merge pass (all
   adjacent pairs; oracle = boundary-page images + local OCR text for continuation-sentence /
   "page N of M" + metadata). AUTO mode UNSAFE (harmed 1/6/5 real docs). SUGGEST mode recall-safe:
   catches 5/17/14 over-splits per case (vs current ~1-3), precision 0.83/0.74/0.74, coverage
   19/31/39%. CONCLUSION: the verify upgrade helps the PRODUCT (fewer reviewer merge-clicks in
   suggest mode) NOT the autonomous strict doc-F1 (auto is recall-catastrophic; suggest needs a
   human to apply).

**WHOLE-ARC CONCLUSION:** the current image window-segmenter is near its practical ceiling for
this model. Over-seg is the gap; it is confident + prompt-immune + text-immune; the recall-safe
merge value is reviewer-click reduction. A materially higher AUTONOMOUS accuracy number needs a
VLM fine-tune (self-hosting).

## APPROVED next build: wider suggest-mode verify net
Plan: **docs/plans/2026-07-09-verify-suggest-net.md** (status draft). Adrian APPROVED the plan
and the defaults (wide net, cap 200). Not yet implemented.

Three upgrades to `mrr_ai/services/verify_pass.py` (app code - the "don't touch verify" freeze
is LIFTED for this specific, measured, recall-safe change; do NOT touch segmentation code):
1. Widen `suspect_indices` from (short <=2pp OR same-cat+date) to ALL adjacent pairs, capped at
   a new `VERIFY_SUSPECT_CAP` (default 200); above the cap, prioritize today's triggers first.
2. Add boundary-page OCR text to the `_same_document` oracle (continuation-sentence + "page N of
   M"). OCR is available via `mrr_ai/services/ocr.extract_text_from_selected_pages` (NOT cached).
   Rasterize each boundary page ONCE, reuse for both the PNG and the OCR.
3. Keep `auto=False` (suggest) default + the recall-safe "unclear -> keep" invariant.
Also: `mrr_ai/config.py` add `VERIFY_SUSPECT_CAP=200` + `VERIFY_USE_TEXT=True` (text off-switch /
instant rollback). `tests/unit/test_verify_pass.py` add wider-net, cap, and recall-safety tests
(mock `_same_document` + OCR; synthetic data; no Gemini).
Integration facts: single caller `segment_engine.py:167` `verify_and_merge(pdf_path, rows,
auto=False)`, gated by `VERIFY_MERGE` config; rows carry start/end/title/date/injury_date/flag/
category. Validate with `pytest tests/unit/test_verify_pass.py` + re-run
`src/verify_upgrade_experiment.py` (resumable, ~free from cache) to confirm the capped net
reproduces 5/17/14 catches, 0 recall damage.

**Branch for the verify build:** originally chosen "off main", but main lacks the pipeline.
Adrian's resolution: land the week's work on main FIRST (the staged PRs above), THEN branch
`feat/verify-suggest-net` off the updated main and implement. (Alternatively it could branch off
experiment/segmentation-vertex now, but Adrian wants main updated first.)

## Next-session sequence
1. Decide PR strategy (squash vs merge-commit; recommend merge-commit for PR 1 due to stacking).
2. Open PR 1 (experiment/segmentation-vertex -> main, per pr-format.md 10-section template);
   Adrian reviews + merges. Then PR 2 (feat/user-accounts-multidoc -> main, with UI screenshots).
3. Branch `feat/verify-suggest-net` off updated main; implement the approved verify plan.
4. Validate (pytest + offline harness re-run); open PR for the verify upgrade.

## Environment / auth / gotchas
- Vertex, model gemini-2.5-flash, project gen-lang-client-0785241985, location global, BAA.
  ADC via impersonation, EXPIRES: `gcloud auth application-default login
  --impersonate-service-account=adriang@gen-lang-client-0785241985.iam.gserviceaccount.com`.
  Preflight with `uv run python src/vertex_smoke.py` before any run.
- Experiment harness: `experiments/a1-segmentation/`. Outputs go to the CAPITAL-E path
  `P:\MRR_AI_Source\Experiments\a1-segmentation\outputs\` (gitignored, no PHI). The oracle-cache
  there holds `verify_merge` verdicts (resumable).
- Cases: Case 1 (294pp/67 docs), Case 2 (363pp/63 docs, dense), Case 3 (227pp/51 docs, clean).
  Gold CSVs at `P:\MRR_AI_Source\MR Samples\AI System Samples\Case N\INPUT *.csv`. Tesseract OCR
  cached at `cache/ocr/Case N.jsonl`. ROR cases are patient-named = PHI; use Case 1/2/3 only.
- ROBUSTNESS LESSON: long stall-prone Vertex runs NEED verdict_cache (resumable) + per-call
  progress logging + short deadline. Run verify re-runs with:
  `GENAI_CALL_DEADLINE=60 GENAI_TIMEOUT_MS=60000 GENAI_MIN_INTERVAL=2 uv run python -u
  src/verify_upgrade_experiment.py "Case 3"` and babysit (poll output; if the [i/N] counter
  freezes ~2min, kill + relaunch - it resumes from cache). First verify run wedged ~19min silent
  on DSQ before caching was added.
- Temp-0 run-to-run variance ~±0.05 doc-F1. Keep temperature 0 (needed for reproducible A/Bs).

## Decisions locked this session
- Target metric = strict doc-F1. OCR-text = negative (dropped). Do NOT blindly loosen the
  prompt (tested null). Verify upgrade = APPROVED (wide net cap 200, suggest mode). Backup =
  DONE. Path to main = staged PRs (pipeline then UI). Verify branch = off main after PRs land.
- STILL OPEN: PR squash-vs-merge-commit strategy; then implement the verify plan.
