---
status: B5 implemented (this PR); B6 deferred until segmentation is finalized
feature: B5 categorization cascade + B6 taxonomy curation
branch: feat/categorization-cascade
approach: tdd for the cascade/taxonomy logic (pure functions, security/business rules); test-after for the /getPages wiring
---

# B5 (categorization cascade) + B6 (taxonomy curation)

Research + implementation plan.

**Status:** B5 (the cascade) is implemented in this PR using a lightweight catalog derived
from the existing taxonomy. B6 (full taxonomy curation) is DEFERRED until after the
segmentation work is finalized. Decisions taken for B5: rules + local embeddings
(sentence-transformers) + Gemini constrained-enum; classify on the title and escalate to
first-page OCR on low confidence; low-confidence/disagreement sets the manual-review flag;
the phantom Group 6 is omitted and the missing category_11 summarization prompt is left for
the summarization work (B5 only emits the category id).

## Goal

Replace the brittle single-stage fuzzy-title matcher with a robust, layered **cascade**
(rules -> constrained-LLM -> embedding cross-check -> fallback) over a **curated taxonomy**,
so sub-documents are categorized accurately and *low-confidence cases are flagged for manual
review* instead of silently dumped into category 100. "Robust" = correct beyond the 3 sample
files, with a measurable accuracy harness.

## How it works today (verified in code)

- `/getPages` (`blueprints/segmentation.py`): Gemini segments the PDF and returns each
  sub-doc's **title**; then `categorize_documents(title, groups)`
  (`services/categorization.py`) maps the title to a category number by **difflib
  `SequenceMatcher` fuzzy match** against the strings in `groups.py` (threshold 0.65); no
  match -> `"100"`. The number is written to the CSV `category` column (col 3 of 6).
- `summarize.py` maps the category number -> a per-category prompt in `prompts.py`.
- **Segmentation works; categorization is the weak link** (Adrian's own finding: "fails on
  fuzzy-matching" / everything defaults to 100).

## Why it fails -- research findings (B5 mechanism)

- **Fuzzy matching is lexical only** ("looks-like"), it cannot capture meaning; Gemini's
  free-text titles vary in phrasing, so they rarely clear 0.65 against the canonical strings
  and fall through to 100. (ipullrank fuzzy-vs-semantic; dataladder fuzzy-matching-101)
- **Gemini supports constrained enum classification**: `response_mime_type="text/x.enum"`
  with `response_schema={"type":"STRING","enum":[...]}`, or an enum field inside a JSON
  schema, forces output to be exactly one of a fixed value set -- so "Gemini emits the
  category number directly" cannot hallucinate an invalid category. Our ~15 categories are
  well under the ~120-enum complexity limit. Structured output guarantees *syntactic*
  validity, not *semantic* correctness -> still validate.
  (https://ai.google.dev/gemini-api/docs/structured-output)
- **2025 best practice is a hybrid/layered cascade** (rules + embeddings + LLM, then
  rerank/fallback), not a single method; traditional/deterministic methods often match
  transformers at far less cost for document-type tasks. (arxiv 2604.04997; procycons
  long-doc-classification-benchmark-2025)
- **LLMs are non-deterministic even at temp 0**; use **self-consistency** (sample K,
  majority vote, confidence = agreement ratio; ~3-5 samples) and a **deterministic rule
  fallback + human-review flag** when confidence is low. (arxiv 2510.13855; arxiv 2506.01951;
  arxiv 2510.25007 clinical coding)

## Taxonomy conflicts -- B6 findings (authoritative source: `docs/reference/Categories Jan 25, 2025.docx`)

The code taxonomy (`groups.py`) has **drifted** from the source doc and contains structural
problems that directly cause mis-categorization:

1. **`groups.py` != the source doc.** Added entries, reworded titles, and duplicates
   ("MRI Lumbar Spine" twice; "X-Ray Report"/"X Ray Report"/"XRay Report").
2. **Group 6 is a phantom.** Absent from the source doc entirely; `groups.py["6"]` is `[]`;
   yet `prompts["category_06"]` exists and `summarize.py` handles `option == 6`. Unreachable
   by fuzzy match (empty list) but wired everywhere else.
3. **`category_11` prompt is MISSING.** `summarize.py` does `prompts["category_11"]` for
   option 11 -> **KeyError crash** for any doc categorized 11. (latent bug)
4. **Section names listed as document types.** Group 5 contains "History of Present
   Illness", "Physical Examination", "Diagnosis" -- these are *sections in almost every
   report*, so titles containing them wrongly match Group 5. (top systematic error)
5. **Overlapping categories** (need explicit edge rules):
   - 12 (QME/AME *Supplemental* Reports) vs 13 (QME/AME Reports).
   - 3 (includes "Laboratory Report") vs 14 ("Results" = lab results).
   - 1 (progress/treating notes) vs 5 (PT/chiro/SOAP) vs 6 (phantom SOAP/daily).
   - 2 (initial consults / "Orthopedic Evaluation") vs 1 ("Orthopedic Follow Up").
   - "Ed (Emergency Department) Provider Notes" sits under Diagnostic (3) -- questionable.
6. **Key inconsistencies.** Fallback returns `"100"` but the taxonomy key is `"Group 100"`;
   source doc says "Group 09" vs code `"9"`.

## Proposed design

### B6 - one curated taxonomy as the single source of truth (`mrr_ai/taxonomy.py`)
Replace the bare `groups.py` lists with a structured taxonomy: for each id (1-14, 100) store
- `name`, `description` (1-2 authoritative sentences),
- `examples` (doc-type titles; reconciled with the source doc; section-names removed),
- `edge_rules` (disambiguation: "X belongs here NOT there because Y"),
- `signals` (keywords/regex for the deterministic rules stage).

The same `description` + `edge_rules` feed BOTH the LLM enum field descriptions AND the
embedding exemplars -> one source of truth, no second drift. Reconcile to the source doc;
remove section-names and duplicates; fix the `"Group 100"`/`"100"` key; resolve Group 6 and
`category_11` (see decisions). Keep category ids aligned with `summarize.py` + `prompts.py`.

### B5 - the cascade (`mrr_ai/services/classification.py`), replaces `categorize_documents`
Each stage returns `(category, confidence, method)`:
1. **Deterministic rules** (precise, explainable): keyword/regex signals on the title
   (PR-2->1, PR-4/P&S/MMI->2, RFA->10, deposition->9, claim/adjudication->7,
   operative/pathology->8, MRI/CT/X-ray/EMG/imaging/lab->3, QME/AME->13, +"supplemental"->12,
   ...). A confident rule hit short-circuits.
2. **Constrained-LLM classification**: a *separate* call (not bundled with segmentation, per
   Adrian's instruction), Gemini enum output over {1..14,100} given the curated descriptions
   + edge rules and the sub-doc title (and optional first-page OCR text). Optional
   self-consistency (K samples, majority vote) -> confidence = agreement ratio.
3. **Embedding cross-check** (optional, deterministic): embed title vs category exemplars;
   nearest category. Used to break ties, verify the LLM pick (agreement -> high confidence),
   and serve as fallback if the LLM is unavailable.
4. **Decide + fallback**: rules-confident OR LLM+embedding agree -> assign. Low
   confidence/disagreement -> assign best guess BUT set `manual_flag="x"` (route to human
   review) instead of silently 100. No signal at all -> 100. Log method+confidence+alternates
   (no PHI bodies) for an accuracy harness.

### Wiring
- New `services/classification.py` (cascade) + `taxonomy.py` (data); services stay
  Flask-free and unit-testable. `/getPages` calls the cascade. Output must be a valid
  `summarize.py` option with a matching prompt (fix 11/6 first). The cascade's low-confidence
  flag ties into the existing `manual_intervention` CSV column.

## Testing (TDD for logic; the 93% suite guards wiring)
- Unit (tdd): rules stage per unambiguous type; **taxonomy integrity test** (every id has a
  prompt + description + >=1 example; no section-names; ids == summarize options); cascade
  decision logic with mocked LLM/embedding (agree->assign, disagree->manual flag; fallback
  ->100).
- Integration (test-after): `/getPages` with mocked Gemini enum classify -> assert CSV
  `category` + `manual_flag`.
- **Accuracy harness**: a small synthetic labelled set (title -> expected category) measuring
  cascade vs old fuzzy matcher -- the "robust beyond 3 files" guard. Synthetic data only.

## Open decisions (Adrian -- needed before coding)
1. **Classifier provider**: Gemini enum (consistent w/ segmentation) vs OpenAI (already used
   for summaries) vs embeddings-first. Recommend: **Gemini enum primary + local-embedding
   cross-check**.
2. **Classify on title only, or title + first-page OCR text?** Recommend: title first,
   escalate to first-page text only for low-confidence cases (saves OCR cost).
3. **Group 6**: define it (SOAP/daily notes split from 5) or merge into 5 and drop
   `category_06`? Taxonomy call.
4. **`category_11`**: add the missing prompt (define what group 11 summarizes) or remap 11.
5. **Edge rules for 12 vs 13 and 3 vs 14** -- confirm the disambiguation.
6. **Embedding model**: local `sentence-transformers` (PHI-safe, new ~heavy dep) vs cloud
   embeddings (BAA). Or ship v1 without the embedding stage (rules + LLM only) and add later.
7. **Self-consistency K** (cost vs reliability), default 3.
8. **Low-confidence surfacing**: is `manual_flag="x"` enough, or also a separate review list?

## Risks
- New deps (sentence-transformers is large) -- can defer the embedding stage to v2.
- Extra per-sub-doc LLM calls (cost/latency) -- mitigated by rules-first short-circuit +
  caching.
- Renumbering during B6 curation must preserve CSV/`summarize`/`prompts` alignment ->
  integrity test is mandatory.
- PHI: LLM classification sends titles/text to the cloud (same path as summarization);
  local embeddings avoid a *new* data flow. No new PHI persisted.

## Sources
- Gemini structured output / enum: https://ai.google.dev/gemini-api/docs/structured-output
- Embeddings vs LLM vs fuzzy: arxiv.org/pdf/2604.04997 ; ipullrank.com/fuzzy-matching-semantic-search ; procycons.com/en/blogs/long-document-classification-benchmark-2025
- Reliability/self-consistency/fallback: arxiv.org/html/2510.13855 ; arxiv.org/pdf/2506.01951 ; arxiv.org/pdf/2510.25007
