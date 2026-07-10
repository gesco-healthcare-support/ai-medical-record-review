---
feature: review-app
date: 2026-07-05
status: approved
base-branch: experiment/segmentation-vertex
related-issues: []
---

# Segmentation Review App (demo build)

**Goal:** A basic fullstack flow in the existing Flask app: upload PDF -> live progress
while segmentation+categorization runs -> editable sub-document list beside a rendered
PDF -> corrected list feeds summarization with progress -> per-document summaries +
Word export. Powered underneath by the Vertex/BAA sliding-window engine (the current
prod path 429s on the free Developer API and cannot run on Vertex at all).

**Decisions (Adrian, 2026-07-05):** Flask + vanilla JS pages (no build tooling);
browser-native iframe PDF viewer with #page=N jumps; rows fully editable (start/end,
category, date, manual flag, merge/delete/add, ascending order); old UI pages stay
untouched (regression safety + manual-CSV plan B). BAA: signed, covers the Vertex
Gemini platform (Adrian confirmed).

**Constraints:** one implementation day; suite + ruff green at every commit; ASCII; no
PHI in code/tests/fixtures; single-process app (one case at a time - matches the
existing global-state design and the one-editor reality); demo browser is Chrome/Edge
(native PDF viewer).

## Architecture

```
review.html (3-step wizard, vanilla JS, polling)
   |  POST /api/case/upload            (reuse upload service; reset state)
   |  POST /api/segment/start          (background thread -> job dict)
   |  GET  /api/segment/status         ({stage, current, total} -> rows when done)
   |  GET  /api/pdf                    (send_file for the iframe; #page=N jumps)
   |  POST /api/summarize/start        (body: edited rows; thread; per-doc progress)
   |  GET  /api/summarize/status       (summaries row-wise when done)
   |  existing /exportresultstoword    (Word export at the end)
services/segment_engine.py  = sliding windows + ownership + full-row metadata + B5
services/windows.py         = byte-budgeted overlapping windows + overlap cap (ported)
services/genai_retry.py     = jittered retries (ported from experiment, proven live)
extensions.py               = Vertex-routed genai client (env-driven)
```

## Tasks

- T1 Vertex client + config (approach: code)
  - files: mrr_ai/config.py, mrr_ai/extensions.py, .env.example
  - USE_VERTEX flag, GENAI_MODEL (gemini-2.5-flash on Vertex else gemini-flash-latest),
    retry knobs, WINDOW_BUDGET_MB=12.5, WINDOW_OVERLAP=30; _build_client() mirrors the
    experiment's proven construction (ADC/impersonation works - DocAI runs prove it).
  - acceptance: suite green with flag unset (dev-API construction unchanged).

- T2 retry wrapper (approach: tdd)
  - files: mrr_ai/services/genai_retry.py, tests/unit/test_genai_retry.py, pyproject (httpx)
  - generate_with_retry(client, **kwargs): 5xx/429/transport retried w/ full jitter,
    per-day quota + non-429 client errors fail fast. Code shape = experiment
    genai_client._generate_with_retry (live-proven).
  - acceptance: unit tests for retry/fail-fast paths pass.

- T3 window packing (approach: tdd)
  - files: mrr_ai/services/windows.py, tests/unit/test_windows.py
  - port _page_raw_sizes, _next_window_start (overlap capped at window//3 - the
    dense-crawl fix), byte_budgeted_windows(pdf_path, n, overlap, budget) -> [(s,e)].
  - acceptance: unit tests: single window when light; overlap preserved when large;
    capped step when dense; fail-fast on oversized page.

- T4 segmentation engine (approach: tdd for ownership/end-derivation, mocked LLM)
  - files: mrr_ai/services/segment_engine.py, tests/unit/test_segment_engine.py
  - run_segmentation(pdf_path, progress) -> rows [{s,e,category,title,date,injury,flag}]:
    per window: inline PDF Part + SEGMENTATION_PROMPT + SEGMENT_RESPONSE_SCHEMA via
    retry -> parse rows (absolute pages);
    OWNERSHIP: window k owns starts in (ws_k, ws_{k+1}]; window-first artifact dropped
    (except absolute page 1); metadata comes from the owning window's row;
    ends re-derived: next surviving start - 1, last = n (output CSV tiles by
    construction - also fixes the silent page-gap defect found in diagnosis);
    then B5 classify per row (title, escalate to first-page OCR on low confidence;
    needs_review OR low conf OR model flag -> flag 'x').
    progress(stage, current, total) callback: "segmenting" per window, "categorizing"
    per row.
  - acceptance: unit test with fake window reports recovers a straddled doc (no seam
    cut), derives tiling ends, drops artifacts; suite green.

- T5 rewire /getPages + delete Files API (approach: test-after)
  - files: mrr_ai/blueprints/segmentation.py, mrr_ai/services/gemini.py,
    mrr_ai/services/classification.py, tests (integration/test_segmentation.py,
    unit/test_gemini.py)
  - /getPages keeps its response contract but calls segment_engine (old UI + plan B
    page keep working); upload_to_gemini/wait_for_files_active deleted;
    classification.py uses GENAI_MODEL + retry.
  - acceptance: full suite green; no references to files.upload remain.

- T6 review API blueprint + job runner (approach: test-after)
  - files: mrr_ai/services/jobs.py, mrr_ai/blueprints/review_api.py,
    mrr_ai/__init__.py (register), tests/integration/test_review_api.py
  - jobs.py: single-slot job {kind, state, stage, current, total, result, error} +
    threading.Thread runner + lock; starting a new job requires previous done (409
    otherwise); upload resets it.
  - endpoints as in Architecture; /api/summarize/start accepts the EDITED rows
    (server-side validation: ints, 1<=s<=e<=n, ascending, no overlaps -> 400 with row
    index on violation; gaps allowed - users skip junk pages deliberately);
    summarization loops rows through the existing per-category prompt internals
    (wrap, don't rewrite summarize.py; extract its inner per-doc block into a helper
    if needed) collecting {row, title, summary} and populating state.all_data so the
    EXISTING /exportresultstoword keeps working.
  - acceptance: integration tests with fake genai/openai: upload->segment->status
    ->rows; edited rows -> summarize -> summaries; validation 400s.

- T7 review UI (approach: code; verified in rehearsal)
  - files: mrr_ai/templates/review.html, mrr_ai/static/review.css,
    mrr_ai/static/review.js, link in templates/index.html
  - one page, 4 states: (1) upload card; (2) progress panel (1s polling, stage text +
    bar); (3) editor: left = rows table sorted ascending (start, end, category
    dropdown of the 14, date input, flag toggle, merge-up, delete; add-row; inline
    validation mirrors server rules; row click -> iframe src=/api/pdf#page=<start>);
    right = iframe viewer; footer "Send to summarization";
    (4) summary progress -> row-wise summaries (header: pages/category/date; body:
    summary text) + "Export to Word" button hitting the existing route.
  - acceptance: synthetic-PDF end-to-end click-through works in Chrome.

- T8 gates + rehearsal (approach: live)
  - Adrian: .env gets GOOGLE_GENAI_USE_VERTEXAI=true,
    GOOGLE_CLOUD_PROJECT=gen-lang-client-0785241985, GOOGLE_CLOUD_LOCATION=global.
  - order: full suite + ruff -> vertex_smoke -> synthetic small PDF through the new
    UI (also warms the MiniLM download) -> REAL demo case end-to-end, timed ->
    save the rehearsal CSV outside the repo as canned plan B (old checkCSV page).
  - acceptance: real case reaches Word export in the new UI; timings recorded for
    demo pacing; OpenAI side confirmed working.

## Risk / Rollback

- Blast radius: /getPages behavior change (engine swap) + new additive blueprint/UI.
  Old pages untouched; manual-CSV path untouched (plan B). Rollback = revert commits;
  flag off (.env) returns Gemini calls to the Developer API path.
- Biggest unknown: summarize.py internals wrap (377 lines, inherited). Mitigation:
  wrap/extract only the per-doc block; if it resists, the job runner calls the
  existing /summarize flow per-row via its service functions and the UI degrades to
  coarser progress. Decide at build, do not rewrite prompts.
- Single-process threads: fine for the demo (one user, one case); documented
  limitation, matches existing state design.
- Summarization wall-clock (50-80 OpenAI calls on the real case) sets demo pacing -
  measured at rehearsal; canned CSV keeps the live demo tight if needed.
