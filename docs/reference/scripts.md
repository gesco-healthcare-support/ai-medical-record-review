# Scripts reference

Every tracked script under `backend/scripts/`: what it does, whether it is meant to be run again,
its inputs, what it writes, whether it calls a model or reads PHI, and the command to run it.

Source of truth: `backend/scripts/` (the module docstring at the top of each file is its own
manual), `backend/Dockerfile`, `backend/.dockerignore`, `backend/pyproject.toml`.

## How a script finds the app and its settings

| Where it runs | How `app` becomes importable | Where settings come from |
| --- | --- | --- |
| Inside the `api` container (the usual place on the server) | The image copies `backend/` to `/app` and sets `PYTHONPATH=/app` (`backend/Dockerfile`). `scripts/` is in the image; `tests/`, `uploads/` and `.env` are not (`backend/.dockerignore`). | The container environment set by `docker-compose.yml` (`x-backend-env`). |
| Inside a worker container | Same layout; `segment-worker` uses the `mrr-backend-classifier` image (torch installed), `summarize-worker` the same `mrr-backend-web` image as `api`. | Same as `api`. |
| On the host, from `backend/` | `app` is not installed as a package (`[tool.uv] package = false`). A script runs as a plain file (`uv run python scripts/x.py`) only if it adds the backend root to `sys.path` itself. `PYTHONPATH=. uv run python scripts/<path>.py` works for every script. Running as a module (`-m scripts.eval.x`) breaks the scripts that import a sibling by bare name (`segmentation_boundary_ab.py` imports `ab_stats` and `prompt_variants`). | Environment variables, then `.env` in the working directory. |

Any script that imports `app.config` or `app.db` builds `Settings`, which requires `DATABASE_URL`,
`SECRET_KEY` and `SECURITY_PASSWORD_SALT` to be set even when the script never opens the database
([Configuration reference](configuration.md)). Scripts that read `app.services.genai_metrics` or
`app.services.llm.pacing` also need Redis (`REDIS_URL`).

The column "Adds `sys.path`" below says whether the file can be run by path from the host.

`backend/pyproject.toml` sets `testpaths = ["tests"]`, so pytest never collects anything under
`scripts/`.

## Summary

| Script | Kind | Writes | Model calls | Adds `sys.path` | Tested by |
| --- | --- | --- | --- | --- | --- |
| `scripts/copy_records.py` | reusable, idempotent | DB rows + PDF files | no | yes | none |
| `scripts/backfill_doi.py` | one-off, idempotent | `summaries` text columns | yes, stage `doi`, one per summary | yes | `tests/test_backfill_doi.py` |
| `scripts/repair_dedup_clobbered_status.py` | one-off, idempotent | `documents.status` | no | yes | `tests/test_repair_dedup_status.py` |
| `scripts/migrate_from_sqlite.py` | one-shot, NOT idempotent | every table | no | yes | none |
| `scripts/dev/ocr_concurrency_hammer.py` | dev proof | nothing | no (OCR only) | no (does not import `app`) | none |
| `scripts/dev/verify_deposition_format.py` | dev proof | nothing | yes, stages `deposition` and `summarize` | no | none |
| `scripts/eval/job_health.py` | reusable report | nothing | no | yes | `tests/test_job_health.py` |
| `scripts/eval/vertex_stats.py` | reusable operator tool | Redis counters (`--reset` only) | no | no | none |
| `scripts/eval/pacer_watch.py` | reusable measurement | stdout (CSV) | no | no | none |
| `scripts/eval/corpus.py` | library | - | - | - | `tests/test_eval_corpus.py` |
| `scripts/eval/ab_stats.py` | library | - | - | - | `tests/test_ab_stats.py` |
| `scripts/eval/prompt_variants.py` | library (prompt texts) | - | - | - | none |
| `scripts/eval/date_in_source.py` | reusable measurement | nothing | no | own directory only | `tests/test_date_in_source.py` |
| `scripts/eval/date_label_check.py` | reusable measurement | nothing | no | own directory only | `tests/test_date_label_check.py` |
| `scripts/eval/date_vs_human_entries.py` | reusable measurement | nothing | no | no | `tests/test_date_vs_human_entries.py` |
| `scripts/eval/ocr_cap_word_recall.py` | measurement | nothing | no (OCR only) | yes | `tests/test_ocr_cap_word_recall.py` |
| `scripts/eval/classify_prompt_ab.py` | measurement | a JSON results file | yes, Gemini directly (arm `A`: stage `classify`) | yes | `tests/test_classify_prompt_ab.py` |
| `scripts/eval/rule_blast_radius.py` | measurement | nothing | no | yes | none |
| `scripts/eval/rule_removal_cost.py` | measurement | nothing | yes, Gemini directly | yes | none |
| `scripts/eval/segmentation_boundary_ab.py` | A/B harness | nothing | yes, stage `segment` | no | none (its `ab_stats` is tested) |
| `scripts/eval/segmentation_cap_ab.py` | A/B harness | nothing | yes, a full segmentation run | yes | none |
| `scripts/eval/window_duration_curve.py` | measurement | nothing | yes, Gemini directly | no | none |

"Gemini directly" means the script builds its own `google-genai` call instead of going through the
provider seam, so it goes to Gemini/Vertex whatever `LLM_BACKEND` says. "Stage `x`" means the call
goes through `backend/app/services/llm` `provider_for_stage()` and follows the configured backend
([Model providers](../explanation/model-providers.md)).

## Maintenance scripts (`backend/scripts/`)

### `copy_records.py`

| Field | Value |
| --- | --- |
| Purpose | Copy one user's documents, with their rows and the stored PDF, under another user, so a record whose reviewer corrections are the only ground truth can be reprocessed without destroying them. |
| Kind | Reusable. Idempotent: each copy writes an `audit_log` row (`action='copy'`, `detail='source=<uuid> source_user=<id> pages=<n>'`) and a later run skips any source that already has one. |
| Inputs | `--from-email` (required), `--to-email` (required), `--document-id` (repeatable; restricts to those documents), `--dry-run` (report counts only). Emails are matched exactly. |
| Reads | The source documents, the newest `segment` job with `state='done'` and its `segment_rows`, all `review_rows`, all `page_texts`, and the PDF. |
| Writes | For each document: the PDF copied to `<UPLOAD_FOLDER>/<target_user_id>/<new_uuid>.pdf` FIRST, then in one transaction a new `documents` row (file name prefixed `COPY-`), a clone of that job with `rq_job_id` cleared, its `segment_rows`, the `review_rows`, the `page_texts` and the `audit_log` marker. On any error the transaction rolls back and the copied file is removed. |
| Not copied | `summaries`, other jobs (including any `dedup` job), the source's `audit_log`. The copy keeps the source's `status`. |
| Skips | A document already copied to that target; a document with a job in `queued`, `running` or `paused`. |
| Model calls | None. |
| PHI | Reads and copies PHI. Prints document id prefixes, page counts and row counts only, never file names. |
| Column coverage | `_clone()` copies every mapped column, so a new column travels automatically unless it is added to that call's `exclude` set. |
| Run | `docker compose exec -T api python scripts/copy_records.py --from-email <source-email> --to-email <target-email> --dry-run` |

Summarizing a copy needs a duplicate check on it first (or the audited skip), because no `dedup`
job travels with it.

### `backfill_doi.py`

| Field | Value |
| --- | --- |
| Purpose | Re-read each stored summary's date of injury from only its own pages and rewrite the leading `**DOI**` prefix of `summaries.text`, `verified_text` and `edited_text`, removing it when the pages state none. |
| Kind | One-off. Idempotent: a second run changes nothing. |
| Inputs | Exactly one scope: `--user-email EMAIL` (matched case-insensitively), `--document-id ID` (repeatable; unknown ids are refused), or `--all`. Optional `--dry-run`. |
| Reads | `summaries` in scope, `documents.stored_path`, the PDF pages `row_start`..`row_end` of each summary. |
| Writes | `summaries.text`, `verified_text`, `edited_text` (and so `summaries.updated_at`). Nothing with `--dry-run`. |
| Model calls | One per summary in scope, through `backend/app/services/summary_doi.py` `extract_injury_date(..., strict=True)`, resolved for stage `doi` (so the backend follows `LLM_BACKEND` / `LLM_BACKEND_OVERRIDES`). `--dry-run` makes the same calls; it only skips the write. |
| Failure handling | A summary whose read fails is skipped and printed, never rewritten. If reads failed and nothing changed, the run rolls back and exits non-zero. |
| PHI | Sends PHI pages to the configured model. Prints document ids and counts. |
| Run | `docker compose exec -T api python scripts/backfill_doi.py --user-email <email> --dry-run` (from a checkout: `cd backend && uv run python scripts/backfill_doi.py ...`) |

### `repair_dedup_clobbered_status.py`

| Field | Value |
| --- | --- |
| Purpose | Restore `documents.status` on records where a duplicate check overwrote a finished status with `reviewing` (before the change that made `dedup` jobs leave status alone). |
| Kind | One-off. Idempotent: a repaired document is no longer `reviewing`. |
| Inputs | Exactly one scope: `--user-email` (matched exactly), `--document-id` (repeatable), `--all`. Optional `--dry-run`. |
| Guard | A document is changed only when all hold: status is `reviewing`; its newest job is `dedup`; its newest non-dedup job is `summarize` in state `done` or `needs_attention`; it has at least one stored summary. The status is set to that job's state name. |
| Writes | `documents.status` only. |
| Model calls | None. |
| PHI | Prints document id prefixes, statuses and summary counts. |
| Run | `docker compose exec -T api python scripts/repair_dedup_clobbered_status.py --all --dry-run` |

### `migrate_from_sqlite.py`

| Field | Value |
| --- | --- |
| Purpose | Copy the retired Flask app's SQLite database into an empty Postgres schema, preserving values (password hashes, `fs_uniquifier`, JSON, datetimes). |
| Kind | One-shot, NOT idempotent. Run only after `alembic upgrade head` on an EMPTY database. |
| Inputs | `--sqlite PATH` (default `../instance/mrr.db`, relative to the working directory). |
| Reads | A temporary copy of the SQLite file (the original is never opened). |
| Writes | Every table present in both schemas, in foreign-key order (`Base.metadata.sorted_tables`), only the columns both schemas have; then sets each integer `id` sequence to `MAX(id)`. All in one transaction. Tables absent from the source (for example `access_token`) are skipped. |
| Model calls | None. |
| PHI | Copies every table, PHI included. Prints table names and row counts. |
| Run (host) | `cd backend && uv run python scripts/migrate_from_sqlite.py` |
| Run (container) | `docker compose exec -T api python scripts/migrate_from_sqlite.py --sqlite /app/instance/mrr.db` (compose mounts `./instance` read-only at `/app/instance`; the default path does not resolve from `/app`) |

## Developer proofs (`backend/scripts/dev/`)

### `ocr_concurrency_hammer.py`

| Field | Value |
| --- | --- |
| Purpose | Reproduce the concurrent-Tesseract deadlock that `OMP_THREAD_LIMIT=1` / `OMP_NUM_THREADS=1` in `docker-compose.yml` prevents. |
| Kind | Dev proof, re-runnable. |
| Inputs | One positional PDF path. Without it, the first `*.pdf` directly inside `/app/uploads` (not in its per-user subfolders). |
| Load | Constants `THREADS=6`, `TASKS=60`, `PAGES=8`, `DPI=120`, `WAIT_SECONDS=90`: rasterizes the first 8 pages and runs 60 `pytesseract.image_to_string` calls on 6 threads. |
| Output | The two OMP variables, then `<done>/60 OCRs in <secs>s` (with a `TIMED OUT` line on a deadlock); exits through `os._exit(0)`. |
| Writes | Nothing. |
| Model calls | None. Reads one PDF (PHI); prints no page text. |
| Run (fixed, expect 60/60) | `docker compose exec segment-worker python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<user_id>/<document_id>.pdf` |
| Run (deadlock repro) | `docker compose exec -e OMP_THREAD_LIMIT= -e OMP_NUM_THREADS= segment-worker python scripts/dev/ocr_concurrency_hammer.py /app/uploads/<user_id>/<document_id>.pdf` |
| Afterwards | `docker compose restart segment-worker` clears stuck `tesseract` processes (the image has no `pkill`). |

### `verify_deposition_format.py`

| Field | Value |
| --- | --- |
| Purpose | Summarize ONE real deposition sub-document and print only the structure of the result: paragraph count, how many paragraphs open with a page range, the cited page numbers, and whether consecutive paragraphs step by the group size (`_GROUP_PAGES`, ten pages, pinned to the prompt by a test). |
| Kind | Dev proof. |
| Inputs | Positional `<document_id>` and `<row_start>`. `<row_start>` must be the first page of an existing `review_rows` row of that document (the live row, not a summary's `row_start` snapshot). |
| Writes | Nothing (it calls `summarize_row()` directly, which is database-free). |
| Model calls | `backend/app/services/deposition_pages.py` `transcript_page_offset()` (stage `deposition`) and `backend/app/services/summarize_engine.py` `summarize_row()` (stage `summarize`: body, title, and the audit when enabled). |
| PHI | Sends a real deposition to the model. Prints counts, page numbers and each paragraph's opening page-range phrase only. |
| Run | `docker compose exec -T api python scripts/dev/verify_deposition_format.py <document_id> <row_start>` |

## Evaluation and measurement (`backend/scripts/eval/`)

These read real records on the machine that holds them. Their docstrings record the measurements
they were written for.

### `corpus.py`

Library, no entry point. The corpus rule for every pooled count: one row per distinct
`documents.sha256`, keeping the earliest copy by `(created_at, id)`. Functions:
`canonical_document_ids()`, `one_copy_per_document()`, `one_copy_per_pdf()`. Used by
`date_in_source.py`, `date_label_check.py`, `rule_blast_radius.py`, `rule_removal_cost.py`.
Scripts that pool counts offer `--all-copies` to count every uploaded copy instead.

### `ab_stats.py`

Library, no entry point, no app imports. Pure aggregation for the segmentation boundary A/B:
totals only over documents every arm finished, and a gap is a difference only when it exceeds each
arm's own run-to-run spread. Imported by `segmentation_boundary_ab.py`.

### `prompt_variants.py`

Library. Whole historical `SEGMENTATION_PROMPT` texts, verbatim from git, used as A/B arms:
`DATE_TITLE_104`, `ENCOUNTER_DATE`. Imported by `segmentation_boundary_ab.py`.

### `job_health.py`

| Field | Value |
| --- | --- |
| Purpose | Pipeline health by job outcome. Prints the naive "not done" rate beside the failure rate over finished jobs, using the outcome taxonomy in `backend/app/worker/failures.py` (`job_outcome()`, `JOB_OUTCOMES`, `is_failure()`). |
| Inputs | `--kind KIND`, `--since YYYY-MM-DD`, `--build SHA_PREFIX` (matches `jobs.build_sha`), `--by-kind` (one report per kind as well). |
| Reads / writes | Reads `jobs.state`, `error`, `kind`; writes nothing. |
| Model calls | None. |
| PHI | Prints counts, states and the app's own error messages; lists raw error strings that did not come from `user_facing_message` (API errors, not record content). |
| Run | `docker compose exec -T api python scripts/eval/job_health.py --by-kind` |

### `vertex_stats.py`

| Field | Value |
| --- | --- |
| Purpose | Read, reset or watch the per-model call counters that `backend/app/services/genai_metrics.py` keeps in Redis (keys `vertex:metrics:*`): attempts, accepted, 429, 5xx, transport errors, rejection percentage, limiter wait. |
| Inputs | none (print totals), `--reset` (delete every counter), `--watch SECONDS` (print the delta each interval until Ctrl-C). |
| Writes | Redis only, and only with `--reset`. No database access, no model calls. |
| Run | `docker compose exec -T api python scripts/eval/vertex_stats.py` |

### `pacer_watch.py`

| Field | Value |
| --- | --- |
| Purpose | Sample the pacer's current send rate (`backend/app/services/llm/pacing.py` `snapshot()`) beside the per-interval accepted and rejected counts from `genai_metrics`, as CSV, so rejection rate can be plotted against send rate. |
| Inputs | `--interval` seconds (default 5), `--minutes` (default 60). |
| Output | CSV on stdout: `elapsed_s,model,req_rpm,tok_rpm,accepted,rejected,p,wait_ms`. `p` is blank for an interval with no calls; rates are blank when the meter has no stored rate. |
| Writes | Nothing. Changes no pacer state; safe during a live job. |
| Run | `docker compose exec -T api python scripts/eval/pacer_watch.py --interval 5 --minutes 60 > pacer.csv` |

### `date_in_source.py`

| Field | Value |
| --- | --- |
| Purpose | Check whether each review row's `date` appears in that row's stored page text. Buckets, in the order `classify_date()` tests them: no date, in the row's pages, within the margin, elsewhere in the document, in a category not summarized by default, only the day differs, absent. Reports the defect as a range. |
| Inputs | `--user N` (owner id), `--margin N` (pages either side counted as adjacent; default 2), `--all-copies`. |
| Reads | `review_rows`, `documents`, `page_texts`, the category catalog. Rows with any page missing from `page_texts` are skipped and counted. |
| Writes / model calls | None. |
| PHI | Prints counts only. |
| Run | `docker compose exec -T api python scripts/eval/date_in_source.py` |

### `date_label_check.py`

| Field | Value |
| --- | --- |
| Purpose | For rows whose pages carry labelled dates (`LABELS`: encounter date, signature date, print or fax date, date of injury), report which labelled date the row's `date` matched. Buckets: took the labelled encounter date, the signature date, a print or fax date, the date of injury; date not next to any label; no labels on these pages; no date to check. |
| Inputs | `--user N`, `--all-copies`. |
| Writes / model calls | None. |
| PHI | Prints counts and dates. |
| Run | `docker compose exec -T api python scripts/eval/date_label_check.py` |

### `date_vs_human_entries.py`

| Field | Value |
| --- | --- |
| Purpose | Compare one record's review rows with the date-led entries of the human-written report for the same record: delivered, excluded, absent (split into re-dated and unfound), extra. |
| Inputs | `--document ID_OR_PREFIX` (required), `--human PATH` (required; the human report as text, tab-separated date-led entries). Refuses to report when the file has no date-led entries. |
| Writes / model calls | None. |
| PHI | The human report is PHI: copy it into the container for the run and delete it afterwards, never into the repository or `uploads/`. Prints counts, dates, page ranges and category ids. |
| Run | `docker compose exec -T api python scripts/eval/date_vs_human_entries.py --document <document_id> --human /tmp/<report>.txt` |

### `ocr_cap_word_recall.py`

| Field | Value |
| --- | --- |
| Purpose | Word-level recall and precision of OCR at candidate `OCR_MAX_LONG_EDGE_PX` caps against an uncapped render, scored only on pages the cap actually shrinks. Leads with the worst page. |
| Inputs | `--pdf PATH` (required), `--caps` (comma list, default `3500,6500`), `--pages` (binding pages to score, default 20). |
| Needs | Poppler and Tesseract; `DATABASE_URL` must parse although the database is never opened. |
| Writes / model calls | None. |
| PHI | Reads one PDF. Prints counts, ratios, page numbers, word lengths and timings, never words. |
| Run | `docker compose exec -T api python scripts/eval/ocr_cap_word_recall.py --pdf /app/uploads/<user_id>/<document_id>.pdf` |

### `classify_prompt_ab.py`

| Field | Value |
| --- | --- |
| Purpose | A/B the classification prompt against reviewer corrections (a `review_rows.category` that differs from the `segment_rows.category` for the same pages). Arms `A` (production `llm_classify()`), `A-prime` (this script's call path with production's instructions, a control), `B` and `B-null`. Excludes rows a rule decides and rows with no stored page text. |
| Inputs | `--user-email` (required), `--repeats` (default 3), `--limit N`, `--only-ambiguous`, `--uncorrected N` (sample rows the reviewer left alone instead), `--model` (default `CLASSIFY_MODEL`), `--out` (default `classify_prompt_ab.json` in the working directory; `/app/instance` is read-only). |
| Checks | Before running, exits if its copy of production's instructions no longer matches `classification.llm_classify` (`check_no_drift()`, `check_prompt_parity()`). |
| Writes | The `--out` JSON (report plus per-row answers; page text removed). Nothing in the database. |
| Model calls | rows x arms x repeats. Arm `A` calls production `llm_classify()` (stage `classify`); the other arms call `google-genai` directly with the same client and retry wrapper production used when the script was written. Sends stored page text (PHI). |
| Run | `docker compose exec -T api python scripts/eval/classify_prompt_ab.py --user-email <email> --repeats 3 --out /tmp/ab.json` |

### `rule_blast_radius.py`

| Field | Value |
| --- | --- |
| Purpose | For the classification rule that maps progress-report titles to category 1, count which alternative in the rule claims each row corpus-wide, and how many of those rows a reviewer corrected. |
| Inputs | `--all-copies`. |
| Reads | `documents`, the newest done `segment` job's rows, `review_rows` titles; `classification._RULES` and `_ADMIN_RULES`. |
| Writes / model calls | None. Regex over stored titles. |
| Run | `docker compose exec -T api python scripts/eval/rule_blast_radius.py` |

### `rule_removal_cost.py`

| Field | Value |
| --- | --- |
| Purpose | Estimate the cost of removing one alternative (default `progress note`) from that rule: on rows the reviewer left alone, ask classifier arms A and B what they would answer without the rule and score agreement with the accepted category. |
| Inputs | `--user-email` (required), `--term` (default `progress note`), `--sample` (default 40), `--repeats` (default 3), `--all-copies`. |
| Writes | Nothing. |
| Model calls | sample x arms x repeats, through `classify_prompt_ab.call()` (`google-genai` directly). Sends stored page text (PHI). |
| Run | `docker compose exec -T api python scripts/eval/rule_removal_cost.py --user-email <email>` |

### `segmentation_boundary_ab.py`

| Field | Value |
| --- | --- |
| Purpose | Re-run segmentation offline with different prompt arms and score the boundaries against reviewer-corrected `review_rows`; reports over-split and under-split separately. |
| Inputs | `--list` (list documents with corrected boundaries), `--docs` (comma-separated ids), `--arms` (default `control,merge_biased`; also `evidence_gated`, `date_title`, `encounter_date`), `--stage windows` or `full` (adds the boundary verify pass), `--repeats N` (default 1). |
| Writes | Nothing in the database: it rebinds `segment_engine.SEGMENTATION_PROMPT` in its own process and never uses the worker path. |
| Model calls | One per window per document per arm per repeat, through `segment_engine._window_rows()` (stage `segment`); `--stage full` adds the boundary verify pass. Sends PDF pages (PHI). |
| Run | `docker compose exec -T api python scripts/eval/segmentation_boundary_ab.py --list` |

### `segmentation_cap_ab.py`

| Field | Value |
| --- | --- |
| Purpose | Score production `run_segmentation()` against a hand-labelled gold case with different `window_max_pages` caps (default arms `10000`, which never binds, and `100`). |
| Inputs | Positional `[case_id]` (default `Case 3`) and `[caps]` (comma list). |
| Needs | The labelled cases loaded by `experiments/a1-segmentation/src/cases.py`, which locates them through a machine-specific directory in `experiments/a1-segmentation/src/config.py`; Vertex credentials; the `classifier` extra. |
| Writes | Nothing. |
| Model calls | A full segmentation run per arm. |
| PHI | Prints span counts, timings and metrics only. |
| Run (host) | `cd backend && DATABASE_URL=postgresql+psycopg://x:y@127.0.0.1:5432/unused SECRET_KEY=x SECURITY_PASSWORD_SALT=x uv run --extra classifier --env-file ../.env python scripts/eval/segmentation_cap_ab.py` |

### `window_duration_curve.py`

| Field | Value |
| --- | --- |
| Purpose | Time one segmentation vision call on the first N pages of one document for a ladder of N, to set `window_max_pages` from a measured curve. Calls `google-genai` with a 600 s client deadline, bypassing the retry wrapper and the pacer. |
| Inputs | Positional `<document_id>` and optional ladder (default `20,40,80,120,160,200,241`, capped at the document's page count). |
| Writes | Nothing. Creates no job. |
| Model calls | One per ladder rung. Sends PDF pages (PHI). |
| PHI | Prints page counts, payload sizes, timings, token counts and reply length. |
| Run | `docker compose exec -T api python scripts/eval/window_duration_curve.py <document_id>` |

> **Note:** most `eval/` scripts import private names from the app (for example `segment_engine._window_rows`, `segment_engine._escalation_text`, `classification._RULES`, `classification._ADMIN_RULES`) and several have no test. A refactor of those modules can stop a script working without failing CI; run it against the current tree before quoting its numbers.

## Related pages

- [How to diagnose a stuck or failed job](../how-to/diagnose-a-stuck-or-failed-job.md) (`job_health.py`)
- [Data model reference](data-model.md)
- [Categorization](../explanation/categorization.md)
- [Segmentation](../explanation/segmentation.md)
- [OCR and page text](../explanation/ocr-and-page-text.md)

<!-- reviewed: 2026-09-30 -->
