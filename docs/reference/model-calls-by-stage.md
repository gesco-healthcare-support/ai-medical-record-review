# Model calls by stage reference

For each of the eight model stages: the call site, provider entry point, model setting and default,
thinking setting, output cap, request shape, reply parsing, and what happens when the call fails.

Source of truth: `backend/app/config.py` (`Settings.model_for()`, `Settings.model_for_stage()`,
`Settings.thinking_for()`, `Settings.vllm_thinking_for()`), `backend/app/services/llm/`, and the
call sites named in each section. The routing and retry machinery is explained in
[Model providers](../explanation/model-providers.md); every setting is in the
[Configuration reference](configuration.md).

## Summary

| Stage | Call site | Provider entry point | Model on Gemini (default) | Thinking on Gemini | Max output tokens | Reply |
| --- | --- | --- | --- | --- | --- | --- |
| `summarize` body | `backend/app/services/summarize_engine.py` `_generate_body()` -> `_generate()` | `get_provider().generate_text` | `SUMMARY_BODY_MODEL` (<- `SUMMARY_MODEL`, `gemini-3.5-flash`) | `SUMMARY_THINKING_BUDGET` (-1) | `SUMMARY_MAX_OUTPUT_TOKENS` (8192); one re-ask at x `SUMMARY_TRUNCATION_RETRY_MULTIPLIER` | Free text |
| `summarize` title | `summarize_engine.summarize_row()` -> `_generate()` | `get_provider().generate_text` | `SUMMARY_TITLE_MODEL` (`gemini-2.5-flash`) | `SUMMARY_THINKING_BUDGET` (-1) | `SUMMARY_MAX_OUTPUT_TOKENS` (8192) | Free text |
| `summarize` audit | `backend/app/services/summary_verify.py` `verify_summary()` | `get_provider().generate_structured` | `AUDIT_MODEL` (`gemini-2.5-flash`) | `SUMMARY_THINKING_BUDGET` (-1) | Argument, else `AUDIT_MAX_OUTPUT_TOKENS`, else `SUMMARY_MAX_OUTPUT_TOKENS` | JSON |
| `segment` | `backend/app/services/segment_engine.py` `_window_rows()` | `provider_for_stage("segment").generate_structured` | `GENAI_MODEL` (`gemini-2.5-flash` on Vertex) | `SEGMENT_THINKING_BUDGET` (-1) | not set | JSON array |
| `extract` | `backend/app/services/extraction.py` `extract_header()` | `provider_for_stage("extract").generate_structured` | `GENAI_MODEL` | `GEMINI_THINKING_BUDGET` (0) | not set | JSON |
| `dedup` | `backend/app/services/dedup.py` `confirm_cluster()` | `provider_for_stage("dedup").generate_structured` | `CLASSIFY_MODEL` (`gemini-2.5-flash-lite`) | `GEMINI_THINKING_BUDGET` (0) | 256 (hardcoded) | JSON |
| `classify` | `backend/app/services/classification.py` `llm_classify()` | `provider_for_stage("classify").generate_choice` | `CLASSIFY_MODEL` (`gemini-2.5-flash-lite`) | `GEMINI_THINKING_BUDGET` (0) | not set | One category id |
| `verify` | `backend/app/services/verify_pass.py` `_same_document()` | `provider_for_stage("verify").generate_choice` | `VERIFY_MODEL` (<- `GENAI_MODEL`) | `GEMINI_THINKING_BUDGET` (0) | not set | `YES` or `NO` |
| `doi` | `backend/app/services/summary_doi.py` `extract_injury_date()` | `provider_for_stage("doi").generate_text` | `GENAI_MODEL` | `SUMMARY_THINKING_BUDGET` (-1) | `DOI_MAX_OUTPUT_TOKENS` (2048) | Free text |
| `deposition` | `backend/app/services/deposition_pages.py` `transcript_page_offset()` | `provider_for_stage("deposition").generate_structured` | `GENAI_MODEL` | `SUMMARY_THINKING_BUDGET` (-1) | `DEPOSITION_MAX_OUTPUT_TOKENS` (2048) | JSON |

On vLLM every non-summarize stage uses `VLLM_MODEL`, and the summarize triple defaults to
`VLLM_MODEL` unless set explicitly. vLLM thinking is off for every stage unless the stage is named in
`VLLM_THINKING_STAGES`. OpenAI serves the summarize stage only; it sends no thinking setting.

`GENAI_MODEL` defaults to `gemini-2.5-flash` when `GOOGLE_GENAI_USE_VERTEXAI` is true and to
`gemini-flash-latest` otherwise.

## Common to every call

| Aspect | Behaviour | Source |
| --- | --- | --- |
| Temperature | 0.0 for every call except the summary body, which uses `SUMMARY_TEMPERATURE` (default 0.0) | Each call site |
| Attempts | `GENAI_MAX_RETRIES` (8) per logical call, full-jitter backoff between attempts | `genai_retry.generate_with_retry()`, `OpenAIProvider._call()`, `VLLMProvider._call()` |
| Pacing | `pacing.acquire()` before every attempt, one 300 s budget per logical call | `backend/app/services/llm/pacing.py` |
| Metrics | Every attempt counted in Redis `vertex:metrics:<model>` | `backend/app/services/genai_metrics.py` |
| Gemini deadline | `max(GENAI_HTTP_TIMEOUT_MS, estimated tokens x GENAI_TIMEOUT_PER_1K_TOKENS_MS / 1000)`; one retry at x `GENAI_DEADLINE_RETRY_MULTIPLIER` after a 504 | `genai_retry.generate_with_retry()` |
| Cancellation | Backoff sleeps raise `JobCancelled` when the job is cancelled; every call site that catches exceptions re-raises `JobCancelled` | `_cancellable_sleep()` in each provider module |
| Truncation | `LLMResponse.truncated` is true on a Gemini `MAX_TOKENS` or an OpenAI/vLLM `length` finish | `backend/app/services/llm/base.py` `LLMResponse` |

## summarize

Three calls per summary row. The three models are resolved once when the summarize job is created
(`backend/app/services/jobs.py` `create_job()`), stored as `jobs.model`, `jobs.title_model` and
`jobs.audit_model`, and passed to every row. The backend that answered is stored per row in
`summaries.backend` (`summarize_engine._backend_name()`, which reads `get_provider().name`).

### Body

| Item | Value |
| --- | --- |
| Entry point | `get_provider().generate_text(stage="summarize")` from `summarize_engine._generate()`, called by `_generate_body()` |
| Model | Job's `model`; config source `Settings.model_for("body")` = `SUMMARY_BODY_MODEL`. Gemini default: `SUMMARY_MODEL`, which defaults to `gemini-3.5-flash`. vLLM default: `VLLM_MODEL`. OpenAI: must be set |
| Thinking | Gemini `SUMMARY_THINKING_BUDGET`; vLLM on only if `summarize` is in `VLLM_THINKING_STAGES` |
| System | The category's summary prompt plus per-row blocks (`summarize_engine._build_system_message()`) |
| Parts | With `SUMMARY_MULTIMODAL` true: up to `SUMMARY_IMAGE_MAX_PAGES` page images (JPEG), then `OCR TEXT:` plus the row's OCR text, then a closing instruction. If rasterising fails, or `SUMMARY_MULTIMODAL` is false: the OCR text only |
| Temperature | `SUMMARY_TEMPERATURE` |
| Max output tokens | `SUMMARY_MAX_OUTPUT_TOKENS` |
| Truncated reply | `_retry_if_truncated()` re-asks once at `SUMMARY_MAX_OUTPUT_TOKENS x SUMMARY_TRUNCATION_RETRY_MULTIPLIER` (skipped when the multiplier is 1 or less). A finished retry replaces the body; between two truncated replies the longer is kept; an empty or failed retry keeps the first body |
| Failure | When the call raises an exception whose `code` is 429 (`backend/app/errors.py` `is_rate_limited()`) and `SUMMARY_BODY_FALLBACK_MODEL` is set and differs from the body model, the call is repeated once on the fallback model, logged at WARNING, and the row records the fallback model as its model. Any other exception propagates to the summarize worker, which decides pause or needs-attention (see [Pipeline and jobs](../explanation/pipeline-and-jobs.md)) |
| No call | A row with no OCR text makes no model call: it becomes an unreadable-pages notice or raises `EmptyExtractionError` |

### Title

| Item | Value |
| --- | --- |
| Entry point | `get_provider().generate_text(stage="summarize")` from `summarize_engine._generate()` |
| Model | Job's `title_model` (older jobs: `model`); config source `Settings.model_for("title")` = `SUMMARY_TITLE_MODEL`. Gemini default `gemini-2.5-flash`; vLLM default `VLLM_MODEL`; OpenAI: must be set |
| Thinking | As the body |
| System | `summarize_engine.TITLE_PROMPT` |
| Parts | The row's OCR text only |
| Temperature | 0.0 |
| Max output tokens | `SUMMARY_MAX_OUTPUT_TOKENS` |
| Reply handling | `truncated` is ignored. `_usable_title()` keeps a reply of 1 to 200 characters (`MAX_GENERATED_TITLE`) and otherwise falls back to the row's segmentation title |
| Failure | Exceptions propagate to the summarize worker |

### Audit

Runs when `SUMMARY_VERIFY` is true (the default of `summarize_row(verify=None)`); callers such as
bundle export pass `verify=False`.

| Item | Value |
| --- | --- |
| Entry point | `get_provider().generate_structured(stage="summarize")` from `summary_verify.verify_summary()` |
| Model | Job's `audit_model` (older jobs: `model`); config source `Settings.model_for("audit")` = `AUDIT_MODEL`. Gemini default `gemini-2.5-flash`; vLLM default `VLLM_MODEL`; OpenAI: must be set |
| Thinking | As the body |
| System | `summary_verify.VERIFY_PROMPT` |
| Parts | One text part: `SOURCE`, the document date when known, `TITLE` when present, `SUMMARY` |
| Temperature | 0.0 |
| Max output tokens | The `max_output_tokens` argument, else `AUDIT_MAX_OUTPUT_TOKENS`, else `SUMMARY_MAX_OUTPUT_TOKENS` |
| Schema | `summary_verify._RESPONSE_SCHEMA`: object with `fixed_text` (string), `fixed_title` (string or null), `issues` (array of `{type, detail}`, `type` one of `unsupported`, `contradiction`, `date`, `laterality`, `vitals`, `pain_descriptor`, `capitalization`, `range_of_motion`, `duplicate_finding`, `prior_visit`) |
| Reply handling | `json.loads` of the reply text, then `_verified_reply()` |
| Truncated reply | Logged at WARNING; the original summary is kept, marked unverified and truncated |
| Failure | Any other exception, including a JSON parse error: logged at WARNING, original summary kept unverified. `JobCancelled` is re-raised |
| No call | An empty summary is returned unverified without a call |

## segment

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("segment").generate_structured(stage="segment")` from `segment_engine._window_rows()` |
| Model | `Settings.model_for_stage("segment")`: `GENAI_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `SEGMENT_THINKING_BUDGET` (-1); vLLM on only if `segment` is in `VLLM_THINKING_STAGES` |
| System | `backend/app/services/gemini.py` `SEGMENTATION_SYSTEM` |
| Parts | Gemini: the window's pages as one inline PDF (`DocumentPart`). vLLM: the window's pages as JPEG images, each preceded by a `Page N` text part, capped at `VLLM_SEGMENT_MAX_PAGES`. Then `SEGMENTATION_PROMPT` last |
| Window size | Gemini: packed to `WINDOW_BUDGET_MB` of raw PDF bytes and at most `WINDOW_MAX_PAGES` pages. vLLM: at most `min(WINDOW_MAX_PAGES, VLLM_SEGMENT_MAX_PAGES)` pages, no byte budget. Overlap `WINDOW_OVERLAP` |
| Sampling | Temperature 0.0, `top_p` 0.95, `top_k` 40 (`segment_engine._TOP_P`, `_TOP_K`) |
| Max output tokens | Not set |
| Schema | `gemini.SEGMENT_RESPONSE_SCHEMA`: array of objects with `id`, `s`, `e`, `t`, `d`, `m` (`m` is `x` or `-`); `s`, `e`, `t`, `d`, `m` required |
| Reply handling | Code fences stripped, `json.loads`, each element through `gemini.parse_segment_item()`; an element that fails to parse is skipped. `truncated` is not read |
| Concurrency | `SEGMENT_WINDOW_WORKERS` windows at once |
| Failure | An exception in any window, including a JSON parse error, propagates and fails the segment job. A pool that outlives `Settings.pool_timeout()` raises `PipelineTimeoutError` |

## extract

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("extract").generate_structured(stage="extract")` from `extraction.extract_header()` |
| Model | `Settings.model_for_stage("extract")`: `GENAI_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `GEMINI_THINKING_BUDGET` (0); vLLM on only if `extract` is in `VLLM_THINKING_STAGES` |
| System | `extraction._HEADER_SYSTEM` |
| Parts | One text part: an instruction plus the OCR text of the first pages (callers pass pages 1 to `min(15, page_count)`) |
| Max output tokens | Not set |
| Schema | `extraction._HEADER_SCHEMA`: object with `first_name`, `last_name`, `dob`, `lawfirm`, all strings, all required |
| Reply handling | `json.loads`; a decode error returns all four fields empty. Missing keys become empty strings. Empty OCR text returns all four empty without a call |
| Failure | Provider exceptions are not caught in `extract_header()`. The segment worker's call (`backend/app/worker/tasks.py`) logs a WARNING and continues; the API route (`backend/app/api/documents.py` `extract_header_route()`) handles `PipelineError` |

## dedup

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("dedup").generate_structured(stage="dedup")` from `dedup.confirm_cluster()` |
| Model | Caller's `model`, else `Settings.model_for_stage("dedup")`: `CLASSIFY_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `GEMINI_THINKING_BUDGET` (0); vLLM on only if `dedup` is in `VLLM_THINKING_STAGES` |
| System | `dedup.CONFIRM_PROMPT` |
| Parts | One text part: numbered blocks of title, date and the first 1,500 characters of each member's text (`_EXCERPT_CHARS`) |
| Max output tokens | 256 |
| Schema | `dedup._CONFIRM_SCHEMA`: object with `duplicate_indices`, an array of integers |
| Reply handling | `json.loads`; indices outside 1..N are dropped; fewer than two confirmed members returns an empty list |
| Failure | Any exception, including a parse error: WARNING, and all candidate members are returned (the algorithmic candidate is trusted). `JobCancelled` is re-raised |
| No call | Clusters with fewer than two members; candidates whose similarity is at or above `DUPE_MODEL_OVERRIDE` skip the call in `backend/app/worker/tasks.py` |

## classify

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("classify").generate_choice(stage="classify")` from `classification.llm_classify()` |
| Model | Caller's `model`, else `Settings.model_for_stage("classify")`: `CLASSIFY_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `GEMINI_THINKING_BUDGET` (0); vLLM on only if `classify` is in `VLLM_THINKING_STAGES` |
| System | A fixed instruction to return one category id |
| Parts | One text part: the instruction, the category catalogue text, and the document text |
| Choices | The auto-assignable category ids (`classification._allowed_ids()`) |
| Max output tokens | Not set |
| Reply handling | Stripped text; returned only if it is one of the allowed ids, else `None` |
| Failure | Any exception: WARNING and `None`; `classify()` then decides from the embedding vote alone and flags the row for review. `JobCancelled` is re-raised |
| No call | `classify()` returns before the model when a rule matches the title or the text is empty |

## verify

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("verify").generate_choice(stage="verify")` from `verify_pass._same_document()` |
| Model | `Settings.model_for_stage("verify")`: `VERIFY_MODEL` (default `GENAI_MODEL`) on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `GEMINI_THINKING_BUDGET` (0); vLLM on only if `verify` is in `VLLM_THINKING_STAGES` |
| System | `verify_pass._VERIFY_SYSTEM` |
| Parts | PNG images first: the last page of the previous row, then up to two pages of this row (`FRAGMENT_PAGE_CAP`), rendered at 120 dpi; then the prompt, with OCR text from the two boundary pages when `VERIFY_USE_TEXT` is true |
| Choices | `YES`, `NO` |
| Max output tokens | Not set |
| Reply handling | True only when the stripped reply is `YES` |
| Failure | Any exception: WARNING and `False` (the boundary is kept). `JobCancelled` is re-raised |
| When it runs | At the end of segmentation when `VERIFY_MERGE` is true, on the boundaries `verify_pass.suspect_indices()` selects (`VERIFY_SUSPECT_CAP`, `VERIFY_TRIGGERED_ONLY`), on `CLASSIFY_WORKERS` threads |

## doi

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("doi").generate_text(stage="doi")` from `summary_doi.extract_injury_date()` |
| Model | Caller's `model`, else `Settings.model_for_stage("doi")`: `GENAI_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `SUMMARY_THINKING_BUDGET` (-1); vLLM on only if `doi` is in `VLLM_THINKING_STAGES` |
| System | None |
| Parts | Gemini: the sub-document's first pages, at most 10 (`summary_doi._MAX_PAGES`), as one inline PDF. vLLM: the same pages as images at `DOI_IMAGE_LONG_EDGE_PX`. Then `summary_doi._ISOLATION_PROMPT` |
| Max output tokens | `DOI_MAX_OUTPUT_TOKENS` |
| Reply handling | `summary_doi._clean()`: dates normalised to `MM/DD/YY`, a cumulative-trauma range kept as one `CT` item, several items joined with ` & `, `-` when none |
| Truncated reply | Raises `RuntimeError` inside the call's own handler |
| Failure | Any exception, including truncation: WARNING; returns `-` by default, re-raises when called with `strict=True` (`backend/scripts/backfill_doi.py`). `JobCancelled` is re-raised |
| When it runs | Once per sub-document at the end of segmentation, on `DOI_WORKERS` threads (`segment_engine.run_segmentation()`) |

## deposition

| Item | Value |
| --- | --- |
| Entry point | `provider_for_stage("deposition").generate_structured(stage="deposition")` from `deposition_pages.transcript_page_offset()` |
| Model | Caller's `model`, else `Settings.model_for_stage("deposition")`: `GENAI_MODEL` on Gemini, `VLLM_MODEL` on vLLM |
| Thinking | Gemini `SUMMARY_THINKING_BUDGET` (-1); vLLM on only if `deposition` is in `VLLM_THINKING_STAGES` |
| System | None |
| Parts | Gemini: the transcript's first pages, at most 6 (`deposition_pages._MAX_PAGES`), as one inline PDF. vLLM: the same pages as images at `DEPOSITION_IMAGE_LONG_EDGE_PX`. Then `deposition_pages._PROMPT` |
| Max output tokens | `DEPOSITION_MAX_OUTPUT_TOKENS` |
| Schema | `deposition_pages._SCHEMA`: object with `pages`, an array of `{i, printed}` integers |
| Reply handling | `json.loads`, then `_offset_from()`: the single offset that at least two pages (`_MIN_AGREEING`) agree on, else `None` |
| Truncated reply | Raises `TranscriptPagesUnreadableError`, a `PipelineError`, which propagates |
| Failure | Any other exception: WARNING and `None` (the summary cites no transcript pages). `JobCancelled` is re-raised |
| When it runs | For category 9 rows while the summary system message is built (`summarize_engine._build_system_message()`) |

## Backend-specific request shape

| Aspect | Gemini | OpenAI | vLLM |
| --- | --- | --- | --- |
| Inline PDF (`DocumentPart`) | Sent | Refused (`TypeError`) | Refused (`TypeError`); segment, doi and deposition send images instead |
| `top_k` (segment only) | Sent | Refused (`TypeError`) | Sent in `extra_body` |
| Structured output | `application/json` with the translated schema | `json_schema`, `strict: true` | `json_schema`, no `strict` |
| Choice | `text/x.enum` | `{"choice": enum}` object, unwrapped; an unparseable reply raises `ValueError` | `extra_body.structured_outputs.choice` |
| Strict schema side effect | none | Every property required, `additionalProperties: false` | Same as OpenAI, so the unused segmentation `id` must also be emitted |
| Image token estimate for the pacer | 1300 per image | 2200 per image | 827 per image |

## Related pages

- [Model providers](../explanation/model-providers.md)
- [Configuration reference](configuration.md)
- [Summarization](../explanation/summarization.md)
- [Segmentation](../explanation/segmentation.md)
- [Categorization](../explanation/categorization.md)
- [Duplicate detection](../explanation/duplicate-detection.md)
- [How to switch model backends](../how-to/switch-model-backends.md)

<!-- reviewed: 2026-09-30 -->
