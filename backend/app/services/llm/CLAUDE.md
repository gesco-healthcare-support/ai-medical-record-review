# CLAUDE.md - backend/app/services/llm

The model provider seam. Every model request in `backend/app` goes through this package. The only
code that may put a request on the wire is `genai_retry.generate_with_retry()` (reached only from
`gemini.py`), `OpenAIProvider._call()` and `VLLMProvider._call()`. Do not import google-genai or
the openai SDK anywhere else in `backend/app` to make a call.

## Routing invariants

- A non-summarize stage uses BOTH `provider_for_stage(stage)` for the transport AND
  `get_settings().model_for_stage(stage)` for the model, and passes `stage="<stage>"`. A bare
  `get_provider()` resolves the summarize backend; pairing it with `model_for_stage()` sends one
  backend's model name to another backend and fails silently. This shipped once (#318).
- Summarize uses bare `get_provider()` and the three models stored on the job (`job.model`,
  `job.title_model`, `job.audit_model`). Never route summarize through `provider_for_stage()` (it
  raises) and never call `model_for_stage("summarize")` (it raises); `get_provider()` is the only path
  that honours `SUMMARY_PROVIDER=openai`.
- `stage=` selects thinking only. It never selects a transport.
- `get_provider(name)` with an explicit name ignores configuration on purpose; the benchmark harness
  relies on it.
- Stage names live in `config._LLM_STAGES`, backend names in `config._LLM_BACKENDS`. Do not add a
  second list.

## Request invariants

- Write schemas in lowercase JSON Schema. `gemini.to_gemini_schema()` and `openai._strict_schema()`
  translate. Never hand-write Gemini uppercase types in a service.
- Part order is load-bearing: images, then OCR text, then the instruction last. OpenAI and vLLM put
  all parts in one user message to keep that order.
- `max_output_tokens`, `top_p`, `top_k` are sent only when not None. Do not add defaults that change
  the request a caller already sends.
- A parameter a backend cannot express is refused with `TypeError`, never silently dropped:
  OpenAI refuses `top_k`, `choices` in `_call()`, and `DocumentPart`; vLLM refuses `DocumentPart`.
- `OpenAIProvider.generate_choice()` must stay overridden; `_call()` raises on `choices` so deleting
  the override fails loudly.
- OpenAI: chat completions only (never Batch, Assistants, threads, fine-tuning) and `store=False`
  on every request. These are PHI constraints, not style.
- vLLM: never send `store`, `strict` or a thinking budget (`thinking_token_budget`). Always send
  `chat_template_kwargs.enable_thinking` explicitly, from `Settings.vllm_thinking_for(stage)`.
  `top_k`, `structured_outputs` and the thinking flag go in `extra_body`; a top-level unknown key is
  accepted and ignored by vLLM.
- Gemini: always send `thinking_config` with `Settings.thinking_for(stage)`. Omitting it once made
  every injury-date read return "-".
- Do not add `summarize` to `VLLM_THINKING_STAGES` and do not step `SUMMARY_THINKING_BUDGET` to 0
  without re-scoring (comments in `config.py` record why).

## Retry, pacing and metrics invariants

- `range(settings.genai_max_retries)` counts ATTEMPTS. Keep it that way in all three loops.
- One pacer budget per logical call: compute `pacer_deadline = time.monotonic() +
  pacing.MAX_ACQUIRE_WAIT_S` once, before the loop, and pass the remaining time to each
  `pacing.acquire()`.
- Every backoff sleep goes through the module's `_cancellable_sleep()` (1 s slices, raises
  `JobCancelled`). Never `time.sleep()` a backoff.
- Record `genai_metrics` per attempt, plus `OUTCOME_EXHAUSTED` once when attempts run out. Metrics
  and pacing must never raise into a job: both swallow their own errors, and the pacer fails open.
- vLLM: never retry `openai.APITimeoutError`; it starts a second generation on the rented GPU.
- Gemini 504: exactly one retry at `deadline x GENAI_DEADLINE_RETRY_MULTIPLIER`, then raise.
- Any caller that wraps a provider call in `except Exception` must first `except JobCancelled: raise`.
  `JobCancelled` subclasses `Exception`; swallowing it turns a Stop into a fake model failure.
- The worker's transient/permanent taxonomy (`backend/app/worker/failures.py` `classify_failure()`)
  and the user-facing messages (`backend/app/errors.py` `genai_user_message()`) match google-genai
  and httpx exception types. A new backend's exceptions must be added there.

## Boot-time invariants

- Never wrap `preflight.assert_backends_ready()` in `except Exception`. Its two call sites
  (`backend/app/main.py` `_lifespan()`, `backend/app/worker/__main__.py` `main()`) sit next to blocks
  that deliberately swallow; do not copy that shape.
- Do not move the version check into `Settings`: pytest, alembic and eval scripts build `Settings`
  and cannot reach a pod. Do not probe Gemini at boot.
- Provider configuration guards live in `Settings._derive()` and its helpers. Their order is
  load-bearing (comments in `_derive`): `_apply_vllm_call_defaults()` after
  `_validate_vllm_backend()`, and the model-name check last.
- `_APPROVED_VLLM_ORIGINS` is a code constant on purpose. Do not make it env-editable in production.

## Settings and tests

- `get_settings()`, `get_provider()`, the genai client and both OpenAI-SDK client caches are cached
  per process. In tests, monkeypatch attributes on the cached `Settings` or patch `get_provider`;
  do not call `cache_clear()` (see the docstring of `backend/tests/test_backend_provenance.py`).
- `Settings` reads the environment; uppercase constructor kwargs are ignored. Config guard tests set
  real env vars (`backend/tests/test_openai_config_guards.py`).
- A new setting needs a `#` comment block directly above the field with no blank line between. If a
  boot guard compares it, if it must match how a pod is served, or if its comment says it can be set
  from the environment, name it in `docker-compose.yml` `x-backend-env` as `${NAME:-<config default>}`
  and add it to `.env.example`. `backend/tests/test_compose_passthrough.py` and
  `backend/tests/test_pool_wiring.py` enforce this.

## Checklists

Adding a stage: `_LLM_STAGES`; its Gemini model in `Settings.model_for_stage()`; its budget in
`Settings.thinking_for()`; if it sends page images on vLLM, `_IMAGE_CAPPED_STAGES`,
`Settings._stage_image_caps()` and the `expected` map in
`test_every_stage_that_rasterises_is_covered_by_the_image_guard`; call it with
`provider_for_stage()` plus `model_for_stage()` plus `stage=`.

Adding a backend: `_LLM_BACKENDS`; a branch in `get_provider()` with a deferred SDK import; a
`DelegatingProvider` subclass whose `_call()` owns retry, `pacing.acquire()`, metrics and a
cancellable sleep; `pacing.ceilings()`, `pacing._BURST_SECONDS`, `tokens._IMAGE_TOKENS`; settings and
guards in `_derive`; a preflight if the server must be vetted; `classify_failure()` and
`genai_user_message()`; compose and `.env.example` entries.

## Traps that have bitten

- `vllm._CLIENTS` is keyed on scalars because the SDK's `Timeout` is unhashable.
- `pacing.snapshot()` strips the known prefix and suffix instead of counting colons from the front,
  because the prefix `llm:pace` itself contains a colon.
- `DelegatingProvider` and `vllm._request_kwargs()` exist to clear SonarCloud gates (duplication,
  cognitive complexity 15). Do not inline them back.
- The Redis metrics hash is named `vertex:metrics:<model>` but every backend writes to it.

## Commands

From the repo root. The suite needs the test Postgres and Redis; see `docs/how-to/run-the-tests.md`.

```bash
cd backend
uv sync --extra docs
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_llm_provider.py tests/test_llm_openai.py tests/test_llm_vllm.py tests/test_llm_preflight.py tests/test_pacing.py tests/test_genai_retry.py tests/test_genai_client.py tests/test_genai_metrics.py tests/test_openai_config_guards.py tests/test_segment_thinking.py tests/test_compose_passthrough.py tests/test_pool_wiring.py tests/test_backend_provenance.py tests/test_cancel_escapes_model_calls.py
```

## Full documentation

- `docs/explanation/model-providers.md`
- `docs/reference/model-calls-by-stage.md`
- `docs/reference/configuration.md`
- `docs/explanation/configuration-model.md`
- `docs/how-to/switch-model-backends.md`

<!-- reviewed: 2026-09-30 -->
