---
status: in progress
feature: Gemini-free prep for the PSS bake-off (5 tasks)
branch: experiment/segmentation-prep (off experiment/segmentation-pss)
approach: per-task flags below; all work validated offline (no Gemini spend)
---

# Segmentation prep - five Gemini-free tasks that de-risk the paid bake-off

Goal: do everything that does NOT need a paid Gemini key now, so that the moment billing is
enabled the full Phase 0b + 4-solution bake-off runs in one sitting on a larger, trustworthy
test set, fast, with a more robust binary-search solution. None of these tasks call Gemini.

Source of the task list: the "Gemini-free work" options surfaced after the free-tier spot
check (9/9 oracle calls correct before the daily cap). User picked all five and asked to RPE
each on a branch off the current one.

## Research findings (verified this session)

- **markitdown OCR wiring** (HIGH - official README, `/microsoft/markitdown`): the
  `markitdown-ocr` plugin reuses the `llm_client` + `llm_model` constructor pattern:
  `MarkItDown(enable_plugins=True, llm_client=OpenAI(...), llm_model="gpt-4o")`. If no
  `llm_client` is provided, OCR is skipped and the standard (blank-on-scans) converter runs.
  The plugin needs no new ML/binary deps. `markitdown-ocr` is NOT yet a project dependency.
- **google-genai async** (HIGH - official docs, `/googleapis/python-genai`): `client.aio`
  exposes async twins of every method, incl. `await client.aio.models.generate_content(...)`.
  Bounded concurrency = `asyncio.Semaphore` + `asyncio.gather`.
- **On-disk data**: the 8 ROR gold CSVs (`cache/ror_labels/`) AND OCR caches for all 11
  cases already exist (generated Jun 12). So the 0a free-cue survey can run on all 11 NOW;
  only the Gemini oracle/bake-off stays blocked. `cache/` + `outputs/` are gitignored (PHI).

## Decisions surfaced (made, not blocking - flag if you disagree)

1. **ROR cases are a SECONDARY tier, never pooled into the verdict.** The 3 AI-System cases
   are hand-typed at physical-document granularity. The 8 ROR cases are derived from
   review-summary hyperlink targets, which can be finer (e.g. one link per lab result) or
   start mid-PDF (front-matter gap). Doc counts hint at this: Aririguzo = 322 docs. We report
   3-clean and 8-ROR metrics SEPARATELY; the 3 clean remain the primary bar, the 8 ROR are
   directional breadth. Task 1 quantifies each case's gold quality so we know which to trust.
2. **Cue tuning objective differs per cue** (cues narrow, they do not decide - locked):
   page-number resets are tuned for PRECISION (they become high-confidence pre-cuts that
   Solution 4 trusts as fixed boundaries); header/footer change is tuned for RECALL (it is
   only a candidate-set generator that Gemini later confirms).
3. **Anti-overfit on threshold tuning**: with only 11 cases (3 reliable) a swept threshold
   overfits. Mitigation: pick thresholds on the 3 clean cases, REPORT (not fit) on the 8 ROR;
   prefer coarse principled thresholds; report per-case sensitivity, not a single global F1.
4. **Voting must be decorrelated.** Temperature-0 Gemini returns the same answer to the same
   image pair, so repeating an identical probe does not vote - it echoes. Near-boundary
   robustness therefore uses INDEPENDENT views: confirm a range-probe boundary with the
   ADJACENT oracle (different prompt + different page pair), and/or perturb DPI. The fake-
   oracle self-test models independent noise so it can prove the cross-check recovers
   boundaries a single noisy oracle would miss.
5. **Async parallelizes only the independent solutions.** sol1 (windows), sol2 / sol3
   (adjacent pairs) are embarrassingly parallel -> async with a semaphore. sol4 (galloping +
   binary search) is inherently sequential (each probe depends on the last) and stays sync;
   we document this rather than fake speedups.

## The five tasks

### Task 1 - Expand the labeled test set to 11 cases  [approach: code]
- `validate_labels.py`: per case report pages, docs, avg/median doc length, #1-page docs,
  front gap (min start > 1), tail coverage, partition validity (gaps/overlaps). Flag
  suspicious cases. Markdown table to `outputs/` (gitignored) + a committed summary table in
  this plan (counts only, no PHI).
- Re-run `ror_to_csv.py` to confirm the 8 CSVs reproduce; assert counts stable.
- Harness: let `run_phase0.py cues` and `bake_off.py run` target `all` (-> `ALL_CASE_IDS`),
  reporting the 3-clean and 8-ROR tiers separately.
- Deliverable now: 0a free-cue survey on all 11 cases (free), saved + summarized.

### Task 2 - Strengthen the free cues  [approach: test-after]
- Footer-band page-number zone: scan only the bottom ~3 lines for the page number (where it
  actually lives) -> higher precision than whole-page scan.
- Broader page-number grammar: "Page X of Y", "Page X", "X of Y", "Pg X", "- X -", a bare
  trailing integer in the footer band. Patterns derived empirically from the OCR cache
  (emit only matched tokens / counts, never PHI).
- Tune `SIM_BOUNDARY_MAX` (header) and `BLANK_INK_MAX` (blank) by sweeping vs gold on the 3
  clean cases; validate on the 8 ROR. `tune_cues.py` prints per-threshold recall/precision/F1.
- Re-run 0a; compare against the locked baseline table; update this plan + the PSS plan.

### Task 3 - Cue-seeded Solution 4 + near-boundary voting  [approach: tdd]
- `sol4b_range_probe_cued(pdf, n, cost, precuts=None, confirm=False)`:
  - seed high-confidence page-number pre-cuts as fixed boundaries -> each galloping range is
    bounded by the next pre-cut (fewer probes, smaller search window);
  - near-boundary confirmation: after binary search finds boundary `hi`, cross-check with the
    ADJACENT oracle at `hi`; on disagreement, re-probe the 1-2 pages around `hi`.
- Self-test in `bake_off.py selftest`:
  - perfect fake oracle -> recovers gold exactly (parity with sol4);
  - noisy fake oracle (independent p-flip) -> sol4b boundary error < plain sol4 (assert
    improvement). Proves the robustness lever works before any Gemini spend.

### Task 4 - Async / batched bake-off  [approach: test-after]
- `genai_client`: `classify_enum_async` via `client().aio.models.generate_content`, same
  retry/backoff (async sleep), shared `asyncio.Semaphore` (default ~8).
- `gather_bounded(coros, limit)` helper; async runners `sol1/2/3_async`. sol4 stays sync.
- `bake_off.py run ... --concurrency=N` uses the async path for the parallel solutions.
- Self-test with a fake async oracle (no Gemini): recovers gold, concurrency bounded.

### Task 5 - Wire markitdown OCR backend (config only)  [approach: code]
- Add `markitdown-ocr` to the `experiment` dependency group.
- `markdown.py`: build `MarkItDown(enable_plugins=True, llm_client=..., llm_model=...)` from
  env. Backends via `MARKITDOWN_OCR_BACKEND`:
  - `openai`  -> `OpenAI(api_key=OPENAI_API_KEY)`, model `MARKITDOWN_LLM_MODEL` (def gpt-4o);
  - `gemini`  -> `OpenAI(api_key=GEMINI_API_KEY, base_url=<gemini openai-compat>)`, a Gemini
    vision model;
  - `none`/unset -> `llm_client=None` (OCR skipped; documented, matches markitdown behavior).
- `markdown.py config_check`: prints the configured backend/model WITHOUT calling the LLM.
- Do NOT run it (no paid key). Cost reality (2x LLM passes/page on scans) already documented.

## Test plan
- Task 1: `validate_labels.py` runs clean on all 11; `ror_to_csv.py` reproduces counts.
- Task 2: `tune_cues.py` + re-run 0a; page-number precision and union recall vs baseline.
- Task 3: `bake_off.py selftest` asserts perfect-oracle parity AND noisy-oracle improvement.
- Task 4: `bake_off.py selftest` async path recovers gold; semaphore caps in-flight calls.
- Task 5: `markdown.py config_check` reports backend with no network call.

## Out of scope (still gated on paid Gemini)
- Running 0b oracle reliability and the real 4-solution bake-off on records.
- Actually OCR-ing scans through markitdown (needs a paid vision key).
- Promoting a winner into `mrr_ai/blueprints/segmentation.py` (a later PR).
