# services

Business logic. **Rule: no Flask imports here** - keeps services unit-testable without an
app context. Config and clients come from `mrr_ai.config` / `mrr_ai.extensions`.

- **pdf.py** - `get_pdf_size`, `get_pdf_page_count`, `segment_pdf`, `segment_pdf_locally`
  (the latter writes chunks under `config.UPLOAD_FOLDER`).
- **ocr.py** - `extract_text_from_selected_pages`, `extract_text_from_all_pages` (Tesseract
  via `pdf2image`/Poppler; mock these in tests - they shell out to native binaries).
- **gemini.py** - `upload_to_gemini`, `wait_for_files_active`, `parse_segment_item`, and
  `SEGMENTATION_PROMPT` (uses `genai_client`).
- **categorization.py** - `normalize`, `similarity`, `categorize_documents` (difflib fuzzy
  match; mislabels noisy titles to `100` - being replaced by B5/B6).
- **files.py** - `allowed_file`, `parse_date`, `is_valid_date`, `count_lines_in_file`.

When testing, mock `mrr_ai.extensions.client` / `genai_client` and the OCR functions; pass
synthetic inputs (build tiny PDFs with pypdf). Never use real patient data.
