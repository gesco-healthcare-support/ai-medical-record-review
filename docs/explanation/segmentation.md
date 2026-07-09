# Segmentation (stage 1): producing the page-map CSV

> Explanation doc. For the high-level pipeline see [../architecture.md](../architecture.md);
> for the output format see [../reference/csv-contract.md](../reference/csv-contract.md); for
> every endpoint see [../reference/api-routes.md](../reference/api-routes.md).

Segmentation turns one large scanned **medical-record (MR) PDF** into a list of
**sub-documents**, each a `(start_page, end_page)` span with a title, dates, and a
manual-review flag. Its output is the 6-column page-map CSV that every downstream stage
consumes. Categorization (filling the `category` column) is folded into this stage on the
automatic path; it is documented separately in [categorization.md](categorization.md).

## Two ways to produce the CSV

The CSV is the contract, and it has **two independent producers** that downstream code cannot
tell apart:

| Path | How the CSV is made | Entry points | Notes |
|------|---------------------|--------------|-------|
| **Automatic (Gemini)** | `/getPages` uploads the PDF to Gemini, which returns sub-document spans + metadata as JSON; the app categorizes each and emits CSV text. | `/upload` then `/getPages` | Fast first pass; the result is shown in the UI for a human to correct. |
| **Manual** | Staff author (or correct) the 6-column CSV by hand. | `/uploadAndCheckCSV`, `/uploadPages` | Historically the **more accurate** path; also the fallback when Gemini mis-splits. |

Both paths end with the CSV saved and its path stored in `state.txt_filepath`, which
[summarization](summarization.md) reads. Nothing downstream branches on which path produced it.

## The automatic path, step by step

Code: [../../mrr_ai/blueprints/segmentation.py](../../mrr_ai/blueprints/segmentation.py),
[../../mrr_ai/services/gemini.py](../../mrr_ai/services/gemini.py),
[../../mrr_ai/services/pdf.py](../../mrr_ai/services/pdf.py).

1. **Upload** (`/upload`, `blueprints/upload.py`). The PDF is saved under `uploads/` (filename
   sanitized via `safe_name`), and `state.pdf_filepath`, `state.num_pages`, and
   `state.main_filename` are set.
2. **Size + page count** (`/getPages`). `get_pdf_size` (MB) and `get_pdf_page_count` decide
   whether chunking is needed. The request may pass `pageDelimiter` (default **100**).
3. **Chunking for scale.** If the PDF is larger than **45 MB or 100 pages**,
   `segment_pdf_locally` splits it into `pageDelimiter`-page chunk PDFs under
   `uploads/<base>_segmented/` and returns their sorted paths (stored in
   `state.sorted_file_paths`). Otherwise the whole PDF is one "chunk". Each chunk's page
   numbers are **local** (`1..delimiter`); a running `current_offset` (incremented by
   `pageDelimiter` per chunk) converts them back to absolute page numbers.
4. **Per chunk: ask Gemini.** `_segment_one_pdf` uploads the chunk via the Gemini Files API
   (`upload_to_gemini`), polls until it is `ACTIVE` (`wait_for_files_active`), then calls
   `generate_content` with the model `gemini-flash-latest`, the `SEGMENTATION_PROMPT`, and a
   config of `temperature=0.0, top_p=0.95, top_k=40, response_mime_type="application/json"`.
   The reply is stripped of any ```` ```json ```` fences and parsed with `json.loads` into an
   array of `{id, s, e, t, d, i, m}` records.
5. **Per record: parse, categorize, format.** `_format_segment_line` runs
   `parse_segment_item` (tolerant extraction, below), classifies the title (the B5 cascade in
   [categorization.md](categorization.md), with OCR escalation on low confidence), sets the
   manual-review flag, and emits one CSV line with `current_offset` added to the page numbers.
   A record that fails to parse is skipped (the batch is not aborted).
6. **Return.** `/getPages` returns `{"pages": "<csv text>"}`; the UI shows it for review.

### What Gemini is asked for (the prompt)

`SEGMENTATION_PROMPT` (in `gemini.py`) fixes the JSON shape and the segmentation rules. The
keys are deliberately terse to save tokens: `id`, `s` (start), `e` (end), `t` (title), `d`
(document/encounter date), `i` (injury date), `m` (manual check). The rules that matter most:

- **Cover every page; never skip a page**, and link pages by counts to find boundaries.
- **No commas in titles** (commas would break the CSV); convert them to dashes.
- A **QME/PQME/AME** evaluation is ONE record even when long and quoting other records;
  supplemental QME/AME reports are separate documents.
- `"X vs Y"` titles are treated as a **Deposition**.
- The **manual-check flag** `m` is `"x"` for handwriting (beyond a signature), many checkbox
  ticks, a work-status report, or a QME/AME report; else `"-"`.
- Empty pages get the title `"Empty Page"`; missing fields are `"-"` (never null).

### Tolerant parsing

`parse_segment_item` exists because Gemini output is not perfectly regular. It accepts either
`t` or `title`, coerces non-string titles, defaults missing `d`/`i`/`m` to `"-"`, and casts
`s`/`e` to `int`. A single malformed element raises (and is skipped by the caller) instead of
the old behavior where one bad element aborted the entire batch.

## The manual path

Staff prepare the 6-column CSV directly (or paste-and-fix the `/getPages` output) and upload
it:

- **`/uploadAndCheckCSV`** saves the file to `state.txt_filepath` and validates it: rows with
  fewer than 4 columns are flagged, column 4 (`doc_date`) is date-checked via `is_valid_date`
  (`-` allowed), and rows sharing the same `(category, doc_date)` are reported as possible
  duplicates. It returns a human-readable report; it does not block summarization.
- **`/uploadPages`** saves the file and returns its line count (a lighter-weight variant).

The manual path is the historical accuracy baseline: the experiments treat "gold" CSVs
(hand-typed) as ground truth and Gemini's output as the thing being measured against them.

## Why chunking is the central limitation

Gemini cannot hold a 600-800 page record in one call, so the PDF is cut into fixed
100-page chunks **before** segmentation. A sub-document that straddles a chunk boundary is
split in two (its first part ends at the chunk edge, its second part starts the next chunk).
This is the known B3/B4 defect. The fix direction - Page Stream Segmentation, which makes
gaps/overlaps unrepresentable - is being measured in the spike under
[../../experiments/a1-segmentation/](../../experiments/a1-segmentation/). It is a spike, not
yet wired into `/getPages`.

## State and PHI

`/getPages` depends on `state.pdf_filepath` (set by `/upload`) and writes
`state.sorted_file_paths`; this cross-request state is why the app must run **single-process**
(see [../architecture.md](../architecture.md)). The **entire PDF is uploaded to Gemini**, so
this path transmits PHI to a third party - any change to the prompt or call path needs the PR
template's HIPAA review.

## Related

- Output format: [../reference/csv-contract.md](../reference/csv-contract.md)
- Categorization of each span: [categorization.md](categorization.md)
- Downstream consumer: [summarization.md](summarization.md)
- Endpoints: [../reference/api-routes.md](../reference/api-routes.md)
- Fix direction for chunk-boundary splits: the PSS experiment (linked above)
