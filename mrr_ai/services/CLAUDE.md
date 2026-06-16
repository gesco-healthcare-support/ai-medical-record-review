# services

Business logic. **Rule: no Flask imports here** - keeps services unit-testable without an
app context. Config and clients come from `mrr_ai.config` / `mrr_ai.extensions`.

- **pdf.py** - `get_pdf_size`, `get_pdf_page_count`, `segment_pdf`, `segment_pdf_locally`
  (the latter writes chunks under `config.UPLOAD_FOLDER`).
- **ocr.py** - `extract_text_from_selected_pages`, `extract_text_from_all_pages` (Tesseract
  via `pdf2image`/Poppler; mock these in tests - they shell out to native binaries).
- **gemini.py** - `upload_to_gemini`, `wait_for_files_active`, `parse_segment_item`, and
  `SEGMENTATION_PROMPT` (uses `genai_client`).
- **classification.py** - the live **B5 categorization cascade** `classify()` (high-precision
  rules -> local sentence-transformers embeddings -> Gemini constrained-enum, with fusion +
  manual-review flagging). Used by `/getPages`. See `../../docs/explanation/categorization.md`.
- **categorization.py** - legacy `normalize` / `similarity` / `categorize_documents` (difflib
  fuzzy match). **Superseded by classification.py and no longer called** (kept pending removal).
- **files.py** - `allowed_file`, `parse_date`, `is_valid_date`, `count_lines_in_file`.

When testing, mock `mrr_ai.extensions.client` / `genai_client` and the OCR functions; pass
synthetic inputs (build tiny PDFs with pypdf). Never use real patient data.
