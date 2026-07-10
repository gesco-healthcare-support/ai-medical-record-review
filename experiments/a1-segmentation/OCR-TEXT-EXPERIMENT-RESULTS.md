# OCR-text segmentation experiment - Mayo replication (2026-07-09)

Tests whether feeding **OCR text** to the segmenter (the Mayo JAMIA Open 2026 approach)
beats the production **image** window method. Isolates ONE variable - input modality -
holding the production prompt, schema, temperature 0, and absolute-offset logic fixed.
Reuses the cached Tesseract OCR (`cache/ocr/<case>.jsonl`), so OCR cost is $0.

Reproduce: `uv run python src/ocr_text_experiment.py "Case 3"` (add `--dry` for offline
call-count only). Model gemini-2.5-flash on Vertex/BAA.

Two text modes:
- **whole-case**: one text call over all pages (the closest to Mayo's holistic single call).
- **windowed**: the image method's byte-budgeted windows + ownership merge, but text input
  (a controlled A/B against `1_windows`).

## Results (strict doc-F1; R = exact start-recall)

| Case | Image baseline | Text whole-case (Mayo) | Text windowed |
|---|---|---|---|
| Case 3 (227pp, clean OCR) | 0.61 / R 1.00 (`sol1`) | 0.613 / R 1.00 / P 0.699 / over 1.43 | 0.618 / R 1.00 / P 0.708 / over 1.41 |
| Case 1 (294pp) | 0.495 / R 0.94 (`naive`) | **0.278 / R 0.657** / P 0.389 / over 1.69 | 0.486 / R 0.955 / P 0.542 / over 1.76 |
| Case 2 (363pp, dense) | 0.532 / R 0.92 (`naive`) | **0.496 / R 0.730** / P 0.657 / over 1.11 | 0.671 / R 0.905 / P 0.687 / over 1.32 |

Image baselines: `sol1` overlapping-windows was only bake-off'd on Case 3; Cases 1-2 use
`naive_chunk` image (both are no-verify window/chunk segmenters -> fair). Temp-0 variance ~±0.05.
Cost: ~$0.15 total (16 calls + preflight). OCR coverage strong even on dense Case 2 (0 empty pages).

## Read - NEGATIVE result

1. **The pure Mayo holistic approach (whole-case text) fails on our bundles.** Recall
   collapses to 0.66 (Case 1) and 0.73 (Case 2) - it merges away 1/4 to 1/3 of documents,
   the unrecoverable error. It held only on the 51-doc Case 3. Mayo scored 0.95 on ~2-doc
   packets; asking one call to track 51-67 boundaries across 460-800K chars of flat text
   loses coherence and lumps documents. Same failure mode as `5_accumulate` (more context
   -> merge bias). Holistic text segmentation does not scale past a handful of documents.
2. **Windowed text is only on par with the image method, and costs recall.** Comparable
   or marginally better doc-F1 (via slightly better precision), but on dense Case 2 recall
   drops to 0.905 vs image 0.92. Recall is paramount (merges are unrecoverable), so this is
   a cost, not a win. On Case 3 (the only exact `sol1`-image head-to-head) they tie.
3. **Text did NOT reduce over-segmentation** (1.41/1.76/1.32 vs image 1.53/1.78/1.51).
   If over-splitting were an image-perception artifact, text would have cut it. It didn't -
   confirming over-segmentation is driven by the deliberate recall-first prompt bias + gold
   convention, NOT the image modality.

## Why

OCR text flattens the **visual boundary cues** images preserve - new letterhead, form
layout, fax cover sheet, break-page whitespace - which are exactly what lets the image
method hold recall 1.00 (a new document usually LOOKS different even when text reads
continuously). Strip the image and recall drops. Empirically confirms segmentation is a
VISION task for these documents, not a text task.

## Corrections

- Text is NOT cheaper than images: ~1.7x the tokens (OCR text of a dense page exceeds the
  ~258 tokens/page image encoding). Still pennies, but the earlier "text is cheaper"
  expectation was wrong.

## Implication

OCR-text is not the lever for over-segmentation. The real lever remains: reduce over-splits
WITHOUT losing recall - which points at the segmenter's prompt bias and the (currently
timid) verify pass, not the input modality. App code unchanged; measurement only.
