# Architecture

## Purpose

MRR AI turns a large, scanned **medical-record (MR) PDF** into a summarized **Medical
Record Review (MRR)** Word document. It is a Flask app (Python 3.12) that orchestrates
Gemini (segmentation), OpenAI (summarization), and local OCR (Tesseract + Poppler).

## The pipeline

```
MR PDF ──► 1. SEGMENT ──► 2. CATEGORIZE ──► [ 6-col CSV ] ──► 3. SUMMARIZE ──► 4. EXPORT ──► MRR .docx
           (Gemini)        (B5 cascade)       (the contract)    (OpenAI)         (python-docx)
```

1. **Segment** - `/getPages` uploads the PDF to Gemini and gets sub-document page ranges
   (start/end) + title/date/injury/manual-flag, as JSON. See
   [explanation/segmentation.md](explanation/segmentation.md).
2. **Categorize** - each sub-document title is classified into a category number (1-5, 7-14,
   100) by the **B5 cascade** (deterministic rules -> local embeddings -> Gemini constrained-
   enum), escalating to first-page OCR on low confidence. See
   [explanation/categorization.md](explanation/categorization.md).
3. The page ranges + categories are written to a **6-column CSV** - see
   [reference/csv-contract.md](reference/csv-contract.md). This CSV is the contract: it can
   be produced automatically (Gemini) or by hand, and everything downstream consumes it.
4. **Summarize** - `/summarize` OCRs each sub-document's pages and sends them to OpenAI
   with the category-specific prompt from `prompts.py`.
5. **Export** - `/exportresultstoword` assembles the summaries into a Word document. Stages 3-4:
   [explanation/summarization.md](explanation/summarization.md).

## Package layout

```
app.py                 thin entry: app = create_app()
mrr_ai/
  __init__.py          create_app() application factory
  config.py            env validation (fail-fast), paths, constants
  extensions.py        genai_client (Gemini) + client (OpenAI), built once
  state.py             shared mutable globals (see "State" below)
  taxonomy.py          category catalog (ids, names, corpora) for the B5 cascade
  groups.py            legacy category taxonomy (superseded by taxonomy.py; unused)
  prompts.py           per-category summarization prompts
  blueprints/          HTTP routes grouped by area
    pages.py           UI page renders + /reset
    upload.py          /upload, /uploadAndCheckCSV, /uploadPages
    segmentation.py    /getPages, /segmentPDF
    summarize.py       /summarize, /summarize_indiv_record
    reports.py         /getDiagOpRep, /getDepoRep, /compute_page_ranges
    export.py          /exportresultsto{CSV,word}, individual-records export
    extraction.py      /getpatientnameanddob, /getlawfirm
    individual_mrr.py  patient-folder create + multi-file upload
  services/            business logic (no Flask imports)
    pdf.py             sizes, page counts, chunk segmentation
    ocr.py             Tesseract text extraction
    gemini.py          upload/poll, SEGMENTATION_PROMPT, response parsing
    classification.py  B5 categorization cascade (rules -> embeddings -> Gemini enum)
    categorization.py  legacy difflib fuzzy matcher (superseded; unused)
    files.py           allowed_file, date validation, safe_name, line counts
  templates/  static/  Flask UI
tests/                 pytest route-smoke + unit/integration
docs/                  this documentation
experiments/           archived research spikes (e.g., page-stream segmentation)
```

## Request lifecycle

`create_app()` builds the Flask app, sets config, and registers all blueprints. Each
blueprint imports the shared `client`/`genai_client` from `extensions` and the `state`
module, and calls into `services/` for logic. Routes are thin; services are testable in
isolation.

## State model (important)

`state.py` holds module-level globals (`pdf_filepath`, `all_data`, `main_filename`, ...)
that carry data **across requests** (upload sets `pdf_filepath`; summarize reads it).
Blueprints read/write them as `state.<name>`.

Consequence: **the app must run single-process.** A multi-worker server (gunicorn -w >1)
would give each worker its own copy and break the flow. Replacing this with a proper
per-session store is tracked as future work (see ADR-0003).

## External dependencies & PHI data flow

- **Tesseract OCR** + **Poppler** (`pdf2image`) - local, no PHI leaves the host.
- **Gemini** (`google-genai`) - the **whole PDF is uploaded** for segmentation.
- **OpenAI** - **OCR'd page text is sent** for summarization and field extraction.

Both Gemini and OpenAI calls transmit PHI to third parties; any change to prompts,
logging, or these call paths requires the HIPAA review in the PR template. Secrets load
from `.env` (never committed); the app fails fast at startup if they are missing.

## Known limitations (tracked)

- Large PDFs are split into fixed 100-page chunks before Gemini; a sub-document spanning a
  chunk boundary is mis-split (see `experiments/a1-segmentation/` for the fix direction).
- Categorization (the B5 cascade) is live, but its category catalog is not yet curated (B6);
  genuinely ambiguous titles are flagged for manual review rather than mislabeled.
- Single-process state (above).
