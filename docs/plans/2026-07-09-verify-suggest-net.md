---
status: draft (awaiting Adrian's approval to implement)
date: 2026-07-09
feature: verify-suggest-net-widening
---

# Plan: wider, text-aware SUGGEST-mode verify net

## Goal (one sentence)
Make the MRR AI verify/merge pass flag many more over-splits as one-click merge SUGGESTIONS,
using the page text as extra evidence, while staying 100% recall-safe (suggest only, never
auto-merge). Segmentation code is NOT touched.

## Why (measured this session)
Offline experiments (`experiments/a1-segmentation/VERIFY-UPGRADE-RESULTS.md`) showed a wider
net + local OCR-text signals catch 5-17 over-splits/case in suggest mode (vs the current
~1-3), recall-safe (0 real docs lost; false flags are declined). Prompting, OCR-first
segmentation, and auto-merge were all ruled out earlier in the session.

## The three upgrades (plain -> technical)

1. **Wider suspect net.** Today `suspect_indices` flags only short fragments (<=2pp) OR
   same-category+same-date pairs. Change: consider EVERY adjacent boundary, with a per-document
   CAP so huge bundles stay bounded. Below the cap: check all pairs. Above the cap: keep today's
   triggers first, then fill up to the cap with the remaining boundaries (prioritized).

2. **Text-aware oracle.** `_same_document` currently sends 2 page images + metadata. Add the
   OCR text of the two boundary pages (A's last-page tail + B's first-page head) so the model
   can use continuation-sentence and "page N of M" pagination clues. OCR is already available at
   verify time via `ocr.extract_text_from_selected_pages`.

3. **Stay recall-safe.** `auto=False` remains the default (suggest, never merge). Unclear or
   oracle-failure -> KEEP the boundary (unchanged invariant, already unit-tested).

## Changes by file

### `mrr_ai/services/verify_pass.py`
- `suspect_indices(rows)`: widen to all adjacent pairs, capped at `VERIFY_SUSPECT_CAP`. When
  `len(rows)-1 > cap`, prioritize today's triggers (short + same-cat-date) then near-boundary
  fragments, ascending, truncated to the cap. Return indices ascending (callers unchanged).
- `_same_document(pdf, prev, row)`: include boundary-page OCR text in the prompt. Keep the
  existing image evidence and the "answer YES only on clear continuation, else NO" contract.
- New helper to rasterize each boundary page ONCE and reuse it for both the PNG and the OCR
  (avoid double rasterization; `_page_png` + `extract_text_from_selected_pages` both rasterize
  today). e.g. `_page_image(pdf, page)` -> PIL image; `_png_bytes(img)`; `image_to_string(img)`.
- No change to `verify_and_merge`'s apply loop (suggestions vs auto unchanged).

### `mrr_ai/config.py`
- Add `VERIFY_SUSPECT_CAP` (default 200) and `VERIFY_USE_TEXT` (default True), env-overridable,
  matching the existing config style. `VERIFY_USE_TEXT=False` cleanly falls back to today's
  image-only oracle (easy A/B + instant rollback of the text change).

### `tests/unit/test_verify_pass.py`
- Widened-net test: all adjacent pairs flagged below the cap.
- Cap/prioritization test: on a >cap row list, today's triggers are kept and the total is
  capped.
- Recall-safety preserved: unclear -> keep; oracle failure -> keep (extend existing tests to
  the text-aware oracle; mock OCR + `_same_document`, no Gemini, synthetic data).

## Validation (before claiming done)
1. `pytest tests/unit/test_verify_pass.py` green.
2. Offline harness `src/verify_upgrade_experiment.py` already proved the all-pairs suggest-mode
   numbers on Case 1/2/3 (5/17/14 catches, recall-safe). Confirm the CAPPED net reproduces them
   on the (sub-cap) experiment cases -> the cap only affects huge docs. (Uses the resumable
   verdict cache = ~free re-run.)
3. Report suggest-mode catches + false-flag rate + 0 recall damage, same table as the results doc.

## Open decisions (need Adrian's call)
- **D1 - Branch base.** Recommend a clean `feat/verify-suggest-net` off `main` (trunk), since
  this app change should ship independently of the experiment docs and the unrelated
  Evaluators/multidoc work on `feat/user-accounts-multidoc`. Confirm base = main? And: commit
  the session's experiment artifacts (docs + scripts, currently uncommitted) separately first,
  or leave them?
- **D2 - Net breadth vs false flags.** Default = wide (all pairs, capped), which measured
  74-83% suggestion precision -> reviewers dismiss ~1 in 4 suggestions (harmless clicks). If
  that is too noisy, narrow the net (fewer catches, fewer false flags). Default wide, OK?
- **D3 - Cap value.** 200 default (typical bundles stay fully checked; a 2000pp/600-doc bundle
  is bounded to 200 verify calls ~ tens of minutes added). Adjust?

## Risk / rollback
- Blast radius: one production pipeline file; gated by the existing `VERIFY_MERGE` config and
  the new `VERIFY_USE_TEXT`. Suggest-mode means no auto-merge -> no silent data loss.
- Latency on large docs: bounded by `VERIFY_SUSPECT_CAP` + rasterize-once.
- Rollback: set `VERIFY_USE_TEXT=False` (drops the text change) or `VERIFY_MERGE=False`
  (disables the pass), or revert the branch.

## Non-goals
- No segmentation/prompt changes. No auto-merge. No fine-tuning (that is the separate VLM /
  self-hosting initiative).
