# Plan: segmentation quality + eliminating repeated page work

Status: measured, nothing implemented. Written 2026-08-04. Baseline `main` `8aa79bf`.
Evidence: feature table over every reviewed boundary on the production DB (see section 1).

**Nothing in here ships without winning an A/B against the stored reviewer ground truth.** That is
Adrian's standing rule and the reason this plan leads with measurement rather than edits.

## 0. The problem in one paragraph

The model cuts documents into too many pieces. Reviewers merge them back: **96.1% of all boundary
corrections are merges**, splits are 1.6%, deletions zero. On the 15 reviewed documents the reviewer
merges **13.5 surplus rows per document**. Each surplus row also costs four model calls at summarize
time, so over-segmentation is simultaneously the largest reviewer-time cost and a direct compute cost.

## 1. What the data says (measured, full population)

Feature table: 1,448 interior boundaries across 15 reviewed documents, 202 spurious (**base rate
14.0%**). `segment_rows` = model output, `review_rows` = what the reviewer kept; a boundary is
"spurious" when no review row starts on that page. Text from every boundary page and its preceding
page, OCR'd once and cached.

### 1.1 Signals that a boundary is FAKE

| Signal                                                         | Fires | Precision | Recall | Lift  |
| -------------------------------------------------------------- | ----- | --------- | ------ | ----- |
| pagination "Page N of M", N>1, not fax-banner, not top-2 lines | 79    | 50.6%     | 19.8%  | 3.63x |
| same document date as previous row                             | 425   | 32.5%     | 68.3%  | 2.33x |
| NO patient/DOB/DOI field grid on the page                      | 280   | 28.6%     | 39.6%  | 2.05x |
| model row is exactly one page                                  | 594   | 20.0%     | 58.9%  | 1.44x |
| a date appears on both sides of the boundary                   | 723   | 18.1%     | 64.9%  | 1.30x |

### 1.2 Signals that a boundary is REAL

| Signal                                      | Fires | Precision | Lift  | Reading                           |
| ------------------------------------------- | ----- | --------- | ----- | --------------------------------- |
| letterhead / contact block in first 3 lines | 177   | 5.6%      | 0.40x | strong start evidence             |
| fax cover / transmittal header              | 123   | 8.1%      | 0.58x | a cover sheet IS its own document |
| field grid with >=2 labels                  | 694   | 8.6%      | 0.62x | strong start evidence             |
| "Page 1 of N"                               | 158   | 8.9%      | 0.64x | start evidence                    |
| signature block on the PREVIOUS page        | 724   | 9.1%      | 0.65x | previous document ended           |

### 1.3 Signals that are dead — do not spend prompt words on them

`ALL-CAPS title line` (11.5%), `same category as previous` (16.2%), `row <= 2 pages` (16.7%),
`certification/notary on previous page` (14.5%). All within noise of the 14.0% base rate.

### 1.4 Two findings that reverse earlier claims

- **Letterhead is NOT noise.** An n=200 probe read it as a coin flip; at n=1,448 it is one of the
  strongest REAL-boundary indicators. The segmentation prompt is RIGHT to list it. Do not remove it.
- **No deterministic rule generalises.** The best 3-way conjunction scored 90.2% precision, but
  **39 of its 41 hits came from one document**; across the other 14 it fired twice. Standalone
  auto-merge rules are a dead end and are dropped from this plan. The signals' value is as EVIDENCE
  GIVEN TO THE MODEL, not as rules that overrule it.

### 1.4b Ground truth is NOT uniform - use alocker's documents

Adrian (2026-08-04): **his own documents are not reliably reviewed for BOUNDARIES**; the correctly
reviewed set is alocker's. Split of the 16 documents that show boundary edits:

| Owner | Docs | Boundaries | Spurious | Pages |
| --- | --- | --- | --- | --- |
| alocker (user 3) | **10** | 812 | 100 | 2,572 |
| Adamf (user 4) | 3 | 440 | 64 | 1,336 |
| Adrian (user 2) | 3 | 274 | 46 | 855 |

**Score arms on alocker's 10 documents as the primary set** (Adamf's 3 as a secondary check). Adrian's
3 are excluded from scoring - a document whose boundaries were never carefully reviewed looks like a
perfect model score and silently flatters every arm. This also means every pooled number in section 1
carries some noise from those 274 boundaries; the DIRECTION of each signal held across users, but the
precise percentages should be re-derived on the alocker set before any arm is judged.

### 1.5 The verify pass is the weakest link

`verify_and_merge` spends **0.86 model calls per sub-document** (2,457 calls over 31 jobs) to produce
merge suggestions at **49.3% precision / 48.8% recall** - a coin flip. Its prompt explicitly discards
the best signal in the data: "Sharing a document type, date, or letterhead is NOT enough". Meanwhile
`suspect_indices` is not selective at all: every adjacent boundary is a candidate, capped at 200, with
"triggers" only reordering priority.

### 1.6 External benchmarks

- A direct competitor publishes **93.3% F1 page-weighted / 97.6% per-case median** over 682 cases and
  524,682 pages, and puts generic tools at **70-80% boundary F1**. Their stated view of where the
  boundary lives: "a new date of service, a different clinical event" - i.e. semantic, not layout.
  They also state correct segmentation is only defined relative to downstream goals; a packet has no
  "platonic decomposition". That explains why 63% of same-date/same-category boundaries are genuinely
  real.
- Published work on this exact task (page stream segmentation) frames it as **binary classification of
  adjacent page pairs** - "the decision for page p_i is made solely based on the pair (p_{i-1}, p_i)" -
  and reports **80% straight-through processing** vs 7.4% for an XGBoost baseline, and **0.81 vs 10.85
  pages** of human correction. Achieved with a fine-tuned 7B model (LoRA, 4-bit, one H100), NOT a
  frontier API.
- The same work found switching Tesseract -> commercial OCR cut blank pages from 2.27% to 0.38% and
  called it a critical bottleneck. This pipeline runs Tesseract.

## 2. Why pure page-pair is off the table (cost)

One call per adjacent page pair means ~N calls for an N-page record: a 217-page record goes from ~5
window calls to 216. At 500k pages/month that is ~500k calls against today's ~7k - roughly 70x. The
published result is affordable only because the model is a self-hosted fine-tuned 7B. At API prices
this is not viable, so this plan adopts a **hybrid**: code computes evidence, code decides WHICH
boundaries get a focused model call, and the model answers a narrow question with the evidence stated.

## 3. Workstream A - one OCR pass, reused everywhere (foundation)

**The same page is currently OCR'd up to four times** and rasterized several more:

| Site                                   | What it does                                                      |
| -------------------------------------- | ----------------------------------------------------------------- |
| `segment_engine.py:119`                | OCRs `row["start"]` per row during segmentation                   |
| `tasks.py:350`                         | OCRs `row.start` per row during classify                          |
| dedup (`tasks.py:441-446`)             | OCRs each row's full range, persists to `review_rows.source_text` |
| `summarize_engine.py:530-533`          | reuses `source_text` when present, else OCRs again                |
| `verify_pass.py:41` + `_boundary_text` | rasterizes boundary pages, optionally OCRs them                   |
| `summary_doi`                          | rasterizes the row's first pages for the vision call              |

### A.1 Add a per-page text store

New table `page_texts (document_id, page, text, ocr_engine, char_count, created_at)`, unique on
`(document_id, page)`. Populate ONCE per document, before segmentation. Every later stage reads from
it by page number.

Why a table and not the existing `source_text`: `source_text` is keyed to a ROW, and rows change
whenever the reviewer merges or splits. Page numbers never change, so a page-keyed store survives
every edit and is the only cache that can be reused across all stages and re-runs.

Cost note: text is small (~2-5 KB/page, so ~3 MB for a 600-page record). Page IMAGES are not cached -
at ~0.5-1 MB per page they would dominate storage; rasterization stays transient.

### A.2 Derive per-page features once

Alongside the text, store the deterministic features from section 1 per page (pagination n/total,
fax-banner flag, field-grid count, letterhead flag, signature flag, dates found, char count). These
are computed from text, cost nothing, and are needed by workstreams B and C. Recomputable at any time
from the stored text, so this is a cache, not a source of truth.

### A.3 Expected saving

Eliminates the segmentation per-row OCR, the classify per-row OCR, the summarize fallback OCR, and the
verify pass's text extraction. Dedup becomes an assembly of page texts rather than a fresh OCR of every
row. **This is a speed change with no effect on output, so it can ship without an A/B** - but it must
be verified byte-identical on a sample (section 6).

### A.4 Test OCR engine as a variable

Once text is stored with an `ocr_engine` column, running a second engine over the same pages and
comparing is cheap. The literature says this is upstream of everything. Measure blank-page rate and
character yield per engine before considering a switch.

## 4. Workstream B - prompt EXPERIMENTS (nothing here is a decided edit)

Every item below is an **arm to be run and scored**, not a change to make. The data in section 1 tells
us which words are worth testing; it does NOT tell us whether changing them improves boundaries. Only
the A/B does. **No prompt edit reaches `main` before its own arm has won on measured evidence.**

### B.0 Design rules

- **One variable per arm.** Bundling five reworded rules and observing an improvement tells us nothing
  about which one caused it - and if the bundle loses, a genuinely good change is discarded with it.
  Arms are combined only AFTER each has been scored alone (arm S6).
- **Control is re-run every time**, not read from history. Model behaviour drifts and the corpus is
  fixed; a stale control invites attributing drift to an arm.
- Each arm below states its **hypothesis** and, more importantly, **what result would kill it**. An arm
  with no falsifying outcome is not an experiment.
- The harness (`backend/scripts/eval/segmentation_boundary_ab.py`) already scores exact / over-split /
  under-split / misaligned and never writes to the DB. Keep that property.

### B.1 Segmentation prompt arms (`app/services/gemini.py`, `SEGMENTATION_PROMPT`)

| Arm | Change | Hypothesis | Killed if |
| --- | --- | --- | --- |
| **S0** | control, unmodified | baseline | - |
| **S1** | ADD the two measured start signals (patient/DOB/DOI field grid, "Page 1 of N") to the strong-start list. Letterhead stays - it measured as real start evidence. | Naming signals that actually discriminate lets the model require real evidence, reducing false splits | over-splits do not fall, or under-splits rise |
| **S2** | REMOVE the ALL-CAPS-title signal only | It measured dead (11.5% vs 14.0% base), so removing it should be neutral-to-positive and shortens the prompt | over-splits rise - meaning it was doing work the feature table could not see |
| **S3** | REMOVE the "page N of M continuation pages" line only | The model ignored it at 22 of 24 such boundaries, so it is inert; removing it should change nothing and frees the instruction to live in code | over-splits rise at paginated boundaries - meaning it was partially working after all |
| **S4** | REPLACE the flat date prohibition with the measured rate (shared date = weak continuation evidence, 2.33x lift; same date + same category still ~63% separate) | Giving the model the true rate beats a blanket ban it cannot calibrate against | under-splits rise - the model over-merges same-day batches |
| **S5** | NEUTRALISE the tiebreak ("when unsure, START A NEW RECORD") | The recall-first trade was set when reviewer time was cheap; at 13.5 merges/document it may be backwards | **under-splits rise at all.** This is the highest-risk arm - see B.3 |
| **S6** | combination of only the arms that won individually | isolated wins should compose | combined result is worse than the best single arm - interaction effects |

### B.2 Verify prompt arms (`app/services/verify_pass.py`)

| Arm | Change | Hypothesis | Killed if |
| --- | --- | --- | --- |
| **V0** | control | baseline (49.3% precision / 48.8% recall) | - |
| **V1** | Stop VETOING the date signal - weigh it instead of "sharing a date is NOT enough" | The veto discards the strongest single signal in the data | precision falls below control |
| **V2** | STATE the computed per-page evidence as fact in the prompt (pagination, field grid, letterhead, signature-on-previous, dates) instead of making the model re-derive it from pixels | The model is measurably bad at reading these off images; handing them over should raise precision | precision does not move - meaning the model was already reading them correctly |
| **V3** | DROP its "if unclear, answer NO" tiebreak | It is the second split bias, compounding segmentation's | under-splits rise |
| **V4** | winners combined | - | worse than best single arm |

### B.3 The tiebreak arms need their own gate

S5 and V3 are the only arms that can produce the LOSS condition in section 6 - trading merges for
hidden documents. They must be run and reported **separately**, never inside a bundle, and a win
requires under-splits to stay at zero, not merely "improve on average". If S5 removes 4 merges per
document but introduces one hidden document every three records, it has lost, and the number to report
to Adrian is the hidden-document count, not the net.

## 5. Workstream C - make the second opinion selective and per-page

### C.1 Triage instead of a wide net

`suspect_indices` currently nominates every boundary. Replace with a score from the section-1 features
and check only the top-K, where K is a tunable budget. Same or better recall for a fraction of the
0.86 calls/row.

### C.2 A per-page question, on the pages that matter

For each checked boundary, ask the narrow pairwise question the literature uses - "does page B continue
page A?" - with the stored page TEXT plus the computed features, not a fresh rasterization. Text-only
calls are far cheaper than vision calls, which is what makes selectivity affordable.

### C.3 Keep `auto=False` until an arm earns it

Auto-merge stays off. The verify pass produces suggestions; the reviewer decides. Auto-merge is
reconsidered only for a rule whose precision is high AND stable ACROSS DOCUMENTS - the check that
killed the 90.2% rule in section 1.4.

## 6. How every arm is scored

Ground truth is free and already collected: `segment_rows` vs `review_rows`. **Primary set is
alocker's 10 documents (812 boundaries, 100 spurious); Adamf's 3 as a secondary check. Adrian's 3
are excluded - see 1.4b.** No new human review needed.

Report in REVIEWER ACTIONS, not F1, because that is the decision Adrian faces:

| Measure                                                   | Today         |
| --------------------------------------------------------- | ------------- |
| Surplus rows the reviewer merges, per document            | **13.5**      |
| Boundaries the reviewer must split (model joined wrongly) | ~0            |
| Verify-pass model calls per sub-document                  | 0.86          |
| Merge-suggestion precision / recall                       | 49.3% / 48.8% |

**An arm that reduces merges but introduces splits has LOST.** A document hidden inside another is
worse than a surplus row, and the current prompt's tiebreak exists to protect against exactly that.

Per-document reporting is mandatory. A pooled number hid the fact that one file carried 39 of 41 hits.

For workstream A specifically, the test is different: OCR text for a sample of pages must be identical
whether read from the store or extracted fresh, and the row output of a full segment+classify+dedup run
must be unchanged.

## 6b. Workstream D - parallelisation: ALREADY OWNED BY THE OTHER SESSION

**Do not implement this here.** Checked at `405791c` (2026-08-04): the other session has done it, and
more rigorously than the draft this section used to contain. Evidence in `docker-compose.yml`:

- `PIPELINE_WORKERS` IS now passed through (this section previously claimed it was not - stale).
- Measured: 7.8 accepted calls/min against `VERTEX_MAX_RPM=20`, i.e. the bucket at ~39% utilisation;
  two concurrent chains at ~15.4s per call.
- Saturation derived by Little's Law: `20/60 * 15.4 = ~5` concurrent rows fills a 20 rpm budget
  without raising it.
- Documented hazard: `rate_limit.acquire()` abandons its wait after `MAX_ACQUIRE_WAIT_S` (300s) and
  proceeds anyway, so pushing far past saturation means queued callers stop being rate limited at all.
- `OMP_THREAD_LIMIT=1` is what stops concurrent Tesseract deadlocking - relevant to workstream A,
  which adds a parallel OCR pass.
- Shipped: adaptive pacing (#75) replacing the fixed cap, and the provider seam (#73/#74/#76).

**Constraint this imposes on workstream A:** the one-time OCR pass is CPU work on the same box and the
population step must respect `OMP_THREAD_LIMIT=1` and not fight the pacing work. Keep its concurrency
its own setting, defaulted conservatively.

## 7. Sequence

1. **A.1 + A.2** - page text store and per-page features. No model calls, output-neutral, and it is what
   makes arm V2 (stating evidence in the prompt) possible at all.
2. **C.1** - triage on stored features. Cuts verify calls with no prompt change.
3. **RUN the arms in section 4.** S0-S5 and V0-V3 individually, then S6/V4 from the winners only.
   Nothing is edited on `main` at this stage - the harness swaps prompt text per arm.
4. **Report per-document**, then decide with Adrian which arms (if any) become real edits.
5. **Only then** edit `SEGMENTATION_PROMPT` / the verify prompt, one winning arm at a time.
6. **C.2** - pairwise text-only verification as a further arm.
7. **A.4** - OCR engine comparison, once the store makes it cheap.

Steps 1-3 change no production behaviour. The first behavioural change to segmentation output is
step 5, and it happens only for arms that won.

## 8. Cost and budget gates

- Workstreams A and C.1: **no model calls at all.**
- Section 4 arms: the harness costs roughly one call per window per document per arm. At 6 documents
  that is ~25 calls/arm; **10 individual arms + 2 combination arms + control re-runs is roughly
  300-400 Vertex calls total.** Small, but **not free - needs Adrian's explicit go before any arm runs.**
  Use `backend/scripts/eval/vertex_stats.py --reset` before each arm so rejections are visible.
- C.2: one text-only call per triaged boundary. At K=20 per document over 15 documents, ~300 calls/arm.
- Full page-pair (section 2): NOT proposed. ~70x call volume; viable only self-hosted.

### 8.1 Measured runtime

78 windows across all 16 documents (mean 4.9, range 1-14); alocker's 10 documents are ~42 of them.
Per-window latency ~40-45s, derived from a 217-page job's `segmenting` stage. Window calls use
`genai_model` (flash), NOT 2.5-pro - and flash measured clean to concurrency 4, so **429s are not the
constraint here**; 78 calls at `VERTEX_MAX_RPM=20` need only ~4 minutes of rate budget.

| Scope | Per arm | Full set (~12 arm-runs) |
| --- | --- | --- |
| alocker only, serial | ~30 min | ~6 h |
| alocker only, 4 documents in parallel | **~8 min** | **~1.6 h** |
| alocker + Adamf, 4 in parallel | ~12 min | ~2.4 h |

Parallelise ACROSS documents, never across windows within a document - the harness serialises windows
deliberately so they cannot contend, and that property is what keeps each document's measurement clean.

**Run after office hours** (Adrian, 2026-08-04): the arms share the same 20 RPM bucket as production
work, so both slow down if they overlap.

Scoring is free and repeatable: ground truth is already in the database.

## 9. Open decisions for Adrian

1. Does the page-text store go in Postgres (simple, queryable, grows the DB) or on disk beside the PDF
   (cheap, needs its own lifecycle)? Recommendation: Postgres, since the DB already stores
   `source_text` OCR output so it is not a new class of data at rest.
2. Vertex budget for the prompt arms.
3. Is a wrong join (a needed split) acceptable at ANY rate, or is the bar zero? This sets whether
   auto-merge is ever reachable, and it is a business call, not a technical one.

## 10. Out of scope

- Self-hosted fine-tuned segmentation model (the 13x result in section 1.6). Real, but a GPU project.
- Changing `taxonomy.py` or category assignment.
- Backfilling already-segmented documents.
- `VERTEX_MAX_RPM` / worker concurrency - owned by the other session.
