# Summarization

Summarization turns each reviewed sub-document of a record (a "row": a page range with a category,
a date, a date of injury and a review flag) into one delivered entry: an ALL CAPS header line, a
body, and the provenance and review flags a reviewer needs before the entry ships. It exists because
the deliverable is a medical-legal document: every entry has to say only what its own pages say, in
the house style the reviewers' own reports use, and has to be traceable to the model and prompt
that wrote it.

This page explains how one row is summarized and how the summarize job drives many rows. Job states
and the pause and resume machinery in general live in [Pipeline and jobs](pipeline-and-jobs.md); the
letter the entries end up in lives in [Deliverable layout](deliverable-layout.md).

## Where it runs

The engine is `backend/app/services/summarize_engine.py` `summarize_row()`. It is deliberately
database-free: callers hand it the row, the resolved prompt and the models, and it returns a plain
dictionary. Three callers use it, and they do not call it the same way.

| Caller | Code | Models | Audit | Record-level context |
| --- | --- | --- | --- | --- |
| Summarize job (worker) | `backend/app/worker/tasks.py` `_summarize_work()` | Body, title and audit models pinned on the Job | `SUMMARY_VERIFY` (on by default) | Stored OCR from the row or the page-text store, unreadable pages, embedded-review pages, standalone studies list |
| Single-row re-draft | `backend/app/api/documents.py` `resummarize()` | Body from the request or config; title and audit from live config | `SUMMARY_VERIFY` | The row's stored OCR, standalone studies list; no embedded-review pages, no unreadable-page seed |
| Bundle summarize | `backend/app/services/bundles.py` `bundle_summary_entries()` | Body from the request or config; title and audit from live config | Always off | The row's stored OCR only |

The rest of this page describes the worker path unless it says otherwise.

## How one row is summarized

```mermaid
flowchart TD
    A[Row + resolved prompt + models] --> Z{Category 18?<br/>illegible}
    Z -- yes --> ZZ[Fixed line 'Document was illegible.':<br/>no OCR, no model call]
    Z -- no --> B[Build system message:<br/>preamble for the category + category prompt]
    B --> C[Fingerprint preamble + prompt]
    C --> D[Append per-row blocks:<br/>document date / studies list / deposition page numbers]
    D --> E{Stored OCR text usable?<br/>not a deposition}
    E -- yes --> G[Use stored text]
    E -- no --> F[Re-OCR the pages with the reporting extractor]
    F --> G
    G --> H{Any text?}
    H -- no, some pages errored --> N[Notice-only entry:<br/>no model call]
    H -- no, pages read but blank --> X[EmptyExtractionError]
    H -- yes --> I[Body call: page images + OCR text + instruction<br/>429 fallback, one truncation re-ask]
    I --> J[Title call: first + last page images + OCR text]
    J --> K[House style on the body]
    K --> L{Audit enabled?}
    L -- yes --> M[Audit call + guards]
    L -- no --> O
    M --> O[Append trailing notices]
    O --> P[Output dict with provenance]
```

0. **Illegible.** A row a reviewer put in category 18 returns at once from `_illegible_output()`:
   the body is `ILLEGIBLE_SUMMARY` ("Document was illegible."), the header is the row's own title
   and date, and nothing is read, rendered or sent to a model. `noticeOnly` is false, because it is
   the reviewer's decision rather than a failure, so the job does not end needs_attention.
1. **Models.** The worker passes the three models stored on the Job (`job.model`,
   `job.title_model or job.model`, `job.audit_model or job.model`). A caller that omits one gets it
   from `Settings.model_for()` (`summarize_engine.py` `_resolved_models()`).
2. **Prompt.** The caller passes the category prompt it resolved from the database first. Without
   one, the engine falls back to `backend/app/services/prompts.py` (key `category_NN`, or
   `category_100` when the key is missing).
3. **System message.** `_build_system_message()` joins the shared preamble for this category and the
   category prompt, fingerprints that text, then appends the per-row blocks (see below).
4. **Source text.** `_row_source_text()` reuses stored OCR text or re-reads the pages (see
   [Source text](#source-text-stored-ocr-and-unreadable-pages)).
5. **Empty text.** If nothing was read and some pages errored, the row becomes a notice-only entry
   and no model is called. If the pages read cleanly and hold no words, the engine raises
   `EmptyExtractionError`.
6. **Body, title, house style, audit.** The three model calls and the deterministic passes described
   below.
7. **Trailing notices** are appended after the audit, to both the raw and the audited body.
8. **Output.** `summarize_row` returns the dictionary the worker persists as one `summaries` row via
   `backend/app/worker/tasks.py` `_build_summary()`, or one per visit when the row's pages record
   several dated visits (see [A page of several visits](#a-page-of-several-visits)).

## The system message

### The preamble is assembled per category

The shared rules are separate blocks in `summarize_engine.py`, assembled by `build_preamble()`. They
were split because a single shared block reached 4,927 characters, 81% of the system message for a
one-line laboratory summary, which was being instructed about depositions, range of motion and pain
scales that cannot apply to it (comment above `_FACTUALITY`).

Every category receives the factuality rules (`_FACTUALITY`), the point-scope rule
(`_C_POINT_SCOPE`), the no-billing-codes rule (`_C_CODES`), the bold-labels-only rule (`_F_BOLD`) and
the sentence-case rule (`_F_SENTENCE_CASE`). The rest depend on the category sets defined just above
`build_preamble()`:

| Category | Normal findings (`_C_NORMAL_FINDINGS`) | Verdict (`_C_VERDICT`) | Embedded review (`_C_EMBEDDED_REVIEW`) | Height/weight, pain, range of motion | Format |
| --- | --- | --- | --- | --- | --- |
| 1, 2, 5, 6 | yes | no | no | yes | one paragraph |
| 12, 13 | yes | no | yes | yes | one paragraph |
| 3, 14 | no | yes | no | no | one paragraph |
| 9 | no | no | no | no | groups of ten pages |
| 4, 7, 8, 10, 11, 15, 16, 17 | no | no | no | no | one paragraph |
| 100 | yes | yes | yes | no | one paragraph |
| Any id not in `_KNOWN_CATEGORIES` | yes | yes | yes | yes | one paragraph |

- **Default is include.** An id the code does not know (an admin can create one at any time) gets
  every block except the deposition format, so a new category is never silently under-instructed.
- **Category 100 is a known id with unknown content.** It takes the content blocks but not the three
  measurement blocks; whether it should get them is an open question the code records against
  issue #216.
- **3 and 5 are also told to mark separate visits** (`_F_SEPARATE_VISITS`, in the format
  section), because their pages can be a log of several dated visits - see
  [A page of several visits](#a-page-of-several-visits). No other category gets it, an unknown
  id included.
- **15, 16 and 17 get the minimal preamble on purpose.** A discharge summary (16) would be told by
  `_C_NORMAL_FINDINGS` to omit what is normal, which is most of its content; a job description (17)
  describes a job, not a patient. Their requirements are stated in their own prompts instead.

### The category prompt

The worker resolves one prompt per category before the thread pool starts, because the database
session is not thread-safe (`tasks.py` `_prompts_for_rows()`). Resolution is
`backend/app/services/catalog.py` `get_prompt()`:

1. The category's own row in the `prompts` table (an admin edit, which always wins).
2. The category's own code prompt in `prompts.py`.
3. The general (category 100) row in `prompts` - reached only by a category with no code prompt.
   Category 11 is the one such category, deliberately.
4. The general code prompt.

An admin-saved prompt row therefore shadows the code prompt for that category until it is reverted.
[How to change a summary prompt or rule](../how-to/change-a-summary-prompt-or-rule.md) covers this.

### Per-row blocks

After the fingerprint is taken, `_build_system_message()` appends up to three blocks that carry row
or record data:

| Block | Categories | What it tells the model |
| --- | --- | --- |
| `_document_date_block()` | 1, 2 (`_CURRENT_VISIT_CATEGORIES`) | The document's own date: summarize that encounter, not a recap of earlier visits. Omitted when the date is `-`. |
| `_standalone_studies_block()` | 12, 13 (`_EMBEDDED_REVIEW_CATEGORIES`) | The category-3 studies that are their own included rows elsewhere in the record, so an embedded records review does not restate them. Built from the full included row set, so a resumed run gives the same list. |
| `_deposition_pages_block()` | 9 | Whether the `Page N:` markers are the transcript's own printed numbers, or record positions that must not be cited. |

They go into the system message, not the user content, so the user payload order stays page images,
then OCR text, then the instruction (`_MULTIMODAL_INSTRUCTION`), which is sent last per Google's
context-first guidance.

## Source text: stored OCR and unreadable pages

- **Seeding.** Before the pool starts, `tasks.py` `_seed_row_text()` gives every row its
  `unreadable_pages` (pages whose `page_texts.extract_ok` is false) and, when the page-text store
  holds every page of the row and read each one cleanly, its `source_text`. A row with even one
  errored page is left to re-extract. The duplicate check may already have stored `source_text` on
  the row itself; that is reused too. See [OCR and page text](ocr-and-page-text.md).
- **Depositions never reuse stored text.** Category 9 is re-read with page markers, because page
  boundaries cannot be recovered from concatenated text.
- **Re-extraction wins.** When the engine re-reads pages it uses
  `backend/app/services/ocr.py` `extract_pages_with_report()`, and that run's errored pages replace
  the seeded list. A page that timed out earlier and reads now is not announced.
- **Only an errored page is announced.** A page that read cleanly and holds no words (a film, a
  photograph, a separator sheet) is silent.

### Notices are built in code

A model asked to describe a page it cannot read is the shape that invents content, so every notice
is a fixed sentence from `summarize_engine.py`:

| Case | Function | Result |
| --- | --- | --- |
| Nothing on the row could be read | `unreadable_notice()` via `_unreadable_output()` | The body IS the notice; every model field is NULL; `noticeOnly` is true; no DOI prefix |
| A reviewer marked the row illegible (category 18) | `_illegible_output()` | The body is `ILLEGIBLE_SUMMARY`; every model field is NULL; `noticeOnly` is false; no DOI prefix |
| Some pages could not be read | `partial_unreadable_notice()` | Sentence appended to the summary of the readable pages |
| An excluded records-review block belongs to this evaluation | `embedded_review_notice()` | Sentence appended naming the review's pages |

Trailing notices are appended by `_trailing_notices()` after the audit (the audit checks the body
against the source, and these sentences are not in the source) and after capitalisation (so the
transform never rewrites them). For a deposition the notice cites pages in transcript numbering,
falling back to record pages if any shifted number would be zero or less (`notice_pages()`). For a
condensed transcript it cites the printed numbers of the quarters that could not be read, falling
back to record pages when a whole sheet could not be read, since that sheet's numbers are unknown.

The embedded-review tag exists because an evaluation's embedded records review is split off as its
own row and excluded, which left its pages unmentioned. `tasks.py` `_seed_embedded_review_pages()`
walks back from each excluded row whose title matches a records-review pattern to the nearest
included row, and tags it only if that host is category 12 or 13.

## The three model calls

All three go through `backend/app/services/llm/__init__.py` `get_provider()`, which resolves the
backend for the `summarize` stage. See [Model providers](model-providers.md) and
[Model calls by stage](../reference/model-calls-by-stage.md).

| Call | Prompt | Input | Temperature | Output cap | On failure |
| --- | --- | --- | --- | --- | --- |
| Body | Preamble + category prompt + per-row blocks | Page images (when `SUMMARY_MULTIMODAL`), OCR text, instruction | `SUMMARY_TEMPERATURE` (0.0) | `SUMMARY_MAX_OUTPUT_TOKENS` (8192) | 429 fallback, one truncation re-ask; read in parts when the prompt is too long |
| Title | `TITLE_PROMPT` | First and last page images + OCR text (multimodal on) | 0.0 | `SUMMARY_MAX_OUTPUT_TOKENS` | Rejected answer falls back to the segmentation title; a too-long prompt is retried with the first part of the text |
| Audit | `VERIFY_PROMPT` (`summary_verify.py`) | Source, document date (categories 1 and 2 only), title, body | 0.0 | Argument, else `AUDIT_MAX_OUTPUT_TOKENS`, else the body cap | Fail-safe: body ships unaudited; not attempted for a body written in parts |

The three models are resolved once, when the Job is created (`backend/app/services/jobs.py`
`create_job()`), and stored on it, so a job resumed after a configuration change keeps the models it
started with. Defaults per backend are in [Configuration](../reference/configuration.md).

### Body

`_generate_body()` makes the call, then:

- **429 fallback.** If the call raises a rate-limit error (`errors.is_rate_limited()`, status 429)
  after the retry seam has spent its budget, and `SUMMARY_BODY_FALLBACK_MODEL` is set and differs
  from the body model, the body is asked once of the fallback model. It is a fallback, not a race:
  asking both at once would double load on a pool that is already refusing. The row records the
  model that answered in `model` and the one asked for in `bodyFallbackFrom`. With the shipped
  Gemini defaults the fallback equals the body model, so it only takes effect when the body model is
  changed.
- **Truncation re-ask.** If the reply hit the token cap, `_retry_if_truncated()` re-asks once at
  `SUMMARY_MAX_OUTPUT_TOKENS x SUMMARY_TRUNCATION_RETRY_MULTIPLIER` (a multiplier of 1 or less turns
  it off). A finished reply beats a truncated one; between two truncated replies the longer wins; an
  empty reply never wins; a retry that raises is swallowed because a partial body beats losing the
  row. It runs against whichever model answered - or, with summarize on vLLM and
  `VLLM_SUMMARY_TRUNCATION_MODEL` set, against that model instead (the base model while a summary
  adapter writes: measured 2026-10-20, 12 of the adapter's 13 cut-off exam bodies were loops, so
  re-asking the adapter only loops for longer). The row's `model` names whichever reply ships.
- **Deposition coverage check.** For category 9, `_log_incomplete_deposition()` logs a warning when
  the body cites less than half of the transcript pages it was given. It changes nothing. It exists
  to separate a model that stopped early (reported as finished) from token truncation.

A body that is still truncated after the re-ask is stored, and the row is flagged
`manual_check`.

#### A document too long for one request

`_body_or_parts()` wraps `_generate_body()`. When the model server refuses the prompt as longer than
its context window, the vLLM provider raises `ContextTooLongError` (`backend/app/errors.py`) instead
of a bare 400, and the body is read in parts. vLLM words that refusal two ways - the prompt plus the
reply do not fit ("maximum context length is ..."), or the prompt alone does not ("Input length (...)
exceeds model's maximum context length (...)") - and `_context_too_long()` recognises both:

1. `_text_parts()` cuts the OCR text at line breaks into consecutive pieces that each fill at most
   60% of the window, sized from the refused prompt's own token count when the server states it,
   else from an estimate of 0.5 tokens per character.
2. Each piece is summarized under the same system message, as OCR text alone with an instruction that
   it is part *i* of *n*. Page images are not sent: the document's images would not fit either.
3. The part summaries are summarized once more, under the same system message, into the one body
   that ships. Each call goes through `_generate_body()`, so the 429 fallback and the truncation
   re-ask still apply.

The title call is retried with the first piece when its prompt is refused the same way
(`_title_or_head()`), and the audit is not attempted for a body written in parts
(`_audit_fits()`): its source is the same text that did not fit. Every document that fits keeps its
single call. Measured 2026-10-23: our model's window is 131,072 tokens, and two whole hospital stays of
149 and 253 pages (about 280,000 and 1,060,000 characters of OCR text) failed their rows before this.
Gemini's window is far larger, so in practice only the vLLM path reaches it.

### Title

`TITLE_PROMPT` asks for one ALL CAPS line: `AUTHOR, CREDENTIALS. FACILITY. DOCUMENT TYPE.` The order
was measured against 812 of 813 human-written entries. Diagnostic studies are named by modality,
side, body part and contrast rather than by document class; a deposition is titled
`DEPOSITION OF <NAME>` with no author or facility; absent elements are left out with their
separator; no dates, page numbers or patient name.

The author is the name PRINTED on the page - the typed name under or beside the signature, a
physician field on a form, or the letterhead - never a transcription of a handwritten signature;
when no printed name can be read in full the author and credentials are left out. The call is sent
the row's first and last page images (letterhead and signature block) with the OCR text
(`_title_contents()`), because OCR cannot read a signature drawn over a typed name: reviewer
feedback on 2026-10-02 showed titles opening with OCR's fragment of such a name, and with the
letters OCR made of a cursive signature.

The facility is read the same way, from the image as well as the OCR, because a logo or stylised
letterhead is where OCR garbles most; a facility that cannot be read in full as real words is left
out rather than written as a fragment. Reviewer feedback on 2026-10-06 showed a title carrying
letters from an institute's logo beside an entry that named the institute correctly, and a
read-only measurement the next day put 38 of the 45 such one-off facility spellings on this call
(`_TITLE_IMAGE_INSTRUCTION` now names the facility as well as the author).

The 150-character limit in the prompt is a hint only: the call has no response schema and Gemini
does not enforce `maxLength`. `_usable_title()` is the enforcement:

- Every stored title is in **capitals**: the reply is upper-cased before anything else reads it, and
  so is the segmentation-title fallback. The prompt asks for capitals but nothing enforced it, and
  the audit's corrected title was the hole. On our model 39 of 189 audit-corrected titles came back
  in Title Case (Gemini 5 of 1,318), and a reviewer reported it on 2026-10-22: "titles should be
  capital always". A corrected title that differs from the current one only in case is no
  correction, so it stores nothing.
- A generated title that is empty or longer than `MAX_GENERATED_TITLE` (200) is **rejected** and the
  row's segmentation title is used instead. Rejected rather than truncated: a 620-character answer
  (seen once, deterministically, on one document) is the wrong kind of answer, and storing it
  overflowed the 512-character column and killed a 124-row job.
- The segmentation title fallback is **truncated** to `MAX_STORED_TITLE` (512 minus the length of the
  three decorations), because a long segmentation title is a real header that is merely long.
- An accepted title passes through `tidy_title()`. Its `without_address()` step drops pieces of the
  title that are unmistakably an address (street, suite, city before a state or ZIP, `CA` after a
  city, a phone number) and keeps everything else byte for byte. Its `tidy_author_and_facility()` step
  turns a `SURNAME, GIVEN NAMES, CREDENTIAL` author into `GIVEN NAMES SURNAME, CREDENTIAL`, and cuts a
  listed health system (`_HEALTH_SYSTEMS`, today Kaiser Permanente and Concentra) down to its name,
  dropping the branch, department or place after it, and drops a facility that is only the author's
  own name with credential and corporate words (`_without_own_practice()`, as in
  `JOHN SMITH, M.D. JOHN SMITH MD, INC.`). Its `tidy_report_type()` step gives a document type that
  names the PR-2 form its printed name, `PRIMARY TREATING PHYSICIAN'S PROGRESS REPORT (PR-2)`.
  All are deliberately narrow: an author without a credential, a second credential or degree in the
  middle slot, text after the system's name that names a document, a named practice, another
  person's corporation, and a facility followed by anything but its corporate words and the
  document type are all left as they are.
- A category-7 title (workers' compensation legal forms) then loses the state agency header
  (`without_wc_agency()`, through `category_title()`), keeping the Appeals Board when the header
  names it - except in an Application for Adjudication of Claim, where the Board goes too. Applied
  to the generated and the audited title of category 7 only, and again on the Summaries tab and in
  the export for a title stored before the rule; other categories keep the agency.

The stored title is decorated: `[ManualCheck] ` in front when the row flag is `x`,
` [Diagnostic Study]` after it for category 3, and ` (Pages S-E)` at the end. Exports strip all
three (`presentable_title()`).

### Audit

`backend/app/services/summary_verify.py` `verify_summary()` makes one structured call that checks the
title and body for faithfulness to the source and for six house rules (`_HOUSE_RULES`):

| Rule | Issue type | May delete content? |
| --- | --- | --- |
| 1 Height and weight removed (other vitals and a BMI diagnosis kept) | `vitals` | yes |
| 2 Pain: frequency, rating, location only | `pain_descriptor` | yes |
| 3 Capitalisation: re-case only; bold headings are required structure | `capitalization` | no (correction only) |
| 4 Range of motion carries a direction; reference ranges permitted | `range_of_motion` | no (correction only) |
| 5 Duplicate sentence removed, except Findings plus Impression | `duplicate_finding` | yes |
| 6 Previous visits removed (only when a document date is given) | `prior_visit` | yes |

Faithfulness issues are `unsupported`, `contradiction`, `date` and `laterality`. The reply schema is
`{fixed_text, fixed_title, issues: [{type, detail}]}`.

On our model (`summarize` on vLLM) the same reply is asked for as `{issues, corrected_title,
corrected_summary}` (`_reply_shape()`, switch `VLLM_AUDIT_ISSUES_FIRST`, on by default) and mapped
back to `fixed_text` and `fixed_title` before anything reads it. vLLM's grammar writes an object's
fields in the order the schema lists them, so the shared schema made the model open with
`fixed_text`, a name the prompt never explains, before it had listed an issue or had a field for
the title. It wrote the title there on 176 of about 300 audits on 2026-10-01, and each was
rejected by the title guard below (#348). Gemini orders its fields alphabetically, which is the
shared order, and gets the shared schema whatever the switch says.

Our model's audit also writes far longer issue lists than Gemini's. Over 1,400 completed audits
(2026-10-19) its issue details reached 22,204 characters (Gemini's maximum: 625) and its lists 80
issues (Gemini's: 41), all rendered on the reviewer's Summaries card. On our model the issues are
therefore stored through `readable_issues()`: at most 30, each detail cut at a word near 600
characters and marked `...`. Every guard reads the full list first, so an issue type named only
past the cap still counts. Switch `VLLM_AUDIT_BOUNDS` (off by default) writes the same two limits
into the reply's grammar, so a runaway stops at the bound instead of spending the token cap; every
audit failure in the saved worker log had used exactly the 8,192-token cap on a row of one to five
pages. Compiling that grammar against Qwen's tokenizer takes about 23 seconds once per vLLM start.

The audit is text-only, while the title call reads the first and last page images, so a name in a
logo can be right in the title and broken letters in the source. `VERIFY_PROMPT` forbids writing
such letters into the title: a name it writes must read as a name or as real words, and where the
source gives the name only as garbled letters the title's name stays. The rule is deliberately no
wider than that. Where the audit re-spelled a title's facility or author and a reviewer later
retyped the title, they kept the audit's spelling about as often as the original's (comment above
`VERIFY_PROMPT`).

The audit is fail-safe. `ok` is true only when the reply parsed; an empty body, a truncated reply
(checked before parsing) or any exception returns the originals with no issues and `ok` false. A
reviewer's Stop (`JobCancelled`) is re-raised, not swallowed.

The document date is passed only for categories 1 and 2, the same gate generation uses. Rule 6
switches itself on whenever a date is present, so passing it for every category armed the rule on
documents whose substance is earlier events (an evaluation's injury history, a deposition's
testimony), and its rewrites were accepted (comment in `_verified_outputs()`).

#### Guards on the audit's rewrite

A rewrite is a whole new body from one model call, so `_verified_outputs()` checks it before
accepting it:

```mermaid
flowchart TD
    A[Audit result] --> B{ok?}
    B -- no --> W[Warning logged, body ships unaudited<br/>verified = false]
    B -- yes --> C{Any issues?}
    C -- no --> V[verified = true, no rewrite stored]
    C -- yes --> D{Rewrite only restates the title?}
    D -- yes --> R[Keep raw body]
    D -- no --> K{Our model, VLLM_AUDIT_KEEPS_CONTENT on,<br/>unsupported or contradiction named,<br/>deleted numbers mostly on the pages?}
    K -- yes --> R
    K -- no --> E{Drops required bold headings?}
    E -- yes --> R
    E -- no --> F{Deposition and fewer paragraphs<br/>or page-range openers?}
    F -- yes --> R
    F -- no --> G[verified_text = DOI prefix + house style of rewrite]
    R --> T[Store issues, correct title independently]
    G --> T
```

- `_restates_the_title()` rejects a rewrite whose content words are all already in the title. A
  subset test needs no threshold and cannot reject a real correction.
- `_would_delete_content_on_our_model()` applies only when `summarize` runs on vLLM and
  `VLLM_AUDIT_KEEPS_CONTENT` is on (off by default). When the issues include `unsupported` or
  `contradiction`, the two the audit answers by deleting content, it compares the numbers the
  rewrite deletes with the source text: deleted numbers at least as often on the pages as not, or
  none deleted, and the draft ships. So our model's audit may still fix house style, a date or a
  left/right flip, and may still cut what no page carries, but not remove what the pages support.
  Measured on two sets of stored base-model summaries of the six records with human summaries
  (comment on the setting in `config.py`): higher word and number recall against the human
  summaries than shipping every rewrite, with no more numbers off the pages. The number check is
  what stops a runaway draft (a looping list of numbers no page carries) from shipping. Gemini
  never takes this branch.
- With `VLLM_AUDIT_IMAGE_CHECK` also on (off by default), a deleted number the OCR text lacks is
  not yet counted against the draft: when the text alone would let the rewrite win, the audit model
  is shown the row's page images (the body's own, `page_image_parts`) and asked, true or false per
  value, whether each of those numbers is written there (`_numbers_on_the_page()`); each one it
  sees counts as on the pages. Every number is listed beside a made-up near miss of it (its last
  digit moved by one, `_near_miss()`), and a reply that says a near miss is written confirms
  nothing. The audit reads the OCR text only, and OCR misses handwriting, so on a hand-filled form
  a number the body read correctly looks unsupported. Measured 2026-10-28 on 253 stored rewrites of
  two sets of large records with hand-filled pages: true for 74% of the deleted numbers on
  hand-filled rows and 81% on typed ones, against 7% and 2% of the near misses. Asked as a plain
  list of the deleted numbers, the same model had also said yes to 23-32% of made-up numbers. A
  failed check confirms nothing, so the text-only rule decides. One extra call, only for rows whose
  audit deletes numbers the OCR does not carry.
- `_drops_required_headings()` compares counts of bold spans, never their text, because renaming a
  heading is allowed. If every issue is correction-only (`capitalization`, `range_of_motion`), any
  lost heading rejects the rewrite. Otherwise a body with at least three headings that loses more
  than half is rejected, unless `unsupported` is among the issues: a fabrication fix is never
  blocked, because shipping invented content is the worse failure.
- `_drops_deposition_structure()` rejects fewer paragraphs or fewer `On pages N to M` openers.

When a guard fires the raw body ships and the issues are still stored, so the reviewer sees what
was flagged. The title correction is applied independently, through `_usable_title()` again, and is
stored in `verified_title` only when it differs.

What is displayed and exported is `edited` over `verified` over raw, for body and title separately
(`backend/app/models.py` `Summary.effective_text()`, `Summary.effective_title()`). The listing
derives three review flags from the stored columns: `verifyChanged` (a rewrite was applied),
`verifyKeptRaw` (issues found, rewrite rejected) and `verifyFailed` (an audit was requested and did
not complete).

## House style on the body

`_house_body()` runs two deterministic passes from `backend/app/services/house_style.py`, on the raw
body before the audit (so the audit reads what a reader will see) and on any accepted rewrite:

- `sentence_case_caps_runs()`: runs of two or more capitalised words become sentence case, or title
  case when the run contains an organisation word (`_PROPER_NOUN_MARKERS`) or ends in a comma and a
  state code ("San Diego, CA"); a lone capitalised word of four or more letters becomes title case.
  An acronym keeps its capitals wherever it stands: an allowlisted one (`_ACRONYMS`), a state code
  (`_STATE_CODES`), or any token carrying a digit ("L4-L5", "COVID-19") or no vowel ("PTSD", "LLC"),
  except titles and units such as "Dr." and "mg" (`_SHORT_WORDS`). It is never applied to a title,
  which is ALL CAPS by design.
- `one_paragraph()`: joins lines into one paragraph, drops list markers, and removes a label that
  introduces nothing: one followed only by another label or the end, carrying no digit, and not
  in front of a result heading (Findings, Impression, Conclusion, Interpretation, Results). So
  `**Objective Findings**:` straight into `**Range of Motion**:` goes, while a study heading such as
  `**MRI of the lumbar spine (05/15/2025):**` in a multi-study summary stays (#407). Skipped for
  depositions.

Both exist because the prompt rule alone missed: 22% of stored summaries still carried a run of
capitals after the prompt and the audit had both had a go, and 83 delivered summaries in 30 days
carried line breaks. The export applies `one_paragraph()` again to unedited machine text so older
stored summaries need no re-run.

## Date of injury prefix

- **Read once, at the end of segmentation.** `backend/app/services/summary_doi.py`
  `extract_injury_date()` reads each sub-document in isolation (its first 10 pages, from the page
  image rather than OCR) so a date cannot be copied from a neighbouring document, and the value is
  stored on the row. See [Segmentation](segmentation.md).
- **The row is the source of truth.** `summarize_row` never re-reads it; it uses
  `row["injury_date"]`, so a reviewer's correction on the review page reaches the summary.
- **Prefix.** `_doi_lead()` writes `**DOI**: <value>. ` with exactly one trailing space, or nothing
  for `-`. It is applied to both the raw and the audited body and never to a notice-only entry.
- **Value grammar** (`summary_doi.py` `_clean()`): `MM/DD/YY`; a cumulative-trauma period
  `CT MM/DD/YY-MM/DD/YY`; several items joined with ` & `; `-` for none.
- **Export restores it.** The Summaries tab moves the prefix into a chip, so a reviewer-edited body
  usually has none; the export re-prepends `doi_prefix(summary.text)` when the edited body carries no
  `**DOI**` (`backend/app/api/documents.py` `_export_title_and_text()`). `doi_prefix()` also reads the
  pre-2026-07-29 form `**DOI**:<dates>,`.

## Depositions

Category 9 differs at every step:

- **Format.** Groups of ten consecutive transcript pages, one paragraph per group, each opening
  with its page range (`_F_DEPOSITION`, and the category 9 prompt in `prompts.py`). The measured
  human convention is one page per paragraph. Groups of three were the owner's instruction
  (2026-08-06), made with that measurement in hand; groups of ten are the senior reviewer's
  instruction (2026-09-25) after reading delivered depositions. The comment above `_F_DEPOSITION`
  records both, so nobody "fixes" the size back to either convention.
- **Page numbers.** `backend/app/services/deposition_pages.py` `transcript_page_offset()` makes one
  `deposition`-stage model call over the first six pages to find the transcript's own printed page
  numbers (at least two pages must agree on one offset). The OCR markers are then labelled in
  transcript numbering and the model is told to cite them. A page that comes before the transcript's
  page 1 (a cover or caption page) has no printed number, so `ocr.page_marker()` marks it as front
  matter instead of labelling it `Page 0:` (#259). If no offset can be established the model
  is told to cite no page numbers. A truncated reply raises `TranscriptPagesUnreadableError`, which
  the worker treats as a permanent failure for that row.
- **Condensed transcripts.** Some depositions are printed four transcript pages to a sheet, in a 2x2
  grid read down the left column, then down the right, each quarter labelled "Page N". The offset
  read cannot handle that (it expects one printed number per scanned page), and a whole-sheet OCR
  returns the four pages out of order with no boundaries. `ocr.has_condensed_sheets()` looks for the
  grid on the row's first six pages, with no model call; when it finds one the offset read is
  skipped and `ocr.extract_condensed_transcript()` cuts each sheet on its grid lines and OCRs each
  quarter on its own. Each transcript page gets its own `Page N:` marker with the number printed on
  it (at least two quarters must agree on a sheet's numbering). A sheet without the grid, such as a
  cover or an exhibit, gets a marker with no number. `_deposition_pages_block()` then tells the model
  the markers are printed page numbers and to group by markers, not by page images. Reviewer report
  2026-10-08: a condensed deposition (11 sheets, 40 transcript pages) failed to summarize three
  times because the offset read was truncated every time.
- **No stored text, no one-paragraph pass, no export flattening.**
- **Audit guard** on paragraph and page-range counts, and the coverage warning described above.

## A page of several visits

A page can record several separate visits under one heading, each with its own date: a sheet of
daily therapy or acupuncture notes, for example. Identification keeps the page one row (a row is
whole pages, and two rows may not share a page), so it is split here, when the summaries are written.
The reviewers asked for each visit to be its own entry in the report (2026-10-15).

- **Which categories.** `_VISIT_LOG_CATEGORIES` in `summarize_engine.py`: 5 (therapy, chiropractic,
  acupuncture), where the case came up, and 3 (diagnostic studies), where the reviewers said it could.
  Only these are given the instruction, and only these have their body split; `writes_visits()` is
  the one predicate both read. An unknown category id does not get it (it is a format the code acts
  on, not a content rule).
- **The instruction.** `_F_SEPARATE_VISITS`, in the format section of the preamble: when the pages
  record several visits, begin each with a line `VISIT: MM/DD/YYYY` and write that visit's summary
  under it. A date the document only mentions (the injury, an earlier visit, a next appointment, a
  fax date) is not a visit, and a page of one visit gets no mark at all.
- **Which date.** A visit's date is the date of the visit, the day the encounter took place - not
  the date the sheet was signed, printed, faxed or received; on a hand-filled sheet that lists
  several dates, it is the date of the visit that sheet records (the reviewers, 2026-10-28: "It
  would be the date of the visit"). This instruction is the only place the summary call chooses an
  entry's date. A row of one visit keeps the date identification gave it (`d`, see
  [Segmentation](segmentation.md)); that prompt already asks for the visit or encounter date and
  excludes fax, print, re-send and injury dates, but does not name signature or received dates or
  multi-date hand-filled sheets. It is left unchanged because a segmentation prompt change needs a
  measurement first: the same call draws the boundaries, and a missed document is never summarized.
- **The split.** `split_visits()` cuts the body at the marks, before the one-paragraph pass (which
  would join the lines). Two marks naming one date are one visit; text before the first mark joins
  the first visit; a mark with no text is not a visit. Fewer than two visits left means no split, and
  `strip_visit_lines()` removes any stray mark. Visits are sorted by date. A mark without a year takes
  the year nearest the row's own date; a date that is no calendar date is kept as written and shows
  as Undated.
- **One summary per visit.** Each visit gets its own date, its own body (with the row's date of
  injury prefix and any unreadable-page notice) and its own audit call, which is given the visit's
  date. The title is the page's one title; its audit correction is taken from the first visit's
  audit for every visit. `summarize_row` returns the first visit at the top level and all of them
  under `visits`; every caller that stores or delivers summaries reads them through
  `visit_outputs()`.
- **Stored.** The worker writes one `summaries` row per visit, numbered by `Summary.part` (0 = first,
  and every single-visit row). `Summary.idx` stays the delivered order, by row then visit
  (`tasks.py` `_Placement`): a row of one visit keeps its row's position, so a record without a split
  page is numbered as before.
- **Delivered.** Each visit is its own entry in the Word letter, the linked PDF (both link to the
  page) and the Summaries tab, sorted by date with everything else. The bundle Word report lists each
  visit too. The diagnostics cover page lists the page once, as one document, with the first visit's
  date. Page accounting counts the page once: it reads rows, not summaries.
- **Re-draft** rewrites every visit of the row, and can find more or fewer visits than before
  (`documents.py` `_redraft_row()`); the Summaries tab re-reads its list afterwards.
- **Re-draft against a running summarize.** The route checks for a job before its model call and
  again after it (`_redraft_still_applies()`), under the document's row lock: a run that started
  meanwhile, or a summary that moved or was removed, answers 409 and writes nothing. `create_job`
  writes that row when it moves a summarize or identify job's status, so no run starts between the
  re-check and the commit. The lock is held for the write only, never for the model call.

## Record-level passes at export

Some rules need the whole record, so they run over the delivered entries when the Word letter and
the linked PDF are built (`documents.py` `_record_pass()`), not on the stored rows. The title passes
are one function, `summarize_engine.consistent_titles()`, which a bundle and the Summaries tab
(`documents.py` `_tab_titles()`, over the summaries an export ships) run too, so the tab names a
provider, a clinic and a PR-2 the way the letter will:

- **One spelling per provider.** `consistent_authors()` joins only spellings that can only be one
  person: same surname, suffix and credential; identical first names; an initial only when exactly
  one first name fits; different middle initials kept apart. A reviewer-edited title is never
  rewritten and its spelling wins its group.
- **One spelling per facility for each provider.** `consistent_facilities()` runs after the author
  pass and groups by author. It joins a facility spelling to the one more of that author's entries
  carry (or to a reviewer's) only when both have the same number of words and every differing word
  is a near spelling of the other: at least four letters, a difflib ratio of at least 0.80, and the
  same compass words. A garble that changes the word count is not joined, because by spelling alone
  it is as close to the right name as a second site of the same practice. The same reviewer lock
  applies.
- **A listed system's abbreviation.** `consistent_systems()` reads `CMC`, alone or followed by a
  site, as `CONCENTRA` (`_SYSTEM_ABBREVIATIONS`) - only in a record that names the system in full,
  because an abbreviation alone could be another place. A reviewer-typed title is kept, and counts
  as naming the system.
- **One name for a PR-2.** `consistent_report_types()` gives a category-1 document type of just
  `PROGRESS REPORT` the PR-2 name when the same author's category-1 entries in the record name the
  form; measured, 25 of 26 such titles sat on pages that print `PR-2`.
- **Same-visit folding.** `fold_record_entries()` makes one entry of the category-1 entries with the
  same author, credential and date string, and of a category-10 entry whose title names a Request for
  Authorization (`_RFA_TITLE`) by that author on that date - wherever it sits in the record. The
  reviewers' rule (2026-10-16): an RFA goes with the PR-2 of its date, since the PR-2 usually says
  what the RFA says, including when the two are not on neighbouring pages. Orders and prescriptions
  share category 10 and are not folded, and an RFA with no category-1 entry by its author that day
  stays its own entry. The entry is headed by a PR-2 when the visit has one (a title matching `PR-2`
  or `PROGRESS REPORT` that is not a work status slip), otherwise by the longest category-1 body. Its
  body is the longest category-1 body plus every section of the others whose text it does not
  already hold, so an RFA only ever adds. Measured on the live box (426 records, read-only,
  2026-10-16): 83 of 386 delivered RFAs fold, 23 of them from non-neighbouring pages; 78 that share
  a date with a category-1 entry stay apart because the author differs or the RFA's title names none.
- **Laboratory folding.** The same function makes one entry of every category-14 entry of one date,
  whoever wrote it (the reviewers, same day: "combine all the labs from the same date and have it
  come out as one summary"). It is headed by the first entry; when the entries' headings differ, its
  author and facility with `LABORATORY REPORT` as the document type (`_combined_lab_title()`), which
  is how the human-written reviews head a lab entry. Measured as above: 65 entries fold away on 46
  dates in 16 records.
- The rows and summaries are untouched by either fold, so the review page and the Summaries tab still
  show every document and a reviewer can still separate them.

Bundle summarize and the Summaries tab apply `consistent_titles()` but not folding.

## The resumable run

The summarize job (`tasks.py` `summarize_document()`) is resumable by design:

- **Rows** are the included review rows in order. Summaries are matched to rows by identity
  `(start, end, category)` (`_reconcile_summaries()`): a match is reused with its reviewer edits
  intact and only re-positioned; stale and duplicate summaries are deleted. A row of several
  visits keeps one summary per `part`, so a duplicate is the same row and the same part. A re-spanned or
  re-categorised row therefore gets a fresh summary.
- **Notice-only entries are retried.** A notice entry with no model and no reviewer edit is deleted
  and re-attempted on the next run (`_is_retryable_notice()`), so a transient OCR failure does not
  become permanent by having been delivered.
- **Each success is committed at once**, so a paused, cancelled or failed run keeps every finished
  row.
- **Rows run concurrently** on `PIPELINE_WORKERS` threads (5 by default).
- **Ending.** `_SummarizeRun.finish()` decides, in this order: give up (no row succeeded in this
  attempt and at least `SUMMARIZE_GIVEUP_AFTER_FAILURES` transient failures occurred) -> needs
  attention; any transient failure left or `SUMMARIZE_PAUSE_AFTER` consecutive transient failures ->
  pause and resume the same job after `SUMMARIZE_RESUME_DELAY` seconds, with no attempt ceiling;
  permanent failures or notice-only rows -> needs attention naming the rows; otherwise done. The
  give-up check must come before the pause check, or a model that refuses everything would pause and
  resume into the same refusal. Notice-only rows do not count as successes for the give-up rule.
- **"Re-summarize all from scratch"** sends `fresh`, and the route deletes every summary before
  enqueuing, discarding reviewer edits.

Job and document states are listed in [Job and document states](../reference/job-and-document-states.md).

## Provenance on each row

| Column | Meaning |
| --- | --- |
| `model` | The body model that answered (NULL on a notice-only row) |
| `title_model`, `audit_model` | The title and audit models; `audit_model` is set whenever the audit was requested |
| `backend` | The provider that served the calls, read from the provider object |
| `prompt_fingerprint` | First 12 hex characters of SHA-256 over the preamble and the category prompt, taken before per-row blocks (`backend/app/services/provenance.py` `summary_prompt_fingerprint()`) |
| `audit_fingerprint` | The same over `VERIFY_PROMPT` alone |
| `verified` | The audit ran and parsed, not merely that it was requested |
| `verify_issues` | The issue list, or NULL on a clean audit (never `[]`) |
| `manual_check` | The row flag was `x`, the body was still truncated, or the body came from the fallback model |
| `unreadable` | At least one page could not be read (with `model` NULL: nothing was summarized) |
| `embedded_review` | The body carries the embedded-review sentence |

The Job also carries a fingerprint of the whole prompt set and the build commit. Full column list:
[Data model](../reference/data-model.md).

## Decisions recorded in the code

| Decision | Reason recorded | Rejected alternative |
| --- | --- | --- |
| Temperature 0 for the body | Repeat runs identical; less fabrication (config comment on `summary_temperature`) | 0.8, which varied |
| Retry a truncated body once at a wider cap | A bigger cap moves every row and a thinking model spends extra budget on reasoning | Raising `SUMMARY_MAX_OUTPUT_TOKENS`; retrying a truncated audit was measured at 1.7x the time for a 29% yield |
| Title rejected, fallback truncated | A 620-character answer is not a title; a long segmentation title is | Truncating the model's answer |
| Notices built in code | A model describing unreadable pages invents content | A model-written notice |
| Guards compare counts | Renaming a heading is legitimate; losing one is not | Comparing heading text |
| Title-restatement test is a word subset | Needs no threshold and cannot reject a real correction | A length ratio |
| Deterministic capitalisation after generation | 22% of rows still had capital runs after prompt and audit | Louder prompt wording |
| No ICD/CPT stripper | The prompt rule already cut codes from 36% of rows to 1 in 124 | A regex that rewrites sentences |
| Height and weight only; BMI kept | BMI appears only as a numbered diagnosis in the human corpus | All vitals |
| Range-of-motion reference ranges allowed | Textbook values, not a claim about the patient | A strict no-inference rule |
| Embedded review gets a tag, not a summary | The senior reviewer asked for a tag | Including the review |
| Ten-page deposition groups | The senior reviewer's instruction (three pages before 2026-09-25, the owner's) | The measured one-page convention |

## Before you change it

- Edit a generation rule and its audit rule together, including the document-date gate
  (`summary_verify.py` comment above `_HOUSE_RULES`).
- A new category id must be added to the right set in `summarize_engine.py`, or it silently takes the
  every-block default; `test_every_catalog_category_is_registered_in_the_preamble_sets` enforces it.
  `_EMBEDDED_REVIEW_CATEGORIES` and `tasks.py` `_EMBEDDED_REVIEW_HOSTS` must agree
  (`test_the_two_embedded_review_category_sets_agree`).
- Keep the fingerprint before the per-row blocks, or identical prompts stop hashing alike.
- Keep notices and the DOI prefix out of the audit's view.
- `verified` means "ran". Keep `verify_issues` NULL on a clean audit.
- A notice-only row must have `model` NULL; the re-draft route rewrites `model` for that reason.
- Never apply `sentence_case_caps_runs()` to a title.
- `get_provider()` is correct only for the summarize stage; any other stage uses
  `provider_for_stage()`.
- `summarize_engine.py` sits at the linter's seven-parameter ceiling and its regular expressions are
  bounded to stay linear; keep both properties.

## Related pages

- [How to change a summary prompt or rule](../how-to/change-a-summary-prompt-or-rule.md)
- [How to troubleshoot a summary](../how-to/troubleshoot-a-summary.md)
- [Pipeline and jobs](pipeline-and-jobs.md)
- [Categorization](categorization.md)
- [Duplicate detection](duplicate-detection.md)
- [Exports and downloads](exports-and-downloads.md)
- [Deliverable layout](deliverable-layout.md)
- [Model calls by stage](../reference/model-calls-by-stage.md)
- [Configuration](../reference/configuration.md)

<!-- reviewed: 2026-09-30 -->
