---
feature: segmentation-bakeoff-fixes
date: 2026-06-17
status: in-progress
base-branch: experiment/segmentation-vertex
related-issues: []
---

## Goal
Repair the two bake-off solutions that fail on Vertex (sol2 async/429, sol1 file-upload), add a
faithful `naive_chunk` production baseline, and document why Case 3 weakens sol4b -- so the next
bake-off measures all candidates against the real current approach.

## Context
- Phase 1 bake-off (2026-06-17) completed only the range_probe family. sol1 and sol2 failed; the
  table's only baseline (`chunk_upper`) is gold-informed, so we cannot see what production actually
  achieves today.
- Runs on Vertex AI under a signed BAA (PHI). Auth = ADC/service-account. Model `gemini-2.5-flash`
  on dynamic shared quota (DSQ); no per-project quota increase is possible.
- Uncommitted in the tree: `genai_client.py`, `vertex_smoke.py` (the prior 429-guard edits). These
  are preserved and folded into this change set.
- Hard constraint: DO NOT run the bake-off (Gemini spend). All verification here is Gemini-free
  (py_compile, ruff, the `bake_off.py selftest` fake-oracle path).

### Verified facts (sources)
- sol2 "Event loop is closed": each async solution runs in its own `asyncio.run()`
  (bake_off.py:86); the module-global `_client` (genai_client.py:47) caches an httpx AsyncClient
  bound to the first loop. httpx pools cannot span loops. HIGH -- python-genai #1518, httpx #2959.
  Fix: build the async client inside each loop (`async with Client().aio` + `aclose()`).
- sol1 `files.upload` rejected on Vertex by design (`if self._api_client.vertexai:` raises).
  HIGH -- python-genai #1803/#2096. Fix: inline the window sub-PDF via
  `types.Part.from_bytes(mime_type="application/pdf")`.
- Inline cap: 20 MB total request, base64 (conservative; do NOT assume the newer 100 MB). HIGH.
  PDFs are 63-143 KB/page (Case1 26.9MB/294pp, Case2 52MB/363pp, Case3 14.4MB/227pp). An 80-page
  window -> ~7-11 MB raw / ~10-15 MB base64 (safe). A 100-page chunk (naive_chunk) -> Case 2 ~14 MB
  raw / ~19 MB base64 (near the edge -> needs a size guard).
- Case 3 root cause (DATA: outputs/phase0.md + bake_off.md): page-number cue recall 0.06 (5 precuts)
  vs 0.40/0.62 for Cases 1/2 -> sol4b loses its pre-cut seeding and degenerates toward sol4,
  under-segmenting (over=0.63). Secondary: confirm's adjacent oracle is high-recall/low-precision
  (0.66) so the relocate branch shifts boundaries off-by-one (bF1 0.59 -> 0.55). HIGH (existing data).

## Approach
- **sol2 (T1):** add an async-client scope in `genai_client` that builds a fresh client bound to the
  running loop and `aclose()`s it on exit; sol2_async opens that scope; bake-off runs sol2 async at
  concurrency=2 with more retries. Chosen over (a) pinning google-genai==1.31.0 (masks the bug, ages
  the SDK) and (b) `Connection: close` headers (kills pooling, slower, leaves __del__ task warnings).
  The scope is the documented pattern and survives repeated `asyncio.run()`.
- **sol1 (T2):** inline the window sub-PDF bytes (build in-memory via PdfWriter -> BytesIO ->
  `Part.from_bytes`). Chosen over GCS `gs://` URIs (needs a BAA bucket + upload infra; unnecessary
  at window=80 which fits inline) and over inlining per-page PNGs (larger payload, loses PDF-native
  page numbering the production prompt relies on). Add a size guard that fails loud above ~19 MB.
- **naive_chunk (T3):** faithful replica of production `/getPages` (segmentation.py:104-131):
  non-overlapping `CHUNK_SIZE`(100)-page chunks, each segmented by the production SEGMENTATION_PROMPT
  via the fixed inline path, page numbers offset, HARD cut at every chunk edge (no seam
  reconciliation). This is literally the current approach -- the real "beat this" number. Chosen over
  the free structural floor (each chunk = 1 doc), which scores ~0 and is uninformative. User decision
  2026-06-17.
- **Case 3 (T4):** investigation only; a Gemini-free diagnostic that quantifies precut recall/precision
  vs gold and doc-length distributions per case, writing a short findings note. No solution change --
  surfacing the finding is the deliverable; any fix is a separate future decision.

## Tasks

- T1: Fix sol2 -- per-loop async client + concurrency=2 + more retries
  - approach: test-after
  - files-touched: [experiments/a1-segmentation/src/genai_client.py,
    experiments/a1-segmentation/src/solutions.py, experiments/a1-segmentation/src/bake_off.py]
  - details:
    - genai_client.py: extract client construction into `_build_client()`; add
      `async_client_scope()` (async context manager) that builds a fresh client inside the running
      loop, exposes it to the async generate path (via a ContextVar so the sync global is untouched),
      and `await`s `aclose()` on exit. `_generate_with_retry_async` resolves the scoped client first.
      Raise effective retries for the heavy image method (bump MAX_RETRIES default 8 -> 10 and/or
      honor a per-run env; keep env-tunable).
    - solutions.py: wrap `sol2_adjacent_image_async` body in `async with genai_client.async_client_scope():`.
    - bake_off.py: run async solutions at concurrency=2 when concurrency is unset (currently 0/serial).
  - acceptance: `bake_off.py selftest` passes, INCLUDING a new regression check that a scope-wrapped
    coroutine survives two sequential `asyncio.run()` calls with no "Event loop is closed" (Gemini-free,
    client build mocked). ruff clean; py_compile OK.

- T2: Fix sol1 -- inline the window sub-PDF instead of files.upload
  - approach: test-after
  - files-touched: [experiments/a1-segmentation/src/oracles.py,
    experiments/a1-segmentation/src/genai_client.py]
  - details:
    - oracles.window_segment: build the sub-PDF in memory (PdfWriter -> io.BytesIO), read bytes, send
      `types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")` with SEGMENTATION_PROMPT via
      `generate_json`. Drop the tempfile + `upload_file`. Add a guard: if base64 size > ~19 MB, raise a
      clear RuntimeError naming the window range (fail loud, not a silent oversized request).
    - genai_client.py: remove the now-dead, Vertex-incompatible `upload_file` (only window_segment used it).
  - acceptance: window_segment no longer references files.upload (grep clean); a Gemini-free unit
    (mock `generate_json`) asserts an `application/pdf` inline Part is built and absolute page offsets
    are correct. ruff clean; py_compile OK.

- T3: Add naive_chunk baseline (faithful production replica)
  - approach: test-after
  - files-touched: [experiments/a1-segmentation/src/solutions.py,
    experiments/a1-segmentation/src/bake_off.py]
  - details:
    - solutions.py: add `naive_chunk_production(pdf_path, n, cost, chunk=CHUNK_SIZE)` -- iterate
      non-overlapping chunks, force a start at each chunk edge, union the per-chunk
      `oracles.window_segment` starts, return spans. Reuses the T2 inline path.
    - bake_off.py: print a `naive_chunk (actual)` scored row right after `chunk_upper (bar)`, with its
      own Cost (Gemini-backed) and full metrics; respect `--only`.
  - acceptance: a selftest case patches `oracles.window_segment` with a fake and asserts naive_chunk
    (1) forces cuts at every chunk edge and (2) recovers the fake's within-chunk starts with correct
    offsets. ruff clean; py_compile OK.

- T4: Investigate Case 3 (offline, no Gemini)
  - approach: code
  - files-touched: [experiments/a1-segmentation/src/diagnose_cases.py (new),
    docs/plans/2026-06-17-segmentation-bakeoff-fixes.md]
  - details: new script computes per case -- strict precut set vs gold (recall/precision/#precuts),
    gold doc-length distribution (mean/median/short-doc count), and restates the 4-vs-4b delta. Writes
    a short findings block to outputs/case_diagnostics.md. Confirms the precut-recall-0.06 finding.
  - acceptance: script runs offline (reads OCR cache + gold only, zero Gemini calls), prints Case 3
    precut recall ~0.06, and writes the findings note. ruff clean; py_compile OK.

## Risk / Rollback
- Blast radius: confined to `experiments/a1-segmentation/src/` plus removal of one dead helper.
  Production segmentation (`mrr_ai/`) is untouched -- T1-T4 only read `SEGMENTATION_PROMPT`/`parse_segment_item`.
- Inline-size risk: naive_chunk at chunk=100 puts Case 2 near the 20 MB cap; the T2 size guard fails
  loud so an oversized chunk surfaces as a clear error, not a corrupted run. Mitigation if it trips:
  drop chunk size or route that chunk via GCS (out of scope now).
- DSQ risk: concurrency=2 + more retries reduces but cannot guarantee zero 429s; if sol2 still 429s,
  fall back to concurrency=1 (env GENAI_CONCURRENCY=1).
- Rollback: `git checkout -- experiments/a1-segmentation/src/` (no commits land until you approve);
  the uncommitted 429-guard edits would be re-applied from this plan's T1 if reverted.

## Verification (Gemini-free; the bake-off is NOT run)
1. `cd experiments/a1-segmentation && .venv/Scripts/python.exe -m py_compile src/*.py` -> clean.
2. `ruff check` on the changed files (genai_client, solutions, oracles, bake_off, diagnose_cases,
   vertex_smoke) -> clean. NOTE (deviation from the draft's broader "ruff check src/"): the full src/
   reports 17 PRE-EXISTING issues in untouched exp002-era scripts (measure_current, pipeline, cases,
   sample_oracle, ...) -- out of scope for these four tasks; left as-is.
3. `.venv/Scripts/python.exe src/bake_off.py selftest` -> all asserts pass, including the new
   T1 cross-loop regression and the T3 chunk-edge assertions.
4. `.venv/Scripts/python.exe src/diagnose_cases.py` -> prints Case 3 precut recall ~0.06 and writes
   outputs/case_diagnostics.md.
5. `grep -rn "files.upload\|upload_file" src/` -> no matches in the experiment src.
6. Manual read: confirm `mrr_ai/` is unchanged (`git status` shows only experiment files + this plan).
   The live Gemini bake-off (sol1/sol2/naive_chunk on real cases) is deferred to you, on request.
