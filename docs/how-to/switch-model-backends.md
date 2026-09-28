# How to switch model backends

Use this when you need to move one pipeline stage, or the whole pipeline, between Gemini (the
default), a self-hosted vLLM server and OpenAI; change the model a stage uses; or roll any of that
back. Every change here is configuration only: edit `.env`, recreate the backend containers,
verify. No rebuild is needed.

Background: [Model providers](../explanation/model-providers.md) explains routing and the boot
guards; [Model calls by stage](../reference/model-calls-by-stage.md) lists each stage's model
setting; [Configuration reference](../reference/configuration.md) lists every variable used below.

## Prerequisites

- A shell on the machine running the Compose stack (`<SERVER_HOST>` in production), in the checkout
  directory that holds `docker-compose.yml` and `.env`. For a backend run on your own machine
  outside Compose, the same variables go in `backend/.env` and you restart the processes instead;
  see [How to run the app locally](run-the-app-locally.md).
- A quiet moment. Recreating the workers stops the jobs they are running; see
  [How to deploy to the server](deploy-to-the-server.md) for what happens to them.
- If you are moving the `summarize` stage: no summarize job queued, running or paused. Summarize
  jobs store the three model names they were created with, while the backend is resolved by the
  process that makes the call. Check with:

  ```bash
  docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT id, kind, state FROM jobs WHERE state IN ('queued', 'running', 'paused');"
  ```

  Expected: no `summarize` rows.
- For vLLM:
  - the server's OpenAI-compatible base URL, normally ending in `/v1`, reachable from inside the
    containers. In production its origin must be `http://127.0.0.1:8000` or
    `http://localhost:8000`; any other origin refuses startup, and widening the list is a code change
    to `_APPROVED_VLLM_ORIGINS` in `backend/app/config.py`;
  - the exact model name the server serves;
  - the server's `--limit-mm-per-prompt` image limit;
  - a server version at or above `VLLM_MIN_VERSION` (default `0.24.0`).
- For OpenAI: an API key, the three summarize models you have chosen (there is no default on
  purpose), and, in production, confirmation that Zero Data Retention is approved on the OpenAI
  organization.

Back up the current `.env` before every change:

```bash
cp .env .env.bak-$(date +%Y%m%d-%H%M%S)
```

## Route one stage to vLLM

1. Confirm the server answers from inside the API container. Replace `<VLLM_ROOT>` with the base
   URL's `scheme://host:port`, without `/v1`:

   ```bash
   docker compose exec -T api python -c "import httpx; print(httpx.get('<VLLM_ROOT>/version', timeout=10).json())"
   ```

   Expected: `{'version': '<x.y.z>'}` with a version at or above `VLLM_MIN_VERSION`.

2. Add or change these lines in `.env`, replacing the placeholders. List several stages with commas,
   for example `segment=vllm,classify=vllm`:

   ```bash
   LLM_BACKEND_OVERRIDES=segment=vllm
   VLLM_BASE_URL=http://127.0.0.1:8000/v1
   VLLM_MODEL=<SERVED_MODEL_NAME>
   VLLM_MAX_IMAGES_PER_PROMPT=<POD_LIMIT_MM_PER_PROMPT>
   ```

   Valid stage names: `summarize`, `segment`, `extract`, `dedup`, `classify`, `verify`, `doi`,
   `deposition`. `VLLM_MODEL` never reaches a container empty: Compose substitutes
   `Qwen/Qwen3.6-35B-A3B-FP8` for an unset or empty value, so set it whenever the server serves a
   different name.

3. Check the image caps of every stage you are moving against `VLLM_MAX_IMAGES_PER_PROMPT`:
   `summarize` sends up to `SUMMARY_IMAGE_MAX_PAGES` (15), `segment` up to `VLLM_SEGMENT_MAX_PAGES`
   (30), `doi` up to 10 and `deposition` up to 6. If a settable cap is higher than the server limit,
   lower it in `.env`. The `doi` and `deposition` caps are code constants; if the server limit is
   below them, leave that stage on Gemini.

4. If the stage is `summarize`, make sure `SUMMARY_BODY_MODEL`, `SUMMARY_TITLE_MODEL` and
   `AUDIT_MODEL` are empty or absent in `.env`, so they default to `VLLM_MODEL`. A `gemini-` name
   left in any of them refuses startup. Leave `SUMMARY_PROVIDER=gemini`; `openai` there would keep
   summarize on OpenAI.

5. Recreate the backend containers:

   ```bash
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   Expected: the three services start. Then run [Verify](#verify).

## Route the whole pipeline to vLLM

1. Complete step 1 of the previous section.

2. Set these lines in `.env`:

   ```bash
   LLM_BACKEND=vllm
   LLM_BACKEND_OVERRIDES=
   VLLM_BASE_URL=http://127.0.0.1:8000/v1
   VLLM_MODEL=<SERVED_MODEL_NAME>
   VLLM_MAX_IMAGES_PER_PROMPT=<POD_LIMIT_MM_PER_PROMPT>
   ```

   To keep some stages on Gemini, name them in the overrides instead of leaving it empty, for example
   `LLM_BACKEND_OVERRIDES=classify=gemini,dedup=gemini`.

3. Apply steps 3 and 4 of the previous section to every stage now on vLLM.

4. Keep `GOOGLE_GENAI_USE_VERTEXAI=true` in production. The production guard requires it whatever
   the backends are.

5. Recreate the backend containers:

   ```bash
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   Then run [Verify](#verify).

## Route the summarize stage to OpenAI

OpenAI serves the `summarize` stage only. Do not set `LLM_BACKEND=openai` or route another stage to
`openai`: every other stage would resolve a Gemini model name, which the model-name check refuses,
and segmentation and the two isolated reads send PDFs, which chat completions cannot take.

1. Set these lines in `.env`, replacing the placeholders:

   ```bash
   LLM_BACKEND_OVERRIDES=summarize=openai
   OPENAI_API_KEY=<OPENAI_API_KEY>
   SUMMARY_BODY_MODEL=<BODY_MODEL>
   SUMMARY_TITLE_MODEL=<TITLE_MODEL>
   AUDIT_MODEL=<AUDIT_MODEL>
   ```

   None of the three model names may start with `gemini-`. `SUMMARY_PROVIDER=openai` is the older
   switch with the same routing effect; `.env.example` and the comments prefer `LLM_BACKEND` and its
   overrides.

2. In production only, after confirming Zero Data Retention in the OpenAI organization settings,
   add:

   ```bash
   OPENAI_ZDR_ACKNOWLEDGED=true
   ```

3. Recreate the backend containers:

   ```bash
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   Then run [Verify](#verify). `OPENAI_MAX_RPM` and `OPENAI_MAX_TPM` are not passed to containers,
   so the pacer starts from the code defaults and then follows OpenAI's rate headers.

## Change the model a stage uses

1. Pick the variable for the stage and backend:

   | Stage | On Gemini | On vLLM | On OpenAI |
   | --- | --- | --- | --- |
   | `summarize` body | `SUMMARY_BODY_MODEL`, else `SUMMARY_MODEL` | `SUMMARY_BODY_MODEL`, else `VLLM_MODEL` | `SUMMARY_BODY_MODEL` |
   | `summarize` title | `SUMMARY_TITLE_MODEL` | `SUMMARY_TITLE_MODEL`, else `VLLM_MODEL` | `SUMMARY_TITLE_MODEL` |
   | `summarize` audit | `AUDIT_MODEL` | `AUDIT_MODEL`, else `VLLM_MODEL` | `AUDIT_MODEL` |
   | Body fallback after a 429 | `SUMMARY_BODY_FALLBACK_MODEL` (`none` disables) | not used | not used |
   | `segment`, `extract`, `doi`, `deposition` | `GENAI_MODEL` (shared by all four) | `VLLM_MODEL` | not supported |
   | `classify`, `dedup` | `CLASSIFY_MODEL` (shared by both) | `VLLM_MODEL` | not supported |
   | `verify` | `VERIFY_MODEL`, else `GENAI_MODEL` | `VLLM_MODEL` | not supported |

   A vLLM server serves one model, so every vLLM stage uses `VLLM_MODEL` unless you set a summarize
   key to another name that server also serves.

2. Set the variable in `.env`, for example:

   ```bash
   CLASSIFY_MODEL=<GEMINI_MODEL_NAME>
   ```

   Leave `GENAI_MODEL` and `VERIFY_MODEL` empty to keep their derived defaults. Keep
   `SUMMARY_THINKING_BUDGET` at `-1`; the comment on it in `backend/app/config.py` warns against 0
   without re-scoring.

3. Recreate the backend containers:

   ```bash
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   Summarize model changes apply to summarize jobs created after the restart; existing jobs keep the
   models stored on them.

## Switch back to Gemini

This is also the recovery when a vLLM server is down or too old: while any stage points at vLLM,
the API and every worker run the version check at startup and refuse to start when it fails.

1. Set these lines in `.env`:

   ```bash
   LLM_BACKEND=gemini
   LLM_BACKEND_OVERRIDES=
   SUMMARY_PROVIDER=gemini
   ```

2. Empty any model variable that now holds a vLLM or OpenAI name: `SUMMARY_BODY_MODEL`,
   `SUMMARY_TITLE_MODEL`, `AUDIT_MODEL`, `GENAI_MODEL`, `VERIFY_MODEL`, `CLASSIFY_MODEL`. A name
   containing `/` without `gemini` refuses startup on Gemini; a name without `/`, such as an OpenAI
   model, is not caught by the check and would be sent to Vertex.

3. The `VLLM_*` and `OPENAI_*` lines can stay. They are read only while some stage resolves to that
   backend.

4. Recreate the backend containers:

   ```bash
   docker compose up -d --force-recreate api segment-worker summarize-worker
   ```

   Then run [Verify](#verify).

## Verify

1. The three services are up, not restarting:

   ```bash
   docker compose ps api segment-worker summarize-worker
   ```

   Expected: every row `Up`. A service in `Restarting` failed a boot check; go to
   [If it fails](#if-it-fails).

2. Print the routing the containers resolved:

   ```bash
   docker compose exec -T api python - <<'EOF'
   from app.config import _LLM_STAGES, get_settings
   s = get_settings()
   for stage in _LLM_STAGES:
       if stage == "summarize":
           models = [s.model_for(kind) for kind in ("body", "title", "audit")]
       else:
           models = s.model_for_stage(stage)
       print(stage, s.backend_for(stage), models)
   print("SUMMARY_PROVIDER", s.summary_provider)
   EOF
   ```

   Expected: each stage on the backend and model you intended. `SUMMARY_PROVIDER openai` means
   summarize goes to OpenAI whatever its line says.

3. When any stage is on vLLM, every process logs the version check:

   ```bash
   docker compose logs api segment-worker summarize-worker | grep "vllm preflight"
   ```

   Expected: one `vllm preflight: <root> reports version <x.y.z> (floor <floor>)` line per process
   (the API plus each worker replica).

4. After the next job has run, confirm who answered. Attempt counters are keyed by model name:

   ```bash
   docker compose exec -T api python scripts/eval/vertex_stats.py
   ```

   Summary rows record the backend and model that produced them:

   ```bash
   docker compose exec -T postgres psql -U mrr -d mrr -c "SELECT job_id, backend, model, count(*) FROM summaries GROUP BY job_id, backend, model ORDER BY job_id DESC LIMIT 10;"
   ```

## If it fails

A failed boot check leaves the message in the container log:

```bash
docker compose logs --tail 50 api
```

| Message contains | Cause | Fix |
| --- | --- | --- |
| `is not a known backend` | `LLM_BACKEND` misspelled | Use `gemini`, `openai` or `vllm` |
| `LLM_BACKEND_OVERRIDES entry ... does not name a known stage` | Missing `=` or a misspelled stage | Use `stage=backend` with a valid stage |
| `LLM_BACKEND_OVERRIDES sets stage ... to unknown backend` | Misspelled backend | Use `gemini`, `openai` or `vllm` |
| `a vllm backend requires` | `VLLM_BASE_URL` or `VLLM_MODEL` empty | Set it |
| `page images per request, above VLLM_MAX_IMAGES_PER_PROMPT` | A stage's image cap exceeds the server limit | Lower that stage's cap, or match the server limit; for `doi` or `deposition`, route that stage to `gemini` |
| `SUMMARY_IMAGE_DPI is ..., which caps the ... read` | The dpi ceiling is below a vLLM render target | Raise `SUMMARY_IMAGE_DPI` or lower the target, as the message states |
| `which is not approved to receive PHI in production` | `VLLM_BASE_URL` origin not approved | Use `http://127.0.0.1:8000` or `http://localhost:8000`; widening the list is a code change |
| `a Gemini model name, but that call is routed to the` | A `gemini-` name left in a key used by a non-Gemini stage | Empty that key or set a name the backend serves |
| `which reads as a Hugging Face repo id, but that call is routed to the 'gemini' backend` | A vLLM name left in a key used by a Gemini stage | Empty that key |
| `could not read the vLLM version from` | Server unreachable or `/version` malformed | Fix the server or tunnel, or switch back to Gemini |
| `reports version ..., below the required floor` | Server older than `VLLM_MIN_VERSION` | Upgrade the server; the floor is a CVE boundary |
| `does not parse as a URL` | `VLLM_BASE_URL` malformed | Correct it |
| `SUMMARY_PROVIDER=openai requires` | OpenAI selected (by either switch) without the key or all three models | Set the missing keys it lists |
| `OPENAI_ZDR_ACKNOWLEDGED must be true` | Production with OpenAI and no acknowledgement | Confirm Zero Data Retention, then set it |
| `GOOGLE_GENAI_USE_VERTEXAI must be true in production` | Vertex turned off with `ENVIRONMENT=prod` | Set it to `true` |

`VLLM_THINKING_STAGES` is not checked at boot. A misspelled stage there raises
`VLLM_THINKING_STAGES names unknown stage(s)` on the first vLLM call, which fails that job.

To undo a change entirely, restore the backup and recreate:

```bash
cp .env.bak-<STAMP> .env
docker compose up -d --force-recreate api segment-worker summarize-worker
```

## Related pages

- [Model providers](../explanation/model-providers.md)
- [Model calls by stage](../reference/model-calls-by-stage.md)
- [Configuration reference](../reference/configuration.md)
- [Configuration model](../explanation/configuration-model.md)
- [How to deploy to the server](deploy-to-the-server.md)
- [How to diagnose a stuck or failed job](diagnose-a-stuck-or-failed-job.md)
- [Scripts reference](../reference/scripts.md)
