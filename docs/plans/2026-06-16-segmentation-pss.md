---
status: 0a done + solutions finalized; 0b + bake-off blocked on paid Gemini tier
feature: B2/B3/B4 segmentation - Page Stream Segmentation at scale
branch: experiment/segmentation-pss
approach: measure-first spike in experiments/, then promote the winner
---

# B2 / B3 / B4 - segmentation: formulate 4 solutions, then test

Goal: recover the exact (start, end) page span of every sub-document in a 400-2385pp
scanned medical-record PDF, with **no gaps/overlaps**, minimizing **Gemini token cost**,
robust to an imperfect oracle. B2 (boundary detection) works within a window; B3 (single
call can't hold a whole record) forces chunking; B4 (fixed chunks sever cross-seam docs).
All three are one problem: Page Stream Segmentation (PSS) at scale.

## Decisions locked (from the questions modal)

- **Cost objective = total tokens** (page-images sent x tokens/page + output), not raw call
  count. Gemini has no per-call fee and auto-caches repeated prompts 90%, so the lever is
  "how many page-images we send in total." The harness reports measured $/case.
- **Measure oracle reliability FIRST** (Phase 0) before committing to any algorithm - a
  binary search that assumes a perfect oracle breaks if Gemini is 90% not 100%.
- **Exploit free cues**: detect blank/near-blank separator pages, page-number resets, and
  header/footer (letterhead) similarity between consecutive pages WITHOUT Gemini, to pre-cut
  boundaries for free and shrink the search space.
- **Documents are contiguous & non-interleaved** (validated in Phase 0).
- **Compare idea 4 (range-probe) AND idea 2 (adjacent)** head-to-head, plus 1 and 3.

## Eval harness (reuse + port)

Extend [experiments/a1-segmentation/](../../experiments/a1-segmentation/): it already scores
**boundary P/R/F1 + exact-span Document-F1** against the gold CSVs, with baselines
`naive_chunk` and `chunk_upper` (current approach's BEST case - beating its Doc-F1 is the
bar). Cases: Case 1 (294pp/67docs), Case 2 (363pp/63docs), Case 3 (227pp/51docs); 8 more ROR
cases as stretch. Port needed: the harness uses the old `google.generativeai` SDK + reads the
key from the pre-refactor app.py - move to `google-genai` + `.env`. Add a **token/$ counter**
and a **#calls counter** per method.

PHI/BAA caveat (applies to every option equally): `ai.google.dev` Gemini keys are not under a
Google Cloud BAA; the covered path is Gemini via Vertex / Gemini Enterprise Agent Platform.
Pre-MVP risk decision; flagged, not blocking.

## Phase 0 - characterize before building (cheap, gates everything)

**0a. Free-cue survey.** On Case 1/2/3, detect (no Gemini): blank/near-blank pages (ink/text
density), page-number resets / "Page X of Y" sequences (OCR the corner), and header/footer
text similarity between consecutive pages. Measure: what fraction of TRUE boundaries land on
a free cue (recall), and the false-positive rate. Output: how much the cues can pre-segment
for free.

**0b. Oracle reliability.** Implement two Gemini oracles and score each against gold on a
sample of pages across the 3 cases:
- **Adjacent oracle** (idea 2/3): given page i-1 and page i -> NEW | SAME.
- **Range-probe oracle** (idea 4): given the document that started at page s (page s, or a
  short signature of it) and a candidate page e -> does e still belong to doc(s)? YES | NO.
Report per-oracle accuracy and where it errs (e.g. long reports whose interior pages look
like new letterhead). This decides whether the binary-search solution is viable and sets the
error rate that the robustness design must tolerate.

## Phase 0 results - 0a complete; 0b deferred (paid-Gemini prerequisite)

Implemented in `experiments/a1-segmentation/src/` (images, genai_client+retry, metrics,
cues, oracles, run_phase0). Metrics validated (WindowDiff matches the NLTK example).

**0a free-cue survey (ran on all 3 cases, no Gemini) - recall/precision:**

| Cue | Case 1 | Case 2 | Case 3 |
|-----|--------|--------|--------|
| blank -> next page | 0.00 / - | 0.00 / - | 0.02 / 1.00 |
| page-number "X of Y" | 0.42 / 0.70 | 0.62 / 0.85 | 0.06 / 0.60 |
| header/footer change | 0.63 / 0.27 | 0.40 / 0.18 | 0.39 / 0.27 |
| UNION of cues | 0.87 / 0.33 | 0.76 / 0.29 | 0.41 / 0.27 |

Findings:
- Blank separator pages are essentially absent in these scans -> drop the blank cue.
- "Page X of Y" is the strong, free cue WHERE PRESENT (Case 2: 62% recall @ 85% precision)
  but varies wildly by case (Case 3: 6%). Use opportunistically, never as the sole signal.
- Header/footer change is noisy (precision ~0.2-0.3): a candidate generator, not a decision.
- UNION recall 0.41-0.87 at precision ~0.3: cues can NARROW where boundaries are (and pre-cut
  high-confidence page-number resets) but cannot stand alone.

**0b oracle reliability: DEFERRED (full run).** Blocked by the Gemini free-tier cap (~20
generate_content req/day; `gemini-flash-latest` -> `gemini-3.5-flash`). The oracle code is
implemented + smoke-verified. Running full 0b + the bake-off needs a PAID Gemini tier or
Vertex (also the BAA path for real PHI) - a hard prerequisite for ALL real-record processing.

**First spot-check (9 known-answer probes, Case 3, via `sample_oracle.py`): 9/9 correct** -
adjacent 5/5 (NEW at gold starts, SAME mid-doc), range-probe 4/4 (SAME_DOC in-doc, NEW_DOC
past-end). Directional only (n=9), but no sign of the noisy-oracle risk; the range-probe
oracle works -> Solution 4 (binary search) is viable. Cost calibration: ~2,285 tokens/2-image
call at 150 DPI ~= $0.0002/call, so full 0b (~346 calls) ~= $0.08 - the blocker is the
free-tier REQUEST cap, not dollars.

## Finalized formulation (refined by 0a)

Cross-cutting:
- **Cues narrow, they do not decide.** Use page-number resets as high-confidence pre-cuts;
  use the cue UNION as a *candidate boundary set* that Gemini confirms (cheaper than scanning
  every page) - but always keep a fallback for the 13-59% of boundaries cues miss.
- **Cost objective = total tokens**, reported per method via `usage_metadata` (real
  tokens/page at 150 DPI to be measured in 0b).
- The 4 solutions below are unchanged in mechanism; 0a only refines how each uses cues + sets
  the bake-off. Solution 4's viability is gated on the 0b range-probe accuracy.

## The four solutions (formulated)

### Solution 1 - Overlapping windows + seam reconciliation (oracle = window)
Keep the windowed call (Gemini returns ALL boundaries in a page range), but windows overlap.
Window `W` (<= measured ceiling, ~80-150pp), overlap `O`. Trust each window's boundaries only
in its **interior** `[start+margin, end-margin]`; seam pages are owned by the neighbor whose
interior fully contains the straddling doc. Align window edges to **free-cue (blank) pages**
so cuts fall on safe seams. If `O >= Lmax`, no document is ever severed.
- Tokens: ~`n(1 + O/W)` page-images; few calls; big JSON outputs.
- Lowest-risk evolution of today's design; still window-based.

### Solution 2 - Adjacent per-page NEW/SAME, image (oracle = adjacent)
For page i, send {page i-1 image, page i image} -> enum NEW|SAME. Page 1 = NEW. Cut points by
construction (no gaps/overlaps, no seams). Free cues set obvious boundaries so we skip Gemini
on blank-separated docs.
- Tokens: ~`2n` page-images (each page sent as current then previous). Optimization to test:
  send only the current page image + a short TEXT signature of "the document so far" instead
  of the full previous image -> ~`n` images + small text.
- Root-cause PSS; B3/B4 dissolve; costliest in page-images unless optimized.

### Solution 3 - Adjacent per-page on markitdown markdown (oracle = adjacent, text)
markitdown (mandatory) converts pages -> markdown, then adjacent NEW|SAME on the markdown
text of (prev, current). On these SCANNED records markitdown's default (pdfminer) returns
blank, so this REQUIRES the `markitdown-ocr` plugin = **LLM-vision OCR** (Gemini/OpenAI key)
or Azure Document Intelligence - there is no local path in markitdown.
- Cost reality: markitdown OCR is ~1 LLM-vision pass/page, THEN ~1 boundary call/page ~=
  **2x LLM passes/page** - on scans this is the COSTLIEST path, not the cheapest (the "text
  is cheap" rationale only holds for born-digital PDFs). Formulated as requested; we measure
  whether markitdown's structured markdown buys enough accuracy over Solution 2 to justify it.
- Upside: markdown preserves tables/headers; the boundary call itself is cheap text.

### Solution 4 - Range-probe galloping + binary search (oracle = range-probe) [the leetcode framing]
Find documents left to right. A document starts at `s` (initially 1). Find its end `e` = the
largest page that still belongs to doc(s):
1. **Galloping (exponential) search**: probe `s+1, s+2, s+4, s+8, ...` (oracle: "does page
   s+2^k belong to doc(s)?") until the first NO.
2. **Binary search** between the last YES and the first NO for the exact boundary.
3. Next document starts at the boundary; repeat until end of PDF.
- Cost: ~`O(sum of log(L_i))` probes for documents of length `L_i`; each probe ~2 page-images
  -> e.g. 1000pp/50docs(avg 20pp) ~= ~225 probes ~= ~450 page-images - cheaper in page-tokens
  than Solution 2 (~2000) and even single-pass (~1000), IF the oracle is reliable.
- Free cues shrink each search range (cut at known blank pages first) -> fewer probes.
- **Most sensitive to oracle noise**: one wrong probe moves a boundary. Robustness (gated by
  Phase 0b error rate): re-probe/vote near the found boundary, or confirm it with an adjacent
  check. This is the token-optimal solution under a reliable oracle - Phase 0 tells us if it is.

## Test plan (after formulation sign-off)

1. Port the harness to `google-genai` + `.env`; add token/$ + call counters.
2. Run Phase 0 (free-cue survey + oracle reliability) on Case 1/2/3.
3. Implement Solutions 1-4 behind a common interface; run each on Case 1/2/3 (stretch: 8 ROR
   cases). Score boundary P/R/F1, exact-span Doc-F1, total tokens/$, #calls, vs `chunk_upper`.
4. Pick the winner by Doc-F1 at acceptable $; promote it into `/getPages` in a later PR.

## Open items / risks

- BAA path for Gemini (above) - settle before production.
- markitdown OCR on scans = LLM-vision (cost + same BAA path); Azure DocIntelligence = new
  vendor. Solution 3 is the costliest on scans by construction.
- Oracle noise is the central risk for Solution 4; Phase 0b is the go/no-go.
- Domain transfer: no public PSS corpus resembles medical records; the 3 (+8) labeled cases
  are our only ground truth - results are directional until more labels exist.
