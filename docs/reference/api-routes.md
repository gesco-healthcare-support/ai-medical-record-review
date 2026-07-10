# API Routes Reference

All routes are registered by `create_app()` via blueprints. Unless noted, POST routes
accept JSON or form data and return JSON. The app relies on shared `state` set by earlier
calls (e.g. you must `/upload` before `/summarize`).

## Pages (`blueprints/pages.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Main UI (index.html) |
| GET | `/pages` | Automatic page-segmenter UI |
| GET | `/pagesManual` | Manual page-entry UI |
| GET | `/pdfsegment` | PDF segment UI |
| GET | `/checkCSV` | CSV validation UI |
| GET | `/DiagAndOpReports` | Diagnostic/operative reports UI |
| GET | `/DepositionReports` | Deposition reports UI |
| GET | `/IndividualMRR` | Individual-record MRR UI |
| GET | `/admin` | Admin console (categories + prompts); `is_admin` only |
| POST | `/reset` | Clear session state (pdf_filepath, all_data, ...) |

## Upload (`blueprints/upload.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/upload` | Save the MR PDF; store path + page count in state |
| POST | `/uploadAndCheckCSV` | Save + validate a page-map CSV (missing cols, bad dates, duplicates) |
| POST | `/uploadPages` | Save a page-map file; return line count |

## Segmentation (`blueprints/segmentation.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/getPages` | Gemini segmentation -> 6-col CSV text. Chunks PDFs >100 pages. |
| POST | `/segmentPDF` | Split the PDF into 100-page files under ~/MRRs |

## Summarize (`blueprints/summarize.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/summarize` | For each CSV row: OCR pages, summarize via OpenAI with the category prompt |
| POST | `/summarize_indiv_record` | Summarize each uploaded individual record |

## Reports (`blueprints/reports.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/getDiagOpRep` | Extract+merge category 3/8 pages into one PDF |
| POST | `/getDepoRep` | Extract category 9 (deposition) pages; return count |
| POST | `/compute_page_ranges` | Merge a patient folder's PDFs; return page ranges |

## Export (`blueprints/export.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/exportresultstoCSV` | Save provided text to ~/MRRs/<name>.csv |
| POST | `/exportresultstoword` | Build the MRR Word doc from `state.all_data` |
| POST | `/exportResultsToWordFileIndivRecords` | As above, individual-records variant |

## Extraction (`blueprints/extraction.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/getpatientnameanddob` | OpenAI extract patient name + DOB (pages 5-15) |
| POST | `/getlawfirm` | OpenAI extract attorney/law firm (pages 1-7) |

## Individual MRR (`blueprints/individual_mrr.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/create_patient_folder_indiv_mrr` | Create a patient folder under uploads/ |
| POST | `/upload_files` | Upload multiple PDFs into the patient folder |

## Admin (`blueprints/admin_api.py`) - `is_admin` accounts only

All under `/api/admin`; the app-level gate returns 403 for authenticated non-admins.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/admin/whoami` | Confirm the caller is an admin |
| GET | `/api/admin/categories` | List all categories (incl. inactive) + prompt presence |
| POST | `/api/admin/categories` | Create a category (numeric immutable id) |
| PATCH | `/api/admin/categories/<id>` | Edit name/description/examples/auto_assign/active (id immutable) |
| GET | `/api/admin/prompts/<category_id>` | Get a category's summary prompt (+ effective/fallback) |
| PUT | `/api/admin/prompts/<category_id>` | Set/replace a category's summary prompt |
| POST | `/api/admin/reprocess/<document_id>` | Re-summarize a document with current prompts (replaces prior summaries) |

> Note: this reference predates the account-based document flow; the `/api/documents/*`,
> `/api/segment/*`, and `/api/summarize/*` routes (blueprints `documents_api`, `review_api`)
> are not yet enumerated here.
