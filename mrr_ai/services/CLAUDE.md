# services

Business logic. **Rule: no Flask imports here** - keeps services unit-testable without an
app context. Config and clients come from `mrr_ai.config` / `mrr_ai.extensions`.

- **pdf.py** - `get_pdf_size`, `get_pdf_page_count`, `segment_pdf`, `segment_pdf_locally`
  (the latter writes chunks under `config.UPLOAD_FOLDER`).
- **ocr.py** - `extract_text_from_selected_pages`, `extract_text_from_all_pages` (Tesseract
  via `pdf2image`/Poppler; mock these in tests - they shell out to native binaries).
- **gemini.py** - `SEGMENTATION_PROMPT`, `SEGMENTATION_SYSTEM`, `SEGMENT_RESPONSE_SCHEMA`,
  `parse_segment_item` (data + parsing only; no client).
- **segment_engine.py** - `run_segmentation` (sliding windows + ownership seams + B5
  categorization + progress callback), `merge_window_rows` (pure, unit-tested).
- **windows.py** - `byte_budgeted_windows`, `next_window_start` (window packing under the
  Vertex inline cap).
- **genai_retry.py** - `generate_with_retry` (jittered backoff; pass the client explicitly).
- **classification.py** - the live **B5 categorization cascade** `classify()` (high-precision
  rules -> local sentence-transformers embeddings -> Gemini constrained-enum, with fusion +
  manual-review flagging). Used by `/getPages`. See `../../docs/explanation/categorization.md`.
- **categorization.py** - legacy `normalize` / `similarity` / `categorize_documents` (difflib
  fuzzy match). **Superseded by classification.py and no longer called** (kept pending removal).
- **files.py** - `allowed_file`, `parse_date`, `is_valid_date`, `count_lines_in_file`.

When testing, mock `mrr_ai.extensions.client` / `genai_client` and the OCR functions; pass
synthetic inputs (build tiny PDFs with pypdf). Never use real patient data.
