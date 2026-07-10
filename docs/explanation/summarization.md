# Summarization and export (stages 3-4): CSV + PDF to the MRR Word doc

> Explanation doc. Input: the [page-map CSV](../reference/csv-contract.md) from
> [segmentation.md](segmentation.md) + [categorization.md](categorization.md). For endpoints
> see [../reference/api-routes.md](../reference/api-routes.md).

Summarization is where the page-map CSV finally meets the PDF: for each row, the relevant pages
are OCR'd and sent to OpenAI with a **category-specific prompt**, producing one summary per
sub-document. Export then assembles those summaries, in date order, into the **Medical Record
Review (MRR) Word document** that is the product's deliverable.

## Stage 3 - `/summarize` (CSV-driven)

Code: [../../mrr_ai/blueprints/summarize.py](../../mrr_ai/blueprints/summarize.py),
[../../mrr_ai/prompts.py](../../mrr_ai/prompts.py),
[../../mrr_ai/services/ocr.py](../../mrr_ai/services/ocr.py).

`/summarize` reads the CSV at `state.txt_filepath` line by line. The `model` (an OpenAI model
name) comes from the request body. For each row:

1. **Parse + validate.** The row must have **exactly 6 columns** or it is skipped. The columns
   are unpacked as `start_page, end_page, document_type, document_date, doi, manual_intervention`.
2. **Select the prompt.** `document_type` (the category int) indexes
   [`prompts`](../../mrr_ai/prompts.py): `category_01` ... `category_14`, `category_100`; any
   unrecognized value falls back to `category_100`. (Note `category_06` and `category_11`
   prompts exist even though the [taxonomy](categorization.md#the-taxonomy) no longer assigns
   `6` automatically - the manual path can still specify them.)
3. **OCR the pages.** `extract_text_from_selected_pages(state.pdf_filepath, start..end)` rasterizes
   each page with Poppler (`pdf2image`) and runs Tesseract (`pytesseract`) - local, no PHI for
   this step.
4. **Summarize (OpenAI call #1).** `client.chat.completions.create` with the category prompt as
   the system message and the OCR text as the user message (`temperature=0.8, max_tokens=2048`).
5. **Title + entity (OpenAI call #2).** A second call with `_TITLE_PROMPT` extracts
   `"[Title] - [Entity responsible for the encounter]"` from the same text. (So every
   sub-document costs **two** OpenAI calls.)
6. **Assemble the entry.** An `output_dict` is built and appended to `state.all_data`:
   - `summaryDate` = the row's `doc_date`;
   - `summaryTitle` = optional `"[ManualCheck] "` prefix + extracted title + optional
     `" [Diagnostic Study]"` (category 3 only) + `" (Pages start-end)"`;
   - `summaryText` = optional `"**DOI**:<injury>,"` + the summary.

The route returns `big_text` (all entries concatenated) for display. `state.all_data`
**accumulates across calls** within the single process; it is the buffer that export reads.

### The category prompts

Each `prompts["category_NN"]` is a long system prompt that pins the **output style** (one
paragraph, each point wrapped in `**...**` to mimic bold, no invented points, omit absent
points) and lists the **document-type templates** with the section headers to summarize. They
mirror the source files in [../reference/prompts/](../reference/prompts/). The richest is
`category_13` (QME/AME): it demands verbatim diagnoses, a detailed physical exam, and the exact
AMA Whole-Person Impairment language - the high-value, error-sensitive content.

> **Editable at runtime (ADR [0006](../decisions/0006-editable-catalog-admin.md)).** `prompts.py`
> is now the **seed + fallback**: the summary prompts live in the `Prompt` DB table and are
> edited from the admin console. The live account-based summarizer (`services/summarize_engine.py`,
> used by the review app + bundles) resolves each prompt DB-first via `catalog.get_prompt`, with
> the general (`100`) prompt as the fallback for a category that has none (e.g. `11`). This legacy
> CSV path still reads the `prompts.py` dict directly.

## Stage 4 - export to Word

Code: [../../mrr_ai/blueprints/export.py](../../mrr_ai/blueprints/export.py).

`/exportresultstoword` builds the document via `_build_mrr_document`, which:

1. **Sorts** `state.all_data` chronologically by `summaryDate` (`MM/DD/YYYY`; unparseable dates
   sort to the epoch via `datetime.min`).
2. Builds a `python-docx` document: a running **header** (`RE: <name>` / DOB / `Page`), a
   centered bold-underlined **title** (the QME/AME string), a **"Medical Record Review"**
   heading, an intro line (`"I have received <num_pages> pages of medical records from
   <lawfirm>..."`), then **one paragraph per entry** formatted `"_<date>_\t****<title>****:
   <text>"`, and a closing line. Everything is Times New Roman.
3. Saves `<main_filename>_int.docx` and `<main_filename>_rep.docx` under `~/MRRs/` and returns
   the file as a download.

`patientName`, `patientdob`, `QMEorAME`, and `lawfirm` come from the request body; the patient
name and DOB are typically obtained by the **extraction** routes (`/getpatientnameanddob`,
`/getlawfirm` in `blueprints/extraction.py`, which ask OpenAI to read them off the first pages).
`/exportresultstoCSV` separately writes provided CSV text to `~/MRRs/<main_filename>.csv`.

## Stage 3 variant - `/summarize_indiv_record`

For the "individual records" workflow (a patient folder of separate PDFs, see
`blueprints/individual_mrr.py`), `/summarize_indiv_record` resets `state.all_data`, OCRs each
**whole** file (`extract_text_from_all_pages`), and summarizes with a hardcoded `gpt-4o-mini`.

> **Known bug (flagged, not fixed here):** its prompt-selection chain is written
> `if option == 1 or "1":  ... elif option == 2 or "2": ...`. Because the non-empty string
> `"1"` is always truthy, the **first branch is always taken**, so every individual record is
> summarized with the `category_01` prompt regardless of its actual category. The CSV-driven
> `/summarize` does **not** have this bug (it compares against ints only). Worth a fix; called
> out here so the doc matches the code.

## State, models, and PHI

- **State coupling:** `/summarize` needs `state.txt_filepath` (the CSV) and `state.pdf_filepath`
  (the PDF); export needs `state.all_data`, `state.num_pages`, `state.main_filename`. This
  cross-request state is why the app is **single-process** ([../architecture.md](../architecture.md)).
- **Models:** `/summarize` takes the model from the request; the individual-record route
  hardcodes `gpt-4o-mini`. Both use `temperature=0.8, max_tokens=2048`.
- **PHI:** OCR'd page text is sent to **OpenAI** for both the summary and the title/entity
  extraction; export writes the patient name and DOB into the Word header. Any change to these
  prompts or call paths needs the PR template's HIPAA review.
- **Side effect:** both summarize routes also dump `str(state.all_data)` to `all_data_temp.txt`
  in the working directory (a debug artifact; safe to delete, regenerated on the next run).

## Related

- Inputs: [segmentation.md](segmentation.md), [categorization.md](categorization.md),
  [../reference/csv-contract.md](../reference/csv-contract.md)
- Prompt sources: [../reference/prompts/](../reference/prompts/)
- Output-formatting macros (applied to the Word doc downstream): [../reference/macros/](../reference/macros/)
- Endpoints: [../reference/api-routes.md](../reference/api-routes.md)
