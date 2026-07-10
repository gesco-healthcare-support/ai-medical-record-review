# Over-segmentation solutions research (2026-07-09)

External-literature survey of techniques to reduce page-stream-segmentation (PSS)
over-segmentation WITHOUT losing recall, cross-referenced with this project's measured
evidence. Prompted by: the strict-doc-F1 gap is entirely over-segmentation
(`ERROR-TRIAGE-RESULTS.md`), and OCR-text input does not fix it
(`OCR-TEXT-EXPERIMENT-RESULTS.md`). No spend; direct web search.

## Headline: we already run the SOTA paradigm - the lever is the merge stage

The proven approach to over-segmentation is "over-split then merge": deliberately
over-segment, then apply an adaptive merge pass (getting boundaries perfect in one shot is
harder). Our architecture already IS this - segment recall-first (SEGMENTATION_PROMPT) ->
verify_pass merge. So the improvement is not a new paradigm; it is that our merge stage is
currently timid (merges ~1-3 rows/case).

## Options, ranked for our situation

### 1. Strengthen the verify/merge pass - highest leverage, lowest risk, cheapest (recommended)
Post-processing merge literature uses signals our verify pass does not yet exploit:
- continuation detection: a page ending mid-sentence (no terminal punctuation) whose next
  page continues it -> strong SAME; pagination continuity ("page 3 of 5").
- layout/font similarity across the boundary.
- widen the suspect net beyond today's trigger (short-fragment + same-category-same-date),
  which is why verify currently merges only ~1-3 rows/case.
Key synthesis: OCR text FAILED for boundary DETECTION (recall collapse, our experiment) but
is genuinely useful for boundary VERIFICATION - checking "does this specific page pair
continue?" locally is where continuation text helps, without the global recall loss.
Recall-safe by construction (verify only merges; unclear -> keep). Testable offline first.

### 2. Fine-tuning - biggest proven lift, but must be a VISION model for us
Fine-tuned decoder LLMs reach doc-F1 >0.9 on TABME++ (Mistral-7B QLoRA, one H100,
start/continue over a local page window). Strongest precision result anywhere. BUT that
benchmark is synthetic TEXT business docs; we measured our medical-scan segmentation to be a
VISION task (text loses recall). So for us this must be a VLM fine-tune = the self-hosting
initiative already scoped (~$48-65k, 2-4 GPUs). High effort, deferred; the only lever shown
to clear 0.9. See [[mrr-ai-self-hosting-eval]].

### 3. CRF / transition smoothing - low value for us
BiLSTM-CRF / Semi-Markov CRF learn a transition matrix that penalizes rapid label switching
to kill spurious boundaries. Fixes ISOLATED RANDOM over-splits; ours are SEMANTIC (embedded
lab tables, same-day batches) and recall is already 1.00. Marginal fit; adds an ML layer.

### 4. Gold-convention ceiling - not a model problem
A chunk of "over-seg" is bundle-vs-item convention + unlabeled gold gaps (project finding I5;
corroborated by the DocSplit benchmark). No algorithm fixes this - only relabeling. It is why
the ceiling is 0.91, so the truly addressable over-seg is smaller than the ~1.5x ratio suggests.

## Corroborating note
The TABME++ authors found strict F1 "masks operational reality" and switched to a human-effort
metric (minimum drag-and-drops). Same tension we hit: over-splits are 1-click fixes, merges
are catastrophic. Noted even though Adrian set strict doc-F1 as the target.

## Bottom line
Cheapest/safest/highest-leverage next step = make the existing verify/merge pass less timid
(wider suspect net + continuation/layout signals, OCR text used LOCALLY at boundaries).
AOSM paradigm applied to our LLM pipeline; recall-safe; measurable offline first. Fine-tuning
is the only >0.9 lever but for us it is a VLM (self-hosting), not a quick win.

## Sources
- AOSM adaptive over-split-and-merge: sciencedirect.com/science/article/abs/pii/S0167865516301350
- Multi-page document processing / merge post-processing: llamaindex.ai/glossary/multi-page-document-processing
- Fine-tuned LLM PSS (TABME++, Mistral-7B, doc-F1 >0.9): arxiv.org/abs/2408.11981 (Heidenreich et al., Roots Automation)
- PSS history + MNDD operational metric: hunterheidenreich.com/posts/history-of-page-stream-segmentation
- DocSplit benchmark (AWS): arxiv.org/html/2602.15958v1
- CRF / Semi-Markov CRF sequence labeling: numberanalytics.com/blog/mastering-crf-techniques-for-sequence-modeling; arxiv.org/abs/2311.18028
- Multi-page document flow binary classification: researchgate.net/publication/274638619
