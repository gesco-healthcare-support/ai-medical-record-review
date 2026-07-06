# RESUME PROMPT - paste into the next session verbatim

---

Continue the MRR AI segmentation review app work. Repo: P:\MRR_AI_Source\mrr-line_source,
branch experiment/segmentation-vertex (14+ commits from 2026-07-05/06, suite = 128 green,
working tree should be clean except docs/plans/2026-07-04-vertex-demo-port.md which is an
untracked stale draft). Full session state lives in your memory file
mrr-ai-naive-diagnosis.md - READ IT FIRST; it is dense and authoritative. Also skim
docs/plans/2026-07-05-review-app.md (the app plan, executed) and
P:\MRR_AI_Source\Experiments\a1-segmentation\outputs\sol1-diagnosis\COMPARISON.md
(measurement history).

## Why this work matters
The app (Flask, /review page) turns a scanned workers-comp record into reviewed,
summarized sub-documents. Segmentation over-generates ~2x rows; every measured shortcut
that auto-fixes this EATS REAL DOCUMENTS (worst error class: a missed document silently
never gets summarized). Recall is therefore the sacred metric; extra rows are one-click
merges. Adrian is deciding whether to switch everything to gemini-3.5-flash (4-5x faster,
95 vs 131 rows, BUT 5 vs 1 missed on the single 294pp test) - he will NOT decide on one
PDF; your job is to produce the evidence.

## Task 1 - 8-case bake-off on gemini-3.5-flash (evidence for the model decision)
Run all 8 eligible labeled cases (Case 1/2/3, R1/R3/R4, Manual Case 1/2 - patient-named
ids MUST be aliased in all output; decoder at outputs/naive-diagnosis/ALIASES.txt) through
the FULL app pipeline (segment -> categorize -> verify-suggest) with
GENAI_MODEL=gemini-3.5-flash, one case at a time. Extend
P:\MRR_AI_Source\Experiments\a1-segmentation\outputs\_run_best_model.py (it already does
one case with per-stage request counting) to take a case alias. Score against gold with
the same buckets (exact / missed / near-miss / unlabeled-gap / over-splits) and produce a
per-case table: rows, missed, request counts (seg/cat/verify), wall-clock - side by side
with the 2.5-flash numbers already in COMPARISON.md and memory. R4 gold is repaired
(offset applied); the 3 cases > 500pp stay excluded. STOP after the table: the
model-switch call is Adrian's.

## Task 2 - rethink both prompts FOR 3.5-flash
The prompt-immunity findings (4 iterations, byte-identical boundaries) were measured ON
2.5-flash ONLY; 3.5-flash is a different, more steerable model - retest movability
honestly. Rework (a) SEGMENTATION_PROMPT + SEGMENT_RESPONSE_SCHEMA in
mrr_ai/services/gemini.py, (b) the verification question in
mrr_ai/services/verify_pass.py (_same_document), aimed at 3.5-flash's stronger
instruction-following. Measure on the fast subset (Case 2 + Case 3 + R3) before/after,
same taxonomy. Success bar (pre-declared): fewer missed documents at equal-or-fewer rows
vs the Task 1 3.5-flash baseline; anything else = honest negative result, logged in
EXPERIMENT-LOG.md like the others. Keep temp-0 variance (~+-0.05 bF1, +-1-2 misses) in
mind before claiming movement.

## Task 3 - review-editor features (pre-approved by Adrian, build without re-asking)
In mrr_ai/static/review.js (+ css/html as needed), all verified in-browser via the
Playwright flow used before (synthetic PDF for screenshots; real cases only via DOM
assertions - NO screenshots/snapshots of real-record content):
1. SPLIT a row: per-row action that splits pages s..e into two rows at a user-chosen page
   k (s..k-1, k..e); second row inherits category/date, gets flag 'x'; list stays
   ascending + tiled; client AND server validation already enforce the contract.
2. Add missed document: addRow exists but appends at the end with guessed pages - make
   insertion land correctly anywhere (user types start/end; row sorts into place; gaps
   render as the existing amber gap-dividers). Mostly polish + verify.
3. Row-click -> PDF jumps to the row's start page: ALREADY IMPLEMENTED AND VERIFIED
   (iframe src '/api/pdf#page=N') - just re-verify after your changes, don't rebuild.

## Constraints and environment (hard-won; violating these wastes hours)
- Suggestions, never auto-merge: three model generations + rich context + pro judge all
  wrongly merge ~3-23% - measured, closed. Do not re-litigate without new evidence.
- Vertex auth = user-ADC impersonating adriang@gen-lang-client-0785241985.iam.
  gserviceaccount.com; when calls fail with RefreshError, Adrian must run:
  gcloud auth application-default login --impersonate-service-account=adriang@gen-lang-client-0785241985.iam.gserviceaccount.com
- Run the app/scripts with TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
  (not on PATH). Flask debug reloader is BLIND on the P: drive - restart the server
  manually after every edit. Vertex DSQ congestion varies wildly by time of day; use
  python -u, bounded retries, background tasks for long runs, and kill anything silent
  >15 min (verification calls still lack a per-call HTTP timeout - known gap, fix
  opportunistically).
- Suite green + ruff before every commit; commit by pathspec (git commit -- <paths>);
  never commit outputs/, PDFs, CSVs, or anything patient-named. ASCII only.
- Costs are trivial (~$0.02-0.10/case) but report spend per task.
- Adrian's working style: findings + options in prose for discussion, THEN build;
  decisions via AskUserQuestion; one case at a time with analysis for bake-offs.

## Definition of done
Task 1: the 8-case comparison table delivered + STOP for Adrian's model decision.
Task 2: before/after subset numbers for both prompts, logged.
Task 3: three features working in the browser, committed, suite green.
