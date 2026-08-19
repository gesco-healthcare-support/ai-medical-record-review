# Categorization (stage 2): assigning a category to each sub-document

> **Last checked against the code 2026-08-18.** Re-verified against
> `backend/app/services/classification.py` after #119 and #122 changed the rules stage: the
> `_PAPERWORK_ABOUT_A_DOCUMENT` exception and the `_EVALUATOR_YIELDS_TO` set are now described in
> stage 1. An earlier pass on 2026-08-12 fixed the code paths (they pointed into the pre-rewrite
> `mrr_ai/` tree, now [`legacy/`](../../legacy/README.md)), the classifier model, and category `6`.
> The three-stage shape, the vote-fusion table and the local-embedding PHI property have been
> accurate throughout.

> Explanation doc. The category is a column on `segment_rows` / `review_rows` in Postgres, written
> inline during segmentation; the [page-map CSV](../reference/csv-contract.md) it used to fill is now
> an export shape only.

Categorization sets each row's `category`: a string id that both labels the sub-document and
**selects its summarization prompt** ([summarization.md](summarization.md)). The auto-assignable ids
are `1`-`5`, `7`-`14`, and `100` (catch-all). Id `6` is a real, active category that is **manually
selectable in the review editor but never auto-assigned** (see [Taxonomy](#the-taxonomy)).

## The B5 cascade (current)

Code: [../../backend/app/services/classification.py](../../backend/app/services/classification.py),
[../../backend/app/services/taxonomy.py](../../backend/app/services/taxonomy.py). Entry point:
`classify(title, page_text=None)`.

Each sub-document is classified by a **three-stage cascade** that escalates only as far as it
must, and cross-checks its two statistical stages so a weak or conflicting result is flagged
for a human rather than silently dumped into the catch-all:

1. **Rules** (`match_rules`) - ordered high-precision regex on the title, short-circuiting the
   cascade with no model cost. Ordering encodes precedence: specific categories precede the ones
   they are confused with - supplemental QME/AME (`12`) before QME/AME (`13`);
   PT/chiropractic/acupuncture (`5`) before the generic progress (`1`) and comprehensive (`2`)
   rules. A rule hit is always `confidence="high"`, `needs_review=False`.

   **First-match-wins holds only for a non-administrative title.** A separate `_ADMIN_RULES` set
   matches the paperwork that wraps a record - routing slips, cover letters, correspondence and
   email, declarations, proofs of service, records requests and indexes, evaluation notices - and
   resolves _document beats wrapper_ in two steps:

   - If the title names a document at all (`_DOCUMENT_NOUN`: report, transcript, notes, study,
     scan, imaging, x-ray, chart, questionnaire, results), the administrative rules **stand down**
     and the normal cascade answers. "Cover Letter - Psychological Evaluation Report"
     is a report, and falls through to the embedding + LLM stages when no keyword rule fits,
     rather than being buried in General.

     **One shape is excepted** (`_PAPERWORK_ABOUT_A_DOCUMENT`, added #122): when the title reads
     `(declaration|proof|certificate) of (service|mailing) of ...`, the document noun names what
     the paperwork is _about_, not what the pages _are_. "AME or QME Declaration of Service of
     Medical - Legal Report" is a mailing receipt, so the noun does not stand the rule down.
     Anchored on the trailing `of` and nothing wider, because a real evaluation genuinely does
     travel with a service page - "QME Report - Proof of Service" is category `13` and stays so.
     Word order is the whole distinction: paperwork-first with the document as its grammatical
     object, versus document-first with the paperwork attached.

   - Otherwise the administrative match holds, and the result is the first document-type rule that
     also fired, else `100`. **Category `13` does not count there**
     (`_EVALUATOR_MENTION`): it fires on a mere mention of the evaluator, which correspondence
     _about_ an AME contains too. Any other rule does count - "Transmittal Letter - MRI Lumbar
     Spine" is an MRI.

   **A named document type also outranks a bare evaluator mention** (`_EVALUATOR_YIELDS_TO`,
   added #122), with no administrative rule in play. Rule `13` sits second and first-match-wins,
   so "AME Deposition Transcript" - a transcript of the AME being _questioned_ - used to answer
   `13` and be summarized with the evaluation prompt. Imaging (`3`), operative (`8`), deposition
   (`9`) and laboratory (`14`) now win instead. Deliberately an explicit set rather than moving
   rule `13` down the list: rules `1` and `2` sit ahead of those four, so any position that lets a
   deposition win also lets "progress report" and "permanent and stationary" win, and those
   describe what an evaluation _concludes_ - "AME Permanent and Stationary Report" is `13`, not `2`.

   Both #122 additions were **decided in-house and are not confirmed with eData**; each is
   one line to reverse.

   So an administrative title can return `100` even though a document-type rule matched. That is
   deliberate, and it is the one path where the cascade's default is load-bearing: `100` is
   unchecked for summarization by default, so a false administrative match silently drops a real
   document from the delivered summary.

2. **Embedding** (`embed_classify`) - the local `all-MiniLM-L6-v2` sentence-transformer encodes
   the text and each category's corpus (name + description + example titles), and picks the
   nearest category by cosine similarity. Runs locally, so **no PHI leaves the host** for this
   stage. `torch`/`sentence-transformers` is imported lazily, so importing the module does not
   pull in `torch`.
3. **LLM** (`llm_classify`) - Gemini on `settings.classify_model` (default
   **`gemini-2.5-flash-lite`**, the cheapest tier: this is a short, structured enum task) with
   **constrained-enum output** (`response_mime_type="text/x.enum"`, `response_schema` enumerating
   the ids the live catalog marks auto-assignable), so it _cannot_ emit an invalid category.
   `temperature=0`. The model is overridable per call so an A/B can compare tiers on identical
   inputs. Its prompt also states the administrative-document rule directly, so the LLM stage
   agrees with `_ADMIN_RULES` rather than fighting it.

### How the votes are fused

`classify` combines the stages defensively - any model failure degrades to a flagged best
guess, never a 500:

| Situation                                   | Result `category` | `confidence` | `needs_review` | `method`                      |
| ------------------------------------------- | ----------------- | ------------ | -------------- | ----------------------------- |
| A rule matches                              | the rule's id     | high         | no             | `rules`                       |
| Administrative title, no document-type rule | `100`             | high         | **no**         | `rules`                       |
| No rule, and no usable text                 | `100`             | low          | **yes**        | `empty`                       |
| Embedding and LLM **agree**                 | that id           | high         | no             | `llm+embedding`               |
| They **disagree**                           | the LLM's id      | low          | **yes**        | `llm-disagree`                |
| Only one stage produced an answer           | that id           | low          | **yes**        | `embedding-only` / `llm-only` |
| Both stages failed                          | `100`             | low          | **yes**        | `no-signal`                   |

The result is a `Classification(category, confidence, method, needs_review)` dataclass; `method`
records which path decided.

Note row 2. It is the only branch that assigns the catch-all at **high confidence without flagging
for review**, because it is a rule hit like any other - and `100` is unchecked for summarization by
default. A wrong administrative match therefore drops a document from the delivered summary with
nothing surfaced to the reviewer, which is why `_DOCUMENT_NOUN` exists and why the rule set is kept
narrow.

### Title-first, with OCR escalation

In [`segment_engine._categorize`](../../backend/app/services/segment_engine.py) the cascade is
called **on the title alone first** (cheap). Only if that returns `needs_review` does it escalate:
read the sub-document's first page and re-run `classify(title, page_text=...)`. That page comes from
the caller's `page_text_fn` where one is supplied - the worker passes a reader over the `page_texts`
store, so the page is not OCR'd a second time - and is OCR'd here only when it is not. The row's
manual-review flag (`row["flag"]`) is `"x"` when **either** the classifier wants review **or**
segmentation already flagged the document.

Rows are categorized on a small thread pool (`CLASSIFY_WORKERS`). Each worker owns its row, and
`classify()` opens its own short-lived session for catalog reads, so the stage is thread-safe
without an app context.

## The taxonomy

`taxonomy.py` holds `CATEGORIES`: for each id, a human name, a description, and example
document-type titles. The `.corpus` property joins these into the text that the embedding and
LLM stages compare against (`classification._corpus` mirrors it for the DB-backed catalog dicts).
Its example titles **mirror the hand-authored business taxonomy in
[`groups.py`](../../legacy/mrr_ai/groups.py) in full** - every title there appears under its
category - enriched with a per-category name + description. It is not yet the curated "B6" taxonomy:

- **Category `6` has no `taxonomy.py` entry**, because it was empty in `groups.py` (no titles) and
  so has nothing to embed against. It is **not** absent from the system: `seed_catalog._ID_SIX`
  adds it to the catalog as "Daily / SOAP notes", `active=True` and `auto_assign=False` - a real,
  active category that a reviewer may select but the classifier never assigns. Its `category_06`
  summarization prompt exists and is used when a reviewer picks it.
- Some group-5 entries are **section headers** ("History of Present Illness", "Physical
  Examination", "Diagnosis") rather than document types. They are **included to mirror
  `groups.py`** (business decision); because they appear in nearly every report they can bias
  the category-5 embedding vote, which the embedding-vs-LLM cross-check is relied on to dampen.
  Refining this is B6.
- `ALLOWED_IDS` and `DEFAULT_ID = "100"` are derived here; `taxonomy.py` is the source of
  truth for a **fresh** database.

### Runtime source: the editable catalog (DB-backed)

As of ADR [0006](../decisions/0006-editable-catalog-admin.md), the live category set is **not**
`taxonomy.py` at runtime - it is the `Category` DB table, seeded from `taxonomy.py` on first
boot and edited from the admin console. `classification.py` reads it lazily through
[`backend/app/services/catalog.py`](../../backend/app/services/catalog.py)
(`get_categories(auto_assign=True)`), keyed on a catalog revision, and rebuilds its catalog text +
embedding matrix when an admin edit bumps that revision (`reset_catalog_cache()` runs at worker
startup). With no reachable DB it falls back to the `taxonomy.py` constants, so the cascade stays
unit-testable. The classifier's assignable set is the `auto_assign=True` categories; the
review editor additionally offers active categories with `auto_assign=False` (this is now how
`6` is modeled: a real, active, editor-only category rather than an omission).

## The predecessor (superseded, now in `legacy/`)

The original categorizer was a single-stage `difflib` fuzzy match:
[`categorization.categorize_documents`](../../legacy/mrr_ai/services/categorization.py) compared the
normalized title to every doctype name in [`groups.py`](../../legacy/mrr_ai/groups.py) and assigned
the best match above a 0.65 ratio, falling back to `100`. It mislabeled noisy titles to `100`
and was confused by the section-name pollution above.

It was dead on the automatic path from the B5 merge onward, and both files now sit in
[`legacy/mrr_ai/`](../../legacy/README.md) along with the rest of the pre-rewrite app. Nothing in
`backend/` imports them.

## Known limitations

- Categories are only as good as the title (plus one escalation page); a misleading title can
  still mislabel a document, which is why low-confidence results set the manual-review flag.
- **A rule hit is never flagged**, including the administrative branch that returns `100`. The
  manual-review flag protects the statistical stages, not the deterministic one, so a rule that
  fires wrongly is invisible until a reviewer notices the row. This is the failure mode worth
  measuring before widening `_ADMIN_RULES` or `_RULES`.
- The catalog is uncurated (B6). Curating it - and resolving the `6`/section-name issues - is
  the planned next step (see [../plans/2026-06-16-b5-b6-categorization.md](../plans/2026-06-16-b5-b6-categorization.md)).
- The LLM stage transmits the title/first-page text to Gemini (PHI); the embedding stage does
  not. See [../architecture.md](../architecture.md) for the PHI-flow summary.

## Related

- Produced during: [segmentation.md](segmentation.md) (**[LEGACY]** - see
  `backend/app/services/segment_engine.py`)
- Consumed by (prompt selection): [summarization.md](summarization.md) (**[LEGACY]** - see
  `backend/app/services/summarize_engine.py`)
- Category column, as an export shape: [../reference/csv-contract.md](../reference/csv-contract.md)
  (**[LEGACY]**)
- Add a category: [../how-to/add-a-category.md](../how-to/add-a-category.md)
