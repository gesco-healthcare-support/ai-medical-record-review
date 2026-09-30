# backend/app/services/llm

The model provider seam. Every model call the backend makes goes through this package: a service
builds a provider-neutral request (text, JSON matching a schema, or one value from a fixed list), and
the package sends it to Gemini (google-genai, Vertex AI or the Developer API), OpenAI chat
completions, or a self-hosted vLLM server, depending on which backend the configuration assigns to
that pipeline stage. It also holds the cross-process rate limiter every backend shares and the
boot-time version check for vLLM.

## Files

| File | Purpose |
| --- | --- |
| `__init__.py` | Registry. `get_provider(name=None)` returns a cached provider; with no name it resolves the summarize stage, honouring `SUMMARY_PROVIDER`. `provider_for_stage(stage)` returns the provider for any other stage. Re-exports the part and response types |
| `base.py` | `LLMProvider` protocol (`generate_text`, `generate_structured`, `generate_choice`), the frozen `LLMResponse` (`text`, `truncated`, token counts), and `DelegatingProvider`, which writes the three public methods once over a provider's `_call()` |
| `parts.py` | Provider-neutral request parts: `TextPart`, `ImagePart`, `DocumentPart` |
| `gemini.py` | `GeminiProvider`. Translates lowercase JSON Schema with `to_gemini_schema()`, sets the per-stage thinking budget, and sends through `backend/app/services/genai_retry.py` `generate_with_retry()` |
| `openai.py` | `OpenAIProvider`. Chat completions only, `store=False` on every request, strict JSON schemas, its own retry loop, and rate-limit headers fed to the pacer. Its message and schema helpers are reused by `vllm.py` |
| `vllm.py` | `VLLMProvider`. The OpenAI wire dialect against a self-hosted server, without `store` or `strict`, with thinking sent explicitly on or off, and no retry after a client read timeout |
| `pacing.py` | Two Redis token buckets per provider and model (requests and estimated tokens) driven by an AIMD controller; fails open when Redis is unavailable |
| `tokens.py` | `estimate_tokens()`: the pre-call token estimate the pacer charges and the Gemini deadline scales with |
| `preflight.py` | `assert_backends_ready()`: when any stage is on vLLM, reads the server's `GET /version` and refuses startup below `VLLM_MIN_VERSION` |
| `README.md` | This file |
| `CLAUDE.md` | Rules for an AI coding agent working in this folder |

Modules outside the folder that belong to the same seam:

| Module | Purpose |
| --- | --- |
| `backend/app/config.py` | Stage and backend names, per-stage backend, model and thinking resolvers, and the boot guards on provider configuration |
| `backend/app/services/genai_retry.py` | Gemini retry loop: backoff, 429 and 504 handling, size-scaled per-request deadline |
| `backend/app/services/genai_client.py` | Cached google-genai client |
| `backend/app/services/genai_metrics.py` | Per-model attempt counters in Redis (`vertex:metrics:<model>`) |
| `backend/app/services/gemini.py` | The segmentation prompt, response schema and row parser. Not a provider, despite the name |

## How it is used

- Pipeline services call it. The summarize stage (`summarize_engine`, `summary_verify`) uses
  `get_provider()` with the models stored on the summarize job. Every other stage (`segment_engine`,
  `extraction`, `dedup`, `classification`, `verify_pass`, `summary_doi`, `deposition_pages`) uses
  `provider_for_stage(stage)` with `Settings.model_for_stage(stage)`.
- The API (`backend/app/main.py` `_lifespan()`) and every worker (`backend/app/worker/__main__.py`
  `main()`) call `preflight.assert_backends_ready()` before serving or taking jobs.
- Operator scripts read its Redis state: `backend/scripts/eval/vertex_stats.py` (attempt counters)
  and `backend/scripts/eval/pacer_watch.py` (pacer rates).

## How it is tested

The tests live in `backend/tests/`: `test_llm_provider.py`, `test_llm_openai.py`,
`test_llm_vllm.py`, `test_llm_preflight.py`, `test_pacing.py`, `test_genai_retry.py`,
`test_genai_client.py`, `test_genai_metrics.py`, `test_openai_config_guards.py`,
`test_segment_thinking.py`, `test_compose_passthrough.py`, `test_pool_wiring.py`,
`test_backend_provenance.py` and `test_cancel_escapes_model_calls.py`.

The suite's `conftest.py` expects the test Postgres and Redis to be running; set them up as described
in [How to run the tests](../../../../docs/how-to/run-the-tests.md). Then, from the repo root:

```bash
cd backend
uv sync --extra docs
uv run pytest tests/test_llm_provider.py tests/test_llm_openai.py tests/test_llm_vllm.py tests/test_llm_preflight.py tests/test_pacing.py tests/test_genai_retry.py tests/test_genai_client.py tests/test_genai_metrics.py tests/test_openai_config_guards.py tests/test_segment_thinking.py tests/test_compose_passthrough.py tests/test_pool_wiring.py tests/test_backend_provenance.py tests/test_cancel_escapes_model_calls.py
```

## Documentation

- [Model providers](../../../../docs/explanation/model-providers.md): why the seam exists, routing,
  retries, pacing, metrics and boot guards
- [Model calls by stage](../../../../docs/reference/model-calls-by-stage.md): every call site with its
  model, thinking, output cap and failure behaviour
- [Configuration reference](../../../../docs/reference/configuration.md): every setting
- [How to switch model backends](../../../../docs/how-to/switch-model-backends.md)

<!-- reviewed: 2026-09-30 -->
