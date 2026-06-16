# Categorization (stage 2): assigning a category to each sub-document

> Explanation doc. Output column: [../reference/csv-contract.md](../reference/csv-contract.md);
> it is produced inline during [segmentation.md](segmentation.md) on the automatic path.

Categorization fills column 3 (`category`) of the page-map CSV: a string id that both labels
the sub-document and **selects its summarization prompt** ([summarization.md](summarization.md)).
The valid ids are `1`-`5`, `7`-`14`, and `100` (catch-all); `6` is intentionally absent
(see [Taxonomy](#the-taxonomy)).

## The B5 cascade (current)

Code: [../../mrr_ai/services/classification.py](../../mrr_ai/services/classification.py),
[../../mrr_ai/taxonomy.py](../../mrr_ai/taxonomy.py). Entry point: `classify(title, page_text=None)`.

Each sub-document is classified by a **three-stage cascade** that escalates only as far as it
must, and cross-checks its two statistical stages so a weak or conflicting result is flagged
for a human rather than silently dumped into the catch-all:

1. **Rules** (`match_rules`) - ordered high-precision regex on the title. The **first** match
   wins and short-circuits the cascade (no model cost). Ordering encodes precedence: specific
   categories precede the ones they are confused with - supplemental QME/AME (`12`) before
   QME/AME (`13`); PT/chiropractic/acupuncture (`5`) before the generic progress (`1`) and
   comprehensive (`2`) rules. A rule hit is always `confidence="high"`, `needs_review=False`.
2. **Embedding** (`embed_classify`) - the local `all-MiniLM-L6-v2` sentence-transformer encodes
   the text and each category's corpus (name + description + example titles), and picks the
   nearest category by cosine similarity. Runs locally, so **no PHI leaves the host** for this
   stage. `torch`/`sentence-transformers` is imported lazily, so importing the module does not
   pull in `torch`.
3. **LLM** (`llm_classify`) - Gemini (`gemini-flash-latest`) with **constrained-enum output**
   (`response_mime_type="text/x.enum"`, `response_schema` enumerating the allowed ids), so it
   *cannot* emit an invalid category. `temperature=0`.

### How the votes are fused

`classify` combines the stages defensively - any model failure degrades to a flagged best
guess, never a 500:

| Situation | Result `category` | `confidence` | `needs_review` |
|-----------|-------------------|--------------|----------------|
| A rule matches | the rule's id | high | no |
| No rule, and no usable text | `100` | low | **yes** |
| Embedding and LLM **agree** | that id | high | no |
| They **disagree** | the LLM's id | low | **yes** |
| Only one stage produced an answer | that id | low | **yes** |
| Both stages failed | `100` | low | **yes** |

The result is a `Classification(category, confidence, method, needs_review)` dataclass; `method`
records which path decided (e.g. `"rules"`, `"llm+embedding"`, `"llm-disagree"`).

### Title-first, with OCR escalation

In [`segmentation._format_segment_line`](../../mrr_ai/blueprints/segmentation.py) the cascade
is called **on the title alone first** (cheap). Only if that returns `needs_review` does it
escalate: OCR the sub-document's first page and re-run `classify(title, page_text=...)`. The
final manual-review flag (CSV column 6) is `"x"` when **either** the classifier wants review
**or** Gemini's segmentation flagged the document (`m == "x"`).

## The taxonomy

`taxonomy.py` holds `CATEGORIES`: for each id, a human name, a description, and example
document-type titles. The `.corpus` property joins these into the text that the embedding and
LLM stages compare against. Its example titles **mirror the hand-authored business taxonomy in
[`groups.py`](../../mrr_ai/groups.py) in full** - every title there appears under its category -
enriched with a per-category name + description. It is not yet the curated "B6" taxonomy:

- **Category `6` is omitted** because it is empty in `groups.py` (no titles) and was never
  assignable. (Note: a `category_06` summarization prompt still exists for the manual path -
  see [summarization.md](summarization.md).)
- Some group-5 entries are **section headers** ("History of Present Illness", "Physical
  Examination", "Diagnosis") rather than document types. They are **included to mirror
  `groups.py`** (business decision); because they appear in nearly every report they can bias
  the category-5 embedding vote, which the embedding-vs-LLM cross-check is relied on to dampen.
  Refining this is B6.
- `ALLOWED_IDS` (the enum given to Gemini) and `DEFAULT_ID = "100"` are derived here, so the
  catalog is the single source of truth for what a valid category is.

## The predecessor (superseded, still in the tree)

The original categorizer was a single-stage `difflib` fuzzy match:
[`categorization.categorize_documents`](../../mrr_ai/services/categorization.py) compared the
normalized title to every doctype name in [`groups.py`](../../mrr_ai/groups.py) and assigned
the best match above a 0.65 ratio, falling back to `100`. It mislabeled noisy titles to `100`
and was confused by the section-name pollution above.

As of the B5 merge it is **no longer called** by any route or service (`/getPages` imports
`classify`, not `categorize_documents`). `categorization.py` and `groups.py` remain in the tree
but are dead on the automatic path - candidates for removal once nothing references them.

## Known limitations

- Categories are only as good as the title (plus one escalation page); a misleading title can
  still mislabel a document, which is why low-confidence results set the manual-review flag.
- The catalog is uncurated (B6). Curating it - and resolving the `6`/section-name issues - is
  the planned next step (see [../plans/2026-06-16-b5-b6-categorization.md](../plans/2026-06-16-b5-b6-categorization.md)).
- The LLM stage transmits the title/first-page text to Gemini (PHI); the embedding stage does
  not. See [../architecture.md](../architecture.md) for the PHI-flow summary.

## Related

- Produced during: [segmentation.md](segmentation.md)
- Consumed by (prompt selection): [summarization.md](summarization.md)
- Category column: [../reference/csv-contract.md](../reference/csv-contract.md)
- Add a category: [../how-to/add-a-category.md](../how-to/add-a-category.md)
