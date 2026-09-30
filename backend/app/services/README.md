# backend/app/services - the pipeline

The work itself: reading pages, finding and categorizing sub-documents, checking duplicates,
writing and auditing summaries, building exports, and calling models. Routes (`app/api/`) and job
functions (`app/worker/tasks.py`) call into these modules; the modules never import FastAPI.

## Modules by stage

| stage | module | what it does |
| --- | --- | --- |
| Upload | `pdf.py` | Page count and file size (pypdf). |
| | `files.py` | Filesystem-safe filename helper. |
| | `aggregate.py` | Merges several already-split PDFs into one record and computes each source's page range. |
| Page text | `page_text.py` | Extracts each page's text once (text layer or OCR), stores it in `page_texts`, and reuses it in every stage. |
| | `ocr.py` | Tesseract OCR over pages rasterized by Poppler. |
| | `rasterise.py` | Renders pages to images for backends that cannot take an inline PDF. |
| Segmentation | `segment_engine.py` | Runs identification: windows, the model call per window, the merge, categorizing, the verify pass, injury dates. |
| | `windows.py` | Packs pages into overlapping windows by byte budget and page cap. |
| | `gemini.py` | The segmentation prompt, response schema and tolerant row parsing. |
| | `verify_pass.py` | Checks suspect boundaries and marks merge suggestions for the reviewer. |
| | `summary_doi.py` | Reads each row's date of injury; formats the DOI prefix for summaries. |
| | `deposition_pages.py` | Finds a deposition transcript's own printed page numbers. |
| Categorization | `classification.py` | The cascade: title rules, then an embedding model and a constrained LLM call that must agree. |
| | `taxonomy.py`, `seed_catalog.py` | The category constants; used only while the `categories` table is empty. |
| | `catalog.py` | Reads the editable catalog (categories and prompts) from the database, with the constants as fallback. |
| Review | `rows.py` | Validates reviewer-edited rows. |
| | `extraction.py` | Reads the report header fields from a record's first pages. |
| Duplicates | `dedup.py` | Groups included rows that look like copies, gates them by date and title/category, and asks a model to confirm. |
| Summaries | `summarize_engine.py` | Summarizes one row: prompt assembly, the body, title and audit calls, notices, provenance. |
| | `prompts.py` | The per-category summary prompts in code (an admin-edited database row overrides one). |
| | `summary_verify.py` | The audit: checks a draft against its source and returns corrections, subject to guards. |
| | `house_style.py` | Deterministic formatting fixes applied after the model writes. |
| Exports | `reporting.py` | The Word review letter and memo (python-docx). |
| | `linked_pdf.py` | The letter followed by the source record, with links to each entry's pages. |
| | `bundles.py` | Category-filtered bundle PDF with a cover page, and the bundle summary. |
| | `downloads.py` | Parks a built export behind a short-lived token and tracks how its download ended. |
| Jobs | `jobs.py` | Creates jobs, enforces one active job per record, enqueues on the owner's lane, owns the status maps. |
| | `provenance.py` | Fingerprints the prompt text that produced a stored row or job. |
| | `audit.py` | Writes `audit_log` rows (ids and action names only). |
| | `pools.py` | Drains a `ThreadPoolExecutor` with a deadline. |
| Models | [`llm/`](llm/README.md) | The provider seam: backends, pacing, preflight. |
| | `genai_client.py`, `genai_retry.py`, `genai_metrics.py` | The Gemini client, its retry and deadline wrapper, and per-model call counters. |

Explanations: [Segmentation](../../../docs/explanation/segmentation.md),
[Categorization](../../../docs/explanation/categorization.md),
[Duplicate detection](../../../docs/explanation/duplicate-detection.md),
[Summarization](../../../docs/explanation/summarization.md),
[Exports and downloads](../../../docs/explanation/exports-and-downloads.md),
[OCR and page text](../../../docs/explanation/ocr-and-page-text.md),
[Model providers](../../../docs/explanation/model-providers.md).

## Tests

Most modules have a `backend/tests/test_<module>.py`. Run one area with, for example:

```bash
cd backend
uv run pytest -q tests/test_classification.py tests/test_segment_engine.py
```

<!-- reviewed: 2026-09-30 -->
