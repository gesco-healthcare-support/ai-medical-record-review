# Configuration reference

Every backend setting: its environment variable, type, defaults in code and in Compose, whether it
reaches a container, how it is validated, and what it affects.

Source of truth: `backend/app/config.py` (`Settings` and its methods) and the `x-backend-env` block
of `docker-compose.yml`. Why the two can disagree is explained in
[Configuration model](../explanation/configuration-model.md).

Column key:

- **Env var**: the variable `Settings` reads. It is the field name uppercased, except
  `GOOGLE_GENAI_USE_VERTEXAI` (field `use_vertex`, read through a `validation_alias`).
- **Code default**: the field default in `config.py`. "derived" means `Settings._derive()` fills it
  when empty.
- **Compose default**: the fallback in `${VAR:-default}` in `docker-compose.yml`, "fixed" when
  compose passes a literal value, "required" for `${VAR:?...}`, and "-" when compose does not name
  the variable.
- **Container**: "yes" when compose passes the variable to `api`, `segment-worker` and
  `summarize-worker`; "no" means a container always runs the code default.
- **Validation**: checks in `Settings._derive()` and its helpers, or at the point of use. No field
  carries pydantic `Field` constraints.

90 settings in 14 groups.

## Runtime and platform

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `ENVIRONMENT` | str | `dev` | `dev` | yes | Only the exact value `prod` is special | `prod` marks the session cookie `Secure` (`backend/app/auth/backend.py` `_cookie_transport()`) and enables three boot guards: Vertex required, OpenAI Zero Data Retention acknowledgement, vLLM origin allowlist |
| `BUILD_SHA` | str | `unknown` | - (set in the image: `backend/Dockerfile` `ENV BUILD_SHA=$GIT_SHA`) | no (image) | none | Stamped on every job row (`backend/app/services/jobs.py` `create_job()`) |
| `DATABASE_URL` | str | required | fixed: `postgresql+psycopg://mrr:${POSTGRES_PASSWORD:-mrr_local_only}@postgres:5432/mrr` | yes | Missing: `Settings` fails to build | SQLAlchemy engines (`backend/app/db.py`); Alembic (`backend/alembic/env.py`) |
| `REDIS_URL` | str | `redis://localhost:6379/0` | fixed: `redis://redis:6379/0` | yes | none | Every Redis client via `backend/app/worker/queues.py` `get_redis()`: RQ queues, pacer, metrics, cancel flags, download records |
| `SECRET_KEY` | str | required | required | yes | Missing: `Settings` fails to build; Compose refuses an unset or empty value | Signs reset-password and verification tokens (`backend/app/auth/users.py` `UserManager`) |
| `SECURITY_PASSWORD_SALT` | str | required | required | yes | Missing: `Settings` fails to build; Compose refuses an unset or empty value | HMAC pre-hash applied before argon2 (`backend/app/auth/password.py` `MrrPasswordHelper`) |
| `UPLOAD_FOLDER` | str | `./uploads` | fixed: `/app/uploads` | yes | none | Where uploaded PDFs and prepared downloads are written (`backend/app/api/documents.py`, `backend/app/services/downloads.py`) |
| `DOWNLOAD_TTL_SECONDS` | int | `300` | - | no | none | Seconds a prepared export stays downloadable before its file is deleted (`backend/app/services/downloads.py`) |
| `DOWNLOAD_WATCH_SECONDS` | int | `900` | - | no | none | Seconds a download's delivery record is kept for the page to watch (`backend/app/services/downloads.py`) |
| `TESSERACT_CMD` | str | `""` | - (excluded on purpose) | no | none | Tesseract binary path for a host-run backend (`backend/app/services/ocr.py`); empty means `PATH` |

## Gemini and Vertex connection

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `GOOGLE_GENAI_USE_VERTEXAI` | bool | `False` | `true` | yes | Must be true when `ENVIRONMENT=prod`, whatever the backends | Vertex AI client versus Developer API client (`backend/app/services/genai_client.py` `get_genai_client()`); selects the derived `GENAI_MODEL` |
| `GEMINI_API_KEY` | str | `""` | - | no | none | API key for Vertex when `GOOGLE_CLOUD_PROJECT` is empty, or for the Developer API when Vertex is off |
| `GOOGLE_CLOUD_PROJECT` | str | `""` | `""` | yes | none | When set with Vertex, the client uses Application Default Credentials for this project |
| `GOOGLE_CLOUD_LOCATION` | str | `global` | `global` | yes | none | Vertex location used with `GOOGLE_CLOUD_PROJECT` |

## Backend routing

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `LLM_BACKEND` | str | `gemini` | `gemini` | yes | Stripped and lowercased; must be `gemini`, `openai` or `vllm` | Backend for every stage that has no override (`Settings.backend_for()`) |
| `LLM_BACKEND_OVERRIDES` | str | `""` | `""` | yes | Comma-separated `stage=backend`; each half stripped and lowercased; an entry without `=`, an unknown stage or an unknown backend refuses startup | Per-stage backend; for a stage listed twice the first entry wins. Stages: `summarize`, `segment`, `extract`, `dedup`, `classify`, `verify`, `doi`, `deposition` |
| `SUMMARY_PROVIDER` | str | `gemini` | `gemini` | yes | Stripped and lowercased; only `openai` changes behaviour | `openai` sends the summarize stage to OpenAI through `get_provider()` whatever `backend_for("summarize")` says, turns on the OpenAI guards, and skips the Gemini summarize defaults |

## Model selection

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `GENAI_MODEL` | str | derived: `gemini-2.5-flash` when `GOOGLE_GENAI_USE_VERTEXAI` is true, else `gemini-flash-latest` | `""` | yes | Model-name check | Gemini model for `segment`, `extract`, `doi`, `deposition`; default for `VERIFY_MODEL`; stamped as `jobs.model` on segment and classify jobs |
| `SUMMARY_MODEL` | str | derived: `gemini-3.5-flash` | `""` | yes | none | Seeds `SUMMARY_BODY_MODEL` on the Gemini summarize path |
| `VERIFY_MODEL` | str | derived: `GENAI_MODEL` | `""` | yes | Model-name check | Gemini model for `verify` |
| `CLASSIFY_MODEL` | str | `gemini-2.5-flash-lite` | `gemini-2.5-flash-lite` | yes | Model-name check | Gemini model for `classify` and `dedup`; stamped as `jobs.model` on dedup jobs |
| `SUMMARY_BODY_MODEL` | str | derived: `SUMMARY_MODEL` when summarize is on Gemini; `VLLM_MODEL` when on vLLM; no default on OpenAI | `""` | yes | Required when OpenAI is reachable; model-name check | Summary body model (`Settings.model_for("body")`), persisted as `jobs.model` when a summarize job is created |
| `SUMMARY_TITLE_MODEL` | str | derived: `gemini-2.5-flash` on Gemini; `VLLM_MODEL` on vLLM; no default on OpenAI | `""` | yes | Required when OpenAI is reachable; model-name check | Summary title model, persisted as `jobs.title_model` |
| `AUDIT_MODEL` | str | derived: `gemini-2.5-flash` on Gemini; `VLLM_MODEL` on vLLM; no default on OpenAI | `""` | yes | Required when OpenAI is reachable; model-name check | Summary audit model, persisted as `jobs.audit_model` |
| `SUMMARY_BODY_FALLBACK_MODEL` | str | derived on the Gemini summarize path only: empty becomes `gemini-3.5-flash`; `none` or `off` becomes empty (disabled) | `""` | yes | Not model-name checked | Model the body call is re-sent to once when the body model's call raises a 429 (`backend/app/services/summarize_engine.py` `_generate_body()`) |

The model-name check (`Settings._validate_model_names_match_their_backend()`) refuses startup when
a resolved model starting `gemini-` is routed to a non-Gemini backend, or when a name containing `/`
and not containing `gemini` is routed to Gemini.

## Thinking

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `GEMINI_THINKING_BUDGET` | int | `0` | `0` | yes | none (`-1` is dynamic) | Gemini thinking budget for `extract`, `dedup`, `classify`, `verify` (`Settings.thinking_for()`); also applied by `backend/app/services/genai_retry.py` `_apply_thinking_default()` to a request with no thinking config |
| `SEGMENT_THINKING_BUDGET` | int | `-1` | `-1` | yes | none | Gemini thinking budget for `segment` |
| `SUMMARY_THINKING_BUDGET` | int | `-1` | `-1` | yes | none | Gemini thinking budget for `summarize` (body, title, audit), `doi`, `deposition` |

vLLM thinking is `VLLM_THINKING_STAGES` in the vLLM group. OpenAI takes no thinking setting.

## Retries, deadlines and pacing

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `GENAI_MAX_RETRIES` | int | `8` | `8` | yes | none | Total attempts (not retries) per logical model call, on all three backends |
| `GENAI_RETRY_BASE_DELAY` | float | `2.0` | `2.0` | yes | none | Backoff base in seconds; each wait is uniform in `[0, min(GENAI_RETRY_MAX_DELAY, base x 2^attempt)]` |
| `GENAI_RETRY_MAX_DELAY` | float | `30.0` | `30` | yes | none | Backoff ceiling in seconds; also caps a server-advised delay |
| `GENAI_HTTP_TIMEOUT_MS` | int | `120000` | `120000` | yes | none | Gemini client timeout and the floor of the per-request deadline, which google-genai forwards to Vertex as the server deadline; OpenAI client timeout (divided by 1000, in seconds) |
| `GENAI_TIMEOUT_PER_1K_TOKENS_MS` | int | `3800` | - | no | none; uncapped by design | Scales the Gemini deadline: `max(GENAI_HTTP_TIMEOUT_MS, estimated tokens x value / 1000)` (`Settings.effective_genai_timeout_ms()`) |
| `GENAI_DEADLINE_RETRY_MULTIPLIER` | float | `2.5` | - | no | A value that does not raise the deadline (1 or less) disables the retry | Deadline of the single retry after a Gemini 504, as a multiple of that call's deadline |
| `VERTEX_MAX_RPM` | int | `60` | `60` | yes | 0 or less turns that meter off | Pacer request ceiling (per minute) for `gemini` and any provider other than `openai` and `vllm` |
| `VERTEX_MAX_TPM` | int | `4000000` | `4000000` | yes | 0 or less turns that meter off | Pacer token ceiling (per minute) for the same providers |

## OpenAI

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `OPENAI_API_KEY` | str | `""` | `""` | yes | Required when `SUMMARY_PROVIDER=openai` or any stage resolves to `openai` | OpenAI client key (`backend/app/services/llm/openai.py` `_client()`) |
| `OPENAI_ZDR_ACKNOWLEDGED` | bool | `False` | `false` | yes | Must be true when `ENVIRONMENT=prod` and OpenAI is reachable | Boot guard only |
| `OPENAI_MAX_RPM` | int | `5000` | - | no | 0 or less turns that meter off | Pacer request ceiling for `openai`; a cold-start bound that `x-ratelimit-*` headers replace after the first call |
| `OPENAI_MAX_TPM` | int | `1000000` | - | no | 0 or less turns that meter off | Pacer token ceiling for `openai`; same cold-start role |

## vLLM

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `VLLM_BASE_URL` | str | `""` | `""` | yes | Required when any stage resolves to `vllm`; in production its origin must be `http://127.0.0.1:8000` or `http://localhost:8000` (`_APPROVED_VLLM_ORIGINS`); must parse as a URL for the preflight | OpenAI-compatible base URL, normally ending in `/v1`; its `scheme://host:port` root serves `GET /version` for the preflight |
| `VLLM_API_KEY` | str | `""` | `""` | yes | none | Client API key; empty sends the placeholder `not-required-by-vllm` |
| `VLLM_MODEL` | str | `""` | `Qwen/Qwen3.6-35B-A3B-FP8` | yes | Required when any stage resolves to `vllm` | Model for every non-summarize stage on vLLM (`Settings.model_for_stage()`); default for the summarize triple when summarize is on vLLM |
| `VLLM_READ_TIMEOUT_S` | float | `600.0` | `600.0` | yes | none | Client read timeout in seconds (connect timeout is fixed at 10 s); a read timeout is not retried |
| `VLLM_MAX_RPM` | int | `0` | `0` | yes | 0 or less turns that meter off | Pacer request ceiling for `vllm`; both at 0 admits every call at once |
| `VLLM_MAX_TPM` | int | `0` | `0` | yes | 0 or less turns that meter off | Pacer token ceiling for `vllm` |
| `VLLM_MIN_VERSION` | str | `0.24.0` | `0.24.0` | yes | Compared at boot with the server's `GET /version` by leading numeric components; lower refuses startup | Version floor enforced by `backend/app/services/llm/preflight.py` `assert_backends_ready()` |
| `VLLM_APPROVED_ORIGINS` | str | `""` | `""` | yes | Comma-separated origins | Read only by `Settings._approved_vllm_origins()`, which returns the code constant in production; outside production no origin check runs |
| `VLLM_MAX_IMAGES_PER_PROMPT` | int | `40` | `40` | yes | Each vLLM stage's image cap must not exceed it | Must equal the pod's `--limit-mm-per-prompt`; the boot guard compares `SUMMARY_IMAGE_MAX_PAGES`, `VLLM_SEGMENT_MAX_PAGES` and the DOI (10) and deposition (6) caps against it for stages on vLLM |
| `VLLM_SEGMENT_MAX_PAGES` | int | `30` | `30` | yes | Must not exceed `VLLM_MAX_IMAGES_PER_PROMPT` when `segment` is on vLLM | Pages per segmentation window on vLLM (the smaller of this and `WINDOW_MAX_PAGES`) and the image cap for that window |
| `VLLM_THINKING_STAGES` | str | `""` | `""` | yes | Comma-separated exact stage names; an unknown name raises `ValueError` when a vLLM call is made, not at boot | Stages sent `enable_thinking: true` on vLLM (`Settings.vllm_thinking_for()`); every other stage is sent `false` |

## Summarize stage

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `SUMMARY_TEMPERATURE` | float | `0.0` | `0.0` | yes | none | Temperature of the body call (title and audit calls use 0.0) |
| `SUMMARY_MAX_OUTPUT_TOKENS` | int | `8192` | `8192` | yes | none | Output cap of the body and title calls, and of the audit when `AUDIT_MAX_OUTPUT_TOKENS` is unset |
| `SUMMARY_TRUNCATION_RETRY_MULTIPLIER` | float | `2.0` | `2.0` | yes | 1 or less disables | A body cut off at the cap is re-asked once at cap x this value |
| `AUDIT_MAX_OUTPUT_TOKENS` | int or None | `None` | - | no | none | Output cap of the audit call; `None` uses `SUMMARY_MAX_OUTPUT_TOKENS` |
| `SUMMARY_VERIFY` | bool | `True` | `true` | yes | none | Runs the faithfulness audit on each summary (`summarize_row()` default) |
| `SUMMARY_MULTIMODAL` | bool | `True` | `true` | yes | none | Sends page images with the OCR text in the body call |
| `SUMMARY_IMAGE_MAX_PAGES` | int | `15` | `15` | yes | Must not exceed `VLLM_MAX_IMAGES_PER_PROMPT` when `summarize` is on vLLM | Page images per body call |
| `SUMMARY_IMAGE_DPI` | int | `120` | `120` | yes | When `doi` or `deposition` is on vLLM, must not cap that stage's render below its long-edge target | DPI ceiling for every rasterised page image (`backend/app/services/rasterise.py`) |
| `SUMMARY_IMAGE_LONG_EDGE_PX` | int | `1024` | - | no | none | Default long-edge pixel target for rasterised page images |
| `PIPELINE_WORKERS` | int | `5` | `5` | yes | none | Rows summarized concurrently inside one summarize job (`backend/app/worker/tasks.py`) |
| `SUMMARIZE_GIVEUP_AFTER_FAILURES` | int | `3` | `3` | yes | none | Transient row failures with zero successes that end a summarize job instead of pausing it |
| `SUMMARIZE_PAUSE_AFTER` | int | `3` | - | no | none | Consecutive transient failures that pause a summarize run for auto-resume |
| `SUMMARIZE_RESUME_DELAY` | int | `60` | - | no | none | Seconds before a paused summarize run resumes |
| `BUNDLE_SUMMARIZE_CAP` | int | `40` | - | no | none | Maximum matched rows for the synchronous bundle summarize route; more returns 409 (`backend/app/api/documents.py`) |

## Injury-date and transcript page reads

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `DOI_MAX_OUTPUT_TOKENS` | int | `2048` | `2048` | yes | none | Output cap of the injury-date read; a truncated reply raises instead of returning "-" |
| `DOI_IMAGE_LONG_EDGE_PX` | int | `1300` | `1300` | yes | Must be reachable under `SUMMARY_IMAGE_DPI` when `doi` is on vLLM | Long-edge pixel target of the injury-date read's page images on vLLM |
| `DEPOSITION_MAX_OUTPUT_TOKENS` | int | `2048` | `2048` | yes | none | Output cap of the transcript page-number read; a truncated reply raises `TranscriptPagesUnreadableError` |
| `DEPOSITION_IMAGE_LONG_EDGE_PX` | int | `1300` | `1300` | yes | Must be reachable under `SUMMARY_IMAGE_DPI` when `deposition` is on vLLM | Long-edge pixel target of the transcript read's page images on vLLM |
| `DOI_WORKERS` | int | `4` | - | no | none | Threads for the injury-date reads at the end of segmentation (`backend/app/services/segment_engine.py`) |

## Segmentation and boundary verify

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `WINDOW_BUDGET_MB` | float | `12.5` | `12.5` | yes | none | Raw PDF megabytes packed into one segmentation window on Gemini; not used on vLLM |
| `WINDOW_OVERLAP` | int | `30` | `30` | yes | none | Pages of overlap between consecutive windows |
| `WINDOW_MAX_PAGES` | int | `100` | `100` | yes | none | Maximum pages in one segmentation window |
| `SEGMENT_WINDOW_WORKERS` | int | `3` | `3` | yes | none | Windows sent concurrently |
| `CLASSIFY_WORKERS` | int | `4` | `4` | yes | none | Threads for categorization and for the boundary verify pass |
| `VERIFY_MERGE` | bool | `True` | `true` | yes | none | Runs the boundary verify pass at the end of segmentation (`segment_engine.run_segmentation()`); false skips it |
| `VERIFY_USE_TEXT` | bool | `True` | `true` | yes | none | Adds OCR text from the two boundary pages to the verify prompt |
| `VERIFY_SUSPECT_CAP` | int | `200` | `200` | yes | none | Maximum boundaries checked per record (`verify_pass.suspect_indices()`) |
| `VERIFY_TRIGGERED_ONLY` | bool | `False` | `false` | yes | none | Checks only the boundaries the trigger heuristic selects |

## Duplicate detection

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `DUPE_JACCARD_THRESHOLD` | float | `0.70` | `0.70` | yes | none | Word-set similarity cut of the candidate finder (`backend/app/services/dedup.py`) |
| `DUPE_SIMILARITY_OVERRIDE` | float | `0.90` | `0.90` | yes | none | Date-masked text similarity at which rows with different dates can still be treated as copies (cross-date admission and the gate's escape hatch) |
| `DUPE_MODEL_OVERRIDE` | float | `0.95` | `0.95` | yes | none | Similarity at or above which the confirm model call is skipped (`backend/app/worker/tasks.py`) |

## OCR and page text

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `OCR_TIMEOUT_SECONDS` | int | `120` | - | no | none | Per-call Tesseract timeout (`backend/app/services/ocr.py`) |
| `OCR_BASE_DPI` | int | `200` | - | no | none | Rasterisation DPI for OCR |
| `OCR_MAX_LONG_EDGE_PX` | int | `0` | - | no | 0 disables | When set, lowers the OCR DPI so a page's long edge fits; never raises it |
| `PAGE_TEXT_WORKERS` | int | `6` | `6` | yes | none | Threads for the one-time per-page OCR pass (`backend/app/services/page_text.py`) |

## Job lifecycle

| Env var | Type | Code default | Compose default | Container | Validation | Affects |
| --- | --- | --- | --- | --- | --- | --- |
| `JOB_TIMEOUT` | int | `3600` | `3600` | yes | none | Floor of the RQ wall-clock cap: `max(JOB_TIMEOUT, pages x JOB_TIMEOUT_PER_PAGE)` (`Settings.effective_job_timeout()`) |
| `JOB_TIMEOUT_PER_PAGE` | float | `20.0` | `20.0` | yes | none | Per-page part of the same cap |
| `FUTURE_TIMEOUT_MARGIN_SECONDS` | int | `120` | - | no | none | Subtracted from the job cap to bound every thread-pool drain: `max(1, cap - margin)` (`Settings.pool_timeout()`) |
| `JOB_CANCEL_GRACE_SECONDS` | int | `10` | `10` | yes | none | Seconds the UI waits for a cooperative stop before offering Force stop (returned by the cancel route); the cancel flag's Redis TTL is `max(60, value x 60)` (`backend/app/worker/cancel.py` `request_cancel()`) |

## Variables compose passes that are not settings

| Variable | Compose value | Read by |
| --- | --- | --- |
| `GOOGLE_APPLICATION_CREDENTIALS` | `${GOOGLE_APPLICATION_CREDENTIALS:-}` | Google Application Default Credentials inside google-genai; `.env.example` points it at `/secrets/vertex-sa.json`, which compose mounts read-only from `./secrets` |
| `OMP_THREAD_LIMIT` | fixed `"1"` | Tesseract / OpenMP; stops concurrent tesseract processes deadlocking |
| `OMP_NUM_THREADS` | fixed `"1"` | Same |
| `POSTGRES_PASSWORD` | `${POSTGRES_PASSWORD:-mrr_local_only}` | Substituted into `DATABASE_URL` and passed to the `postgres` service |
| `GIT_SHA` | build argument `${GIT_SHA:-unknown}` for `api`, `segment-worker`, `summarize-worker` | `backend/Dockerfile`, which sets `BUILD_SHA` from it |

## Boot-time validation

Every check below stops the API or a worker from starting. The required-settings check is pydantic
validation when `Settings` is built; the next ten raise `RuntimeError` in `Settings._derive()`; the
vLLM preflight raises `RuntimeError` in `backend/app/services/llm/preflight.py`
`assert_backends_ready()`, called from `backend/app/main.py` `_lifespan()` and
`backend/app/worker/__main__.py` `main()`.

| Check | Condition that refuses startup |
| --- | --- |
| Required settings | `DATABASE_URL`, `SECRET_KEY` or `SECURITY_PASSWORD_SALT` missing |
| Vertex in production | `ENVIRONMENT=prod` and `GOOGLE_GENAI_USE_VERTEXAI` false |
| Backend name | `LLM_BACKEND` not `gemini`, `openai` or `vllm` |
| Overrides | An `LLM_BACKEND_OVERRIDES` entry without `=`, with an unknown stage, or with an unknown backend |
| OpenAI keys | `SUMMARY_PROVIDER=openai` or any stage on `openai`, and `OPENAI_API_KEY`, `SUMMARY_BODY_MODEL`, `SUMMARY_TITLE_MODEL` or `AUDIT_MODEL` empty |
| OpenAI Zero Data Retention | Same condition, `ENVIRONMENT=prod`, and `OPENAI_ZDR_ACKNOWLEDGED` not true |
| vLLM keys | Any stage on `vllm` and `VLLM_BASE_URL` or `VLLM_MODEL` empty |
| vLLM image counts | A stage on `vllm` whose image cap exceeds `VLLM_MAX_IMAGES_PER_PROMPT` |
| vLLM render targets | `doi` or `deposition` on `vllm` and `SUMMARY_IMAGE_DPI` caps its render more than one dpi step below its long-edge target |
| vLLM origin | `ENVIRONMENT=prod`, any stage on `vllm`, and the origin of `VLLM_BASE_URL` not in `_APPROVED_VLLM_ORIGINS` |
| Model name versus backend | A resolved model starting `gemini-` on a non-Gemini backend, or a name with `/` and without `gemini` on Gemini |
| vLLM preflight | Any stage on `vllm` and `GET {root}/version` fails, times out after 10 s, returns no usable `version`, or reports a version below `VLLM_MIN_VERSION` |

## Settings methods

| Method | Returns |
| --- | --- |
| `backend_for(stage)` | The backend for one stage: first matching override, else `LLM_BACKEND`. Unknown stage raises `KeyError` |
| `resolved_backends()` | Set of `LLM_BACKEND` plus every override backend |
| `model_for(kind)` | Summarize model for `body`, `title` or `audit` |
| `model_for_stage(stage)` | Model for a non-summarize stage: `VLLM_MODEL` on vLLM, else the Gemini setting for that stage. `summarize` raises `KeyError` |
| `thinking_for(stage)` | Gemini thinking budget for a stage |
| `vllm_thinking_for(stage)` | Whether a stage may think on vLLM |
| `effective_job_timeout(pages)` | `max(JOB_TIMEOUT, int(pages x JOB_TIMEOUT_PER_PAGE))` |
| `pool_timeout(pages)` | `max(1, effective_job_timeout(pages) - FUTURE_TIMEOUT_MARGIN_SECONDS)` |
| `effective_genai_timeout_ms(est_tokens)` | `max(GENAI_HTTP_TIMEOUT_MS, int(est_tokens x GENAI_TIMEOUT_PER_1K_TOKENS_MS / 1000))` |

## Module constants in `config.py`

| Constant | Value | Role |
| --- | --- | --- |
| `_LLM_STAGES` | `summarize`, `segment`, `extract`, `dedup`, `classify`, `verify`, `doi`, `deposition` | Valid stage names |
| `_LLM_BACKENDS` | `gemini`, `openai`, `vllm` | Valid backend names |
| `_APPROVED_VLLM_ORIGINS` | `http://127.0.0.1:8000`, `http://localhost:8000` | Origins approved for `VLLM_BASE_URL` in production |
| `_DOI_IMAGE_CAP` | `10` | Mirror of `summary_doi._MAX_PAGES` for the image guard |
| `_DEPOSITION_IMAGE_CAP` | `6` | Mirror of `deposition_pages._MAX_PAGES` for the image guard |
| `_IMAGE_CAPPED_STAGES` | `summarize`, `segment`, `doi`, `deposition` | Stages that send page images on vLLM |
| `_STAGE_RENDER_TARGETS` | `doi` -> `doi_image_long_edge_px`, `deposition` -> `deposition_image_long_edge_px` | Stages with their own render target |
| `_SMALLEST_PAGE_LONG_EDGE_PT` | `790.0` | Page geometry the render-target guard evaluates |
| `_GEMINI_FLASH_MODEL` | `gemini-2.5-flash` | Vertex default for `GENAI_MODEL`; Gemini default for the title and audit models |

## Related pages

- [Configuration model](../explanation/configuration-model.md)
- [Model providers](../explanation/model-providers.md)
- [Model calls by stage](model-calls-by-stage.md)
- [Compose services reference](compose-services.md)
