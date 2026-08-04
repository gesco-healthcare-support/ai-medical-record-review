# Plan: move summarization and auditing to OpenAI behind a provider abstraction

Status: researched, decisions resolved with Adrian 2026-08-03, nothing implemented.
Baseline `origin/main` `71ce31d`.

Evidence: `W:\MRR_Research_and_Analysis\03_Reports\OPENAI_OPTION_2026-08-03.md`,
`CAPACITY_500K_PAGES_2026-08-03.md`, `THROUGHPUT_CAPACITY_2026-08-03.md`.

## 0. Decisions (resolved -- do not re-litigate)

| #   | Decision         | Chosen                                                                  |
| --- | ---------------- | ----------------------------------------------------------------------- |
| 1   | Which calls move | **Body + title + audit.** DOI stays on Gemini Flash                     |
| 2   | Rate limiting    | **Per-model request bucket, generous cap.** TPM as a metric, not a gate |
| 3   | Seam shape       | **Full provider abstraction**                                           |
| 4   | Model choice     | **Config-driven ladder, no hardcoded default** -- see section 6         |

## 1. Why this is worth doing

Measured 2026-08-03: Vertex 2.5-pro rejects 75-93% of requests at concurrency **2**, and was worse
on a re-run thirty seconds later. Three of every four summarize calls per row run on pro. That is
why the summarize stage sits at 7.8 accepted calls/min while the rate bucket runs at 39%
utilisation - **pro is saturated at concurrency 1, and the bucket was never the constraint.**

OpenAI, measured on the account: **5,000 RPM and 1-2M TPM**, per-project, deterministic, reported in
`x-ratelimit-*` headers on every response. At the 500,000 pages/month target we would use **0.4% of
RPM and 6-7% of TPM.**

## 2. The BAA constraint - get this right first

The BAA is signed. That is **not sufficient on its own**. Per OpenAI's data-retention
documentation, processing PHI requires Zero Data Retention (or Modified Abuse Monitoring / Eyes Off
/ Safety Retention) approved on the organization **in addition to** the executed BAA.

**Prerequisite, not a task: confirm with OpenAI that ZDR is enabled on this org.** Everything below
assumes it.

Consequences that constrain the design:

- **Use `/v1/chat/completions`.** It is ZDR-eligible. `/v1/responses` also is, but it stores by
  default (`store=true`) and buys us nothing here.
- **The Batch API is NOT ZDR-eligible** and must never carry PHI. This kills the 50% batch discount
  quoted in the earlier cost report - that option is off the table, not merely unattractive.
- Also ineligible and therefore banned for this pipeline: Assistants, threads, vector stores,
  conversations, fine-tuning jobs.
- Set `store=false` explicitly on every call. ZDR forces it server-side, but an explicit false means
  a misconfigured org fails safe rather than silently retaining PHI.
- Mirror the existing Vertex production fail-fast: in `ENVIRONMENT=prod`, refuse to start if the
  OpenAI provider is selected without an explicit ZDR acknowledgement flag set.

## 3. Architecture: `app/services/llm/`

A full provider abstraction (decision 3), so any stage can move later without another port.

```
app/services/llm/
  __init__.py      # get_provider(name) -> LLMProvider
  base.py          # LLMProvider protocol + normalized part/response types
  parts.py         # TextPart, ImagePart(bytes, mime), DocumentPart(bytes, mime)
  gemini.py        # wraps the existing google-genai path
  openai.py        # new
  retry.py         # provider-agnostic retry/backoff (generalises genai_retry)
```

**The interface, deliberately small:**

```python
class LLMProvider(Protocol):
    def generate_text(self, *, model, system, parts, temperature,
                      max_output_tokens) -> LLMResponse: ...
    def generate_structured(self, *, model, system, parts, schema, temperature,
                            max_output_tokens) -> LLMResponse: ...

@dataclass(frozen=True)
class LLMResponse:
    text: str
    truncated: bool          # Gemini: finish_reason MAX_TOKENS; OpenAI: finish_reason "length"
    input_tokens: int | None
    output_tokens: int | None
```

**What each provider must reconcile:**

| Concern           | Gemini                                   | OpenAI                                        |
| ----------------- | ---------------------------------------- | --------------------------------------------- |
| System prompt     | `config.system_instruction`              | a `system` role message                       |
| Images            | `types.Part.from_bytes`                  | `image_url` with a base64 data URL            |
| Structured output | `response_schema` + `response_mime_type` | `response_format` json_schema, `strict: true` |
| Thinking budget   | `thinking_config`                        | no equivalent -- ignore, do not emulate       |
| Truncation        | `_hit_token_cap(response)`               | `finish_reason == "length"`                   |
| Retry hint        | `RetryInfo.retryDelay` in error details  | `Retry-After` header                          |

`thinking_config` having no OpenAI equivalent is the one asymmetry worth naming in code: the
Gemini provider keeps it, the OpenAI provider ignores it, and neither leaks the concept upward.

## 4. Call sites to migrate

Three, and only three:

1. **`summarize_engine._generate`** (`summarize_engine.py:422`) - serves both the summary body
   (multimodal) and the title (text only).
2. **`summarize_engine._page_image_parts`** (`:445`) - returns `types.Part` today; must return
   neutral `ImagePart`s.
3. **`summary_verify.verify_summary`** (`summary_verify.py`) - the structured audit call.

**Not migrated**, and they keep the Gemini provider through the same abstraction:
`segment_engine`, `classification`, `dedup`, and `summary_doi.extract_injury_date` (decision 1 -
DOI is isolated vision on the raw PDF, is already on the right tier, and PR #71 has just fixed it).

### The structured-output port needs care

`summary_verify._RESPONSE_SCHEMA` will not transfer as-is. OpenAI strict mode requires **every**
property to be listed in `required`, with optionality expressed as a null union, and
`additionalProperties: false` on every object. Today `fixed_title` is optional. It must become
`"type": ["string", "null"]` and be required, and `verify_summary` must treat null as "no title
change" - exactly what an absent key means today.

Strict mode also forbids `allOf`, `not`, `if/then/else`, and an `anyOf` root. The current schema
uses none of those, so the enum and nesting are fine.

## 4b. Rate limiting: researched 2026-08-05, supersedes decision 2

Decision 2 originally said "per-model request bucket, generous cap". **Measurement and research both
say a fixed request-rate cap cannot work.** What replaced it, and why:

### What was measured

- **Vertex's 429 carries no signal.** The entire body is
  `{"code":429,"message":"Resource exhausted. Please try again later.","status":"RESOURCE_EXHAUSTED"}`
  - no `RetryInfo`, no remaining-capacity header. So `genai_retry._retry_delay_seconds()` never
    fires on our endpoint and backoff always takes over.
- **The serviceable rate moves by more than 4x across days.** 2.5-pro rejected 75-93% at concurrency
  2 on 2026-08-03; on 2026-08-05 it ran clean at concurrency 4 and needed concurrency 16 to reject.
- **Pool depletion dominates payload size.** A paired experiment (same concurrency, same request
  count, 5-token vs 20,000-token payloads) gave 12% then 50% in one order and 19% then 75%
  reversed - the SECOND arm is punished either way. **The experiment could not establish whether DSQ
  meters requests or tokens**, and no claim that it does is made anywhere in this plan.

### What the documentation says

- DSQ is capacity-based: _"there are no predefined quota limits... dynamic shared quota (DSQ)...
  serves incoming requests by distributing available capacity among all customers using that
  specific model and region."_ Tier limits are a guaranteed TPM **floor, not a ceiling**, and
  multimodal inputs carry their own TPM sub-limits - so TPM is the unit wherever Google publishes a
  number at all. (Official, MEDIUM confidence: the pages render as JS shells, so this is via
  search-grounded excerpts.)
- **Google says to avoid "sharp, second-level spikes"** even when the per-minute average is within
  budget, and that steady traffic is prioritised over bursty traffic.
- Recommended mitigations are exponential backoff with jitter and the `global` endpoint for a larger
  pool. **We already use `global`.**
- **The Gemini Developer API (ai.google.dev) is a different product** and DOES return
  `RetryInfo.retryDelay`. Verified that MRR AI runs `vertexai=True` with a service account, i.e. the
  true Vertex endpoint, so that signal is not available to us.
- OpenAI: RPM and TPM are both enforced, _"whichever occurs first"_; headers report remaining and
  reset on every response; **a rejected request still consumes quota**; the cookbook's parallel
  processor does proactive client-side pacing against both meters rather than only reacting to 429s.

### Prior art

Generic AIMD limiters exist (`mxcoras/aimd-limiter`, `gadget-inc/aimd-bucket`,
`Netflix/concurrency-limits`, AWS smithy's `AdaptiveRateLimiter` - a token bucket whose refill rate
tracks the success/throttle ratio). LLM-specific limiters found were all **static-config** RPM/TPM
buckets. No existing library does adaptive pacing against Gemini/OpenAI using their real error
bodies and headers, so this is a genuine gap rather than a wheel to avoid reinventing.

### The design that follows

1. **Two meters per (provider, model)** - requests and tokens - and a call must satisfy both. Correct
   whether DSQ meters requests, tokens, or both, which is exactly what could not be established.
2. **AIMD controller** adjusts both: halve on a 429, nudge up on success. The standard pattern for an
   opaque backend, and the only option when the provider publishes nothing.
3. **Header override for OpenAI.** Where a provider states its remaining capacity, use it rather than
   probing for a number it is already telling us.
4. **Near-zero burst on Vertex** (1s vs 4s for OpenAI), because Google explicitly warns against
   second-level spikes and a token bucket's average would hide exactly that shape.
5. **Ceilings are safety bounds, not targets.** The controller can only ever sit at or below them.
6. **Rejections are the feedback signal.** The controller must probe upward, so occasional 429s are
   expected by design; the retry layer absorbs them.

## 5. Retries, backoff, and rate limiting

Generalise `genai_retry.generate_with_retry` into `llm/retry.py`, preserving its existing shape
(the logic is sound; only the exception types and the retry-hint extraction are provider-specific).

**Retryable on OpenAI:** `RateLimitError` (429), `APIStatusError` with a 5xx, `APITimeoutError`,
`APIConnectionError`.
**Not retryable:** 400/401/403/404, and any 429 whose body indicates an exhausted quota rather than
a rate limit - the same carve-out `genai_retry` already makes for `PerDay` / `free_tier`, because
backoff cannot fix a spent budget inside one request.

**Honour `Retry-After`** when present, exactly as `_retry_delay_seconds` honours Gemini's
`RetryInfo`; fall back to full-jitter exponential backoff otherwise.

**Keep `_cancellable_sleep`.** The stop button depends on it: a bare `time.sleep` through eight
retries parks a job for minutes and makes cancel look broken.

**Rate limiting (decision 2).** `rate_limit` currently uses one global bucket
(`vertex:ratelimit:tokens`) with no model dimension, which is why flash traffic is throttled by
pro's scarcity. Key it per model: `llm:ratelimit:{provider}:{model}:{tokens,ts}`. Set OpenAI's cap
generously - we need 21.6 calls/min against 5,000 RPM. The bucket exists to stop a runaway loop,
not to pace a provider that publishes its own limits.

**TPM as a metric, not a gate.** Record `x-ratelimit-remaining-tokens` and
`x-ratelimit-remaining-requests` into `genai_metrics` on every OpenAI response. That gives a real
utilisation signal without the failure mode of a token estimator that guesses multimodal payloads
wrong.

## 6. Model selection: a ladder, not a default

Adrian asked what the documentation and other users recommend. Reporting honestly:

**OpenAI's own guidance is a method, not a model:** _"Optimize for accuracy until you hit your
accuracy target,"_ then choose the cheapest and fastest model that holds that accuracy. Their worked
example steps gpt-4o down to gpt-4o-mini.

**Documented tier positioning** (current frontier family, all 1.05M context, all vision, all
structured output, knowledge cutoff 2026-02-16):

| Model           | OpenAI's description                                               |
| --------------- | ------------------------------------------------------------------ |
| `gpt-5.6-sol`   | "Frontier model for complex professional work" - complex reasoning |
| `gpt-5.6-terra` | "Balances intelligence and cost"                                   |
| `gpt-5.6-luna`  | "Optimized for cost-sensitive workloads" - high volume             |

Structured Outputs documentation recommends **gpt-5.6 for new projects**, and notes strict mode
_"ensures responses adhere to supplied schemas"_ rather than merely producing valid JSON.

**Independent benchmarks: NOT RETRIEVED.** WebSearch errored throughout this session. I am not going
to cite community recommendations I could not open. Two things make that less costly than it sounds:

1. This is **not generic summarization.** It is constrained extraction from OCR'd scans with a fixed
   point set, bold-heading rules, capitalisation rules, and per-category length conventions. Public
   summarization benchmarks measure prose quality on clean news text and would not predict
   performance here.
2. **We already have a better evaluator than any public benchmark**: 55 eData deliverables frozen as
   `02_Extracted_Data/human_baselines.json` (2,115 entries) plus `90_Scripts/11_score_app_baseline.py`,
   scoring the exact axes we care about - length ratio per category, caps-run violations,
   normal-finding retention, billing-code leakage.

**So the plan ships a ladder, executable without code changes:**

1. Establish the accuracy ceiling with **`gpt-5.6-sol`** on a fixed document set.
2. Step down: **terra**, then **luna**, then **gpt-4.1-mini**.
3. Stop at the cheapest model whose score is within tolerance of the ceiling.
4. Record the chosen model per call type. The body call is the one that needs capability; title and
   audit may well sit two rungs lower.

Config keys, one per call type so the ladder can move them independently:
`SUMMARY_BODY_MODEL`, `SUMMARY_TITLE_MODEL`, `AUDIT_MODEL`, plus `SUMMARY_PROVIDER`
(`gemini` | `openai`) and `OPENAI_MAX_RPM`.

**No default model.** Selecting the OpenAI provider without the three model keys set is a startup
error. A silent default is how an unvalidated model reaches production.

**Expect a dip before a gain.** The category prompts were tuned against Gemini's failure modes over
a dozen PRs; `HARDENING_PREAMBLE` exists because of what Gemini did. Re-tuning is part of the work,
not a sign the port failed.

## 7. Configuration and the compose trap

Every new key must be named in `docker-compose.yml`'s `x-backend-env` block or it will **not reach a
container** - the trap that made `VERTEX_MAX_RPM` inert until #64 and `PIPELINE_WORKERS` inert until
#72. New keys: `SUMMARY_PROVIDER`, `SUMMARY_BODY_MODEL`, `SUMMARY_TITLE_MODEL`, `AUDIT_MODEL`,
`OPENAI_API_KEY`, `OPENAI_MAX_RPM`, `OPENAI_ZDR_ACKNOWLEDGED`.

`OPENAI_API_KEY` is a secret: it goes in `.env` (git-ignored) and is referenced, never defaulted, in
compose. `.env.example` gets the key name and a comment, never a value.

Dependency: add `openai` to `backend/pyproject.toml`. It is not currently installed.

## 8. Acceptance (EARS)

- WHEN `SUMMARY_PROVIDER=openai` and any of the three model keys is unset, THE SYSTEM SHALL refuse
  to start with a message naming the missing key.
- WHEN `ENVIRONMENT=prod` and `SUMMARY_PROVIDER=openai` without `OPENAI_ZDR_ACKNOWLEDGED`, THE
  SYSTEM SHALL refuse to start.
- WHEN a summary body is generated with the OpenAI provider, THE SYSTEM SHALL send the page images
  and OCR text in the same order the Gemini path uses (images, then OCR text, then instruction).
- WHEN an OpenAI call returns 429 with `Retry-After`, THE SYSTEM SHALL wait that long before
  retrying, and SHALL count the rejection in `genai_metrics` against that model.
- WHEN a job is cancelled during retry backoff, THE SYSTEM SHALL abandon the sleep within one
  second.
- WHEN the audit returns under strict schema, THE SYSTEM SHALL treat a null `fixed_title` exactly as
  today's absent key.
- WHEN an OpenAI reply stops at the token cap, THE SYSTEM SHALL set `truncated` true, so the row is
  flagged rather than stored as a finished summary.
- WHEN `SUMMARY_PROVIDER=gemini`, THE SYSTEM SHALL behave exactly as before this change.

## 9. Tests (none need a live provider - stub the client seam)

1. Gemini provider still produces byte-identical request shapes for body, title and audit
   (regression guard on the port).
2. OpenAI provider builds a `system` message plus base64 image parts in images-then-text order.
3. `Retry-After: 5` on a 429 produces a ~5s wait, not exponential backoff.
4. A non-retryable 400 raises immediately without consuming the retry budget.
5. Cancellation during backoff raises `JobCancelled` within one second.
6. Truncation maps from OpenAI `finish_reason == "length"` to `truncated=True`.
7. Strict schema round-trips: null `fixed_title` yields no title change.
8. Missing model config with `SUMMARY_PROVIDER=openai` fails startup.
9. Rate-limit bucket is keyed per provider+model: pro and flash do not share tokens.
10. `x-ratelimit-remaining-*` headers land in `genai_metrics`.

## 10. Validation loop

```bash
docker compose -f docker-compose.dev.yml up -d postgres redis
cd /c/src/mrr-ai && set -a; . ./.env; set +a
export DATABASE_URL="postgresql+psycopg://mrr:mrr_dev_only@localhost:5432/mrr"
cd backend && uv sync --extra docs && uv run alembic upgrade head
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

No migration. Backend only, so no `ng build` / `ng test`. The CI gate is "Ruff lint + **format**
check" - `ruff check` alone is not enough.

## 11. Sequencing

1. `llm/` package with the Gemini provider only, migrating the three call sites. **Ships with zero
   behaviour change** and is independently revertable.
2. Per-model rate-limit keys + TPM metrics.
3. OpenAI provider, behind `SUMMARY_PROVIDER`, defaulting to `gemini`.
4. Run the model ladder against the frozen baselines.
5. Set the three model keys from the ladder result, then deploy.

Step 1 landing on its own is the point: if the abstraction is wrong, that is discovered while the
system still runs entirely on Gemini.

## 12. Out of scope

- Segmentation, classification, dedup, and DOI extraction stay on Gemini.
- No Batch API, ever, for PHI (section 2).
- Prompt re-tuning for the new model - expected, but it is a separate exercise driven by the ladder
  results.
- The 41% segmentation failure rate and the over-segmentation A/B: separate work, and both matter
  more for the 500k target than this does.
