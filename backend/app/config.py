"""Application settings (ported from the Flask mrr_ai/config.py).

Lazy via get_settings() so importing the package does not require the env to be present
(tests/tooling import freely; a real run reads .env). Required secrets have no default, so
instantiation fails fast if they are missing. Postgres + Redis + Vertex-only per the plan.
"""

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The pinned Gemini flash model: the Vertex default, and the step-down for the title and
# audit calls. Named once so a version bump is a single edit.
_GEMINI_FLASH_MODEL = "gemini-2.5-flash"

# The stages that can be routed to a backend independently. Named once so an override naming a stage
# that does not exist fails at startup instead of silently leaving that stage where it was.
#
# "summarize" covers the body, title and audit calls together: all three cross the same seam and all
# three take summary_thinking_budget today, so splitting them here would invent a distinction the
# code does not make.
_LLM_STAGES = (
    "summarize",
    "segment",
    "extract",
    "dedup",
    "classify",
    "verify",
    "doi",
    "deposition",
)
_LLM_BACKENDS = ("gemini", "openai", "vllm")

# The tightest page geometry the benchmark corpus actually contains, as a long edge in POINTS.
#
# Used by `_validate_doi_render_target` as the worst case, because a SMALLER declared box demands a
# HIGHER dpi to reach the same pixel target - so the smallest box is the one a dpi ceiling binds on
# first. Records 05, 06 and 10-14 declare ~605x790pt, an honest box over a 150 dpi source. The
# corpus's other two geometries declare the box EQUAL to the pixel count (1258x1631 and 2700x3455),
# so they fit at 57 and 27 dpi and no plausible ceiling reaches them at all - the exposure is
# specific to the honest-box records. US Letter (792pt) is marginally LESS demanding than this, and
# no record in the corpus actually has it; tests/test_rasterise.py parametrises all four.
_SMALLEST_PAGE_LONG_EDGE_PT = 790.0

# Stages that declare their OWN render target, and the setting carrying it. Both are here for the
# same reason: they read a SMALL field off the page - a labelled injury date, a printed transcript
# page number in a corner - where `summary_image_long_edge_px` (1024) was measured on SEGMENTATION,
# which judges page layout. `_validate_render_targets` walks this, so a third stage wanting its own
# target gets the boot guard by adding one line rather than by copying the arithmetic.
_STAGE_RENDER_TARGETS = {
    "doi": "doi_image_long_edge_px",
    "deposition": "deposition_image_long_edge_px",
}

# The two page caps that live as MODULE CONSTANTS rather than settings, mirrored here so the image
# guard can see them. `services/summary_doi` and `services/deposition_pages` import this module, so
# the dependency runs one way and config cannot read theirs;
# `tests/test_compose_passthrough.py::test_the_mirrored_page_caps_match_their_modules` ties each to
# its source, and fails here rather than there if they ever drift.
#
# They are constants ON PURPOSE and should stay that way: 10 is measured (raising the DOI read from
# 5 to 10 on 2026-07-31 was the single largest fix to missed injury dates), and making a measured
# number an env knob invites changing it without redoing the measurement.
_DOI_IMAGE_CAP = 10
_DEPOSITION_IMAGE_CAP = 6

# Destinations approved to receive PHI in production, as ORIGINS (scheme + host + port).
#
# WHAT THIS ACTUALLY PROVES, and it is less than it looks. The SSH tunnel terminates INSIDE the pod,
# so the app dials loopback and this origin is the same for every pod we will ever rent - pod IPs and
# SSH ports are assigned at creation and change every time, which is the reason the tunnel exists.
# So this check proves the app dialled the TUNNEL. It cannot prove where the tunnel went; whoever
# controls the tunnel controls that. Adrian chose an origin list on 2026-09-11 knowing this.
#
# The PATH is deliberately not compared: "/v1" against "/v1/" is not a difference in where PHI goes,
# and refusing a production boot over a trailing slash would be a self-inflicted outage. A different
# host or port IS a different destination, and a new pod on a new port SHOULD require an edit here.
_APPROVED_VLLM_ORIGINS = ("http://127.0.0.1:8000", "http://localhost:8000")


def _origin(url: str) -> str:
    """``scheme://host:port`` for a URL, or "" when it does not parse as one.

    The default port is filled in so that ``http://host`` and ``http://host:80`` compare equal -
    otherwise the same destination written two ways would be approved in one spelling and refused in
    the other, which teaches operators to widen the allowlist rather than to fix the URL.
    """
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:  # a non-numeric port; treat as unparseable rather than guessing
        return ""
    port = port or (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


@lru_cache(maxsize=16)
def _parsed_overrides(raw: str) -> tuple[tuple[str, str], ...]:
    """``LLM_BACKEND_OVERRIDES`` -> validated ``(stage, backend)`` pairs. Raises on anything unknown.

    A CACHED MODULE FUNCTION rather than state on ``Settings``, and the reason is worth recording.
    This was a ``PrivateAttr`` assigned during validation, which SonarCloud flagged as python:S5890 -
    the annotation said ``dict[str, str]`` while the value assigned at class creation is a
    ``ModelPrivateAttr``. Pydantic rewrites that, so the code behaved correctly and the complaint was
    still fair. Deriving on demand removes the mutable attribute as well as the warning, and a
    settings object with no derived mutable state is easier to reason about besides.

    Returns a TUPLE of pairs rather than a dict: an lru_cache hands every caller the same object, and
    a dict would let one of them mutate what the next receives.

    Cached on the raw string, which is short and changes only when the environment does. An entry
    that raises is not cached - the exception is simply re-raised on the next call, which is what we
    want for a validator.
    """
    overrides: list[tuple[str, str]] = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        stage, separator, backend = entry.partition("=")
        stage, backend = stage.strip().lower(), backend.strip().lower()
        if not separator or stage not in _LLM_STAGES:
            raise RuntimeError(
                f"LLM_BACKEND_OVERRIDES entry {entry!r} does not name a known stage; "
                f"expected 'stage=backend' with stage one of {list(_LLM_STAGES)}."
            )
        if backend not in _LLM_BACKENDS:
            raise RuntimeError(
                f"LLM_BACKEND_OVERRIDES sets stage {stage!r} to unknown backend {backend!r}; "
                f"expected one of {list(_LLM_BACKENDS)}."
            )
        overrides.append((stage, backend))
    return tuple(overrides)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"  # "prod" hard-requires Vertex (BAA)

    # The commit this image was built from, stamped onto every job (services/jobs.create_job).
    # Prompt fingerprints cover prompt TEXT; this covers the deterministic code that is NOT a prompt
    # and is otherwise unattributable - house_style, and the per-row context blocks summarize_engine
    # appends after the fingerprint is computed (_standalone_studies_block, _document_date_block,
    # _deposition_pages_block). Set by the Dockerfile from the GIT_SHA build arg.
    #
    # "unknown" is deliberate: a build made without the arg is honestly labelled rather than
    # silently mislabelled with a value that is not the code that ran.
    build_sha: str = "unknown"

    # Persistence + queue (local self-hosted; no cloud).
    database_url: str  # e.g. postgresql+psycopg://mrr:...@localhost:5432/mrr  (required)
    redis_url: str = "redis://localhost:6379/0"

    # Auth: cookie signing + the carried-over Flask-Security password salt (required).
    secret_key: str
    security_password_salt: str

    # PHI at rest; keep off any network share.
    upload_folder: str = "./uploads"
    tesseract_cmd: str = ""

    # Gemini routing. Vertex is the BAA-covered path; required in production.
    use_vertex: bool = Field(default=False, validation_alias="GOOGLE_GENAI_USE_VERTEXAI")
    gemini_api_key: str = ""
    google_cloud_project: str = ""
    # `global`, not a region. A Vertex 429 is Dynamic Shared Quota - capacity unavailable at that
    # moment rather than an exhausted regional allowance - and the global endpoint draws on a larger
    # pool, so it mitigates the 429s the original `us-central1` was chosen to avoid. That choice
    # rested on this project having no quota in `global`; overturned 2026-08-12, and the server has
    # run `global` since. This default only bites an environment that does not set the variable,
    # which is the one least equipped to diagnose the 429s it would get.
    google_cloud_location: str = "global"
    genai_model: str = ""
    summary_model: str = ""
    verify_model: str = ""
    # Classification is a short, structured enum task - the cheapest tier is enough and cuts
    # cost/latency vs full Flash. A separate knob so a quality regression reverts via env alone.
    classify_model: str = "gemini-2.5-flash-lite"
    # Summary body temperature. Extractive medical summarization wants determinism: an eval on real
    # duplicate sub-docs showed 0.0 makes repeat runs identical (0.8 varied down to ~0.26 similarity)
    # and cuts fabrication. Env-overridable so a regression reverts without a redeploy. (2.5-flash
    # only; Gemini 3 would want its default 1.0.)
    summary_temperature: float = 0.0
    # Output budget for one summary. 2048 cut long category-1 narratives off mid-sentence and the
    # partial reply was stored as if it were finished; 8192 fits the longest real notes with
    # headroom, well under 2.5-flash's 65k output ceiling. Env-overridable so a box can raise it
    # without a redeploy, and a reply that still hits the cap is flagged for manual check.
    summary_max_output_tokens: int = 8192
    # Output budget for the AUDIT pass, when it should differ from the body's. None means the
    # audit shares `summary_max_output_tokens` above, which is exactly the historical behaviour.
    #
    # The audit's reply has to hold a corrected copy of the WHOLE summary, and on a thinking model
    # the reasoning is billed against the same budget - so the two compete, and the longer the
    # summary the less is left for the answer. Measured on the box 2026-09-11 over the 1,155
    # summaries that requested an audit, bucketed by the length of the text the reply had to
    # reproduce (`verified_text` else `text`): 36 (3.1%) never got one, ALL 36 delivered, and the
    # rate tracks that length - 0.6% below 1,000 characters, 2.6% at 1,000-2,000, 7.7% at
    # 2,000-4,000, and 60.0% above 4,000. A truncated audit is not an exception, so nothing retries
    # it; the summary ships unaudited and #290 is what puts that on the reviewer's screen.
    #
    # State the BASIS with the number. An earlier note put the top bucket at 69.2% off a different
    # length column, which is the same data disagreeing with itself for want of one clause.
    #
    # `verify_summary` has taken an override since #285 and the benchmark harness passes one. This
    # is the app side of that knob: without it the one production caller cannot opt in, so the
    # tuning experiment cannot be run here at all. Deliberately NOT given a value - which way it
    # should move is an open question with real evidence on both sides (the benchmark lowered its
    # cap to bound what a runaway costs; the failure curve above suggests long summaries are
    # starved rather than extravagant), and that call wants a measurement, not a default.
    audit_max_output_tokens: int | None = None
    # Summary faithfulness verify pass: a second temp-0 call rewrites each summary to drop
    # statements unsupported by / contradicting its OCR source (problem #3), keeping the raw output
    # too. On by default; a regression reverts via env with no redeploy. Distinct from the
    # segmentation verify_* knobs below.
    summary_verify: bool = True
    # Summarization also sends the page IMAGES alongside the OCR text (multimodal): the images
    # recover tables, checkboxes, and handwriting the OCR garbles (an eval on real sub-docs showed it
    # adds missing vitals/allergies with no loss). Env-toggle to revert to OCR-only.
    summary_multimodal: bool = True
    # Cap page images per sub-document so a long record cannot blow the request payload/latency; the
    # full OCR text still covers every page beyond the cap.
    summary_image_max_pages: int = 15
    # DPI for the summary page images (lean JPEG); 120 was enough to read tables/handwriting in the eval.
    summary_image_dpi: int = 120
    # Ceiling on the rendered LONG EDGE in pixels, applied by lowering the DPI per page. A DPI alone
    # is not a resolution: it only means something against a page's declared box, and a scanned PDF
    # can declare anything. Measured across the 17-record benchmark corpus, three geometries exist -
    # ~605x790pt pages (a real 150 dpi source), ~1258x1631pt pages, and 2700x3455pt pages. The last
    # two declare their box EQUAL to their pixel count, i.e. 72 points per inch, so asking for 120 dpi
    # magnifies those scans 1.67x and costs 2.8x the vision tokens for no extra information at all.
    #
    # Consequence before this cap existed: summarize images cost ~6,100 tokens each instead of ~850,
    # and the largest measured payload reached 121,306 tokens - against a 128,000-token model, with
    # 8,192 of that reserved for output. The OCR text leg is uncapped on top.
    #
    # 1024 is not a new number. It is the segmentation render target, chosen on 2026-08-17 by A/B
    # against ground truth (+32.6% throughput AND better exact F1, 0.708 vs 0.676, winning 6 of 7
    # records) and recorded in 03_Reports/RESULTS_2026-08-17_122b_capacity.md. Segmentation expresses
    # it as a pixel target and was therefore immune to all of the above; this makes summarization
    # agree. Changing it is not a local decision - see that report first.
    summary_image_long_edge_px: int = 1024
    # Dynamic thinking (-1) for the summary body. Originally forced: 2.5-pro REJECTS the seam's
    # default thinking_budget=0 with a 400. It stays at -1 under 3.5-flash for a DIFFERENT reason -
    # the 2026-08-14 scoring arm that selected 3.5-flash ran with -1, so the quality measurement only
    # holds at this value. 3.5-flash accepts 0 as well, which makes "the body is a flash tier now, so
    # step this down" a tempting cleanup. Do not, without re-scoring: two silent thinking_budget=0
    # bugs have already shipped in this codebase.
    summary_thinking_budget: int = -1
    # Output budget for the isolated DOI read. WAS 200, INLINE, and that was Google's documented
    # anti-pattern rather than a tight-but-fine number: "max_output_tokens ... INCLUDING THOUGHT
    # TOKENS", and "If the model hits this limit while reasoning, it stops generating ... and
    # returns truncated or empty output" (ai.google.dev/gemini-api/docs/thinking). Thinking on this
    # call is dynamic via summary_thinking_budget above, so the whole 200 could go on thought - and
    # `_clean("")` then returns "-", the value that MEANS "this document states no injury date".
    # In scripts/backfill_doi.py that STRIPPED a correct injury date out of a stored medical-legal
    # summary, which is precisely what that function's `strict` flag exists to prevent.
    #
    # 2048 because the answer is a date - 30 tokens at the very most - so effectively all of it is
    # thinking headroom. NOT measured: nothing in this repo persists usage_metadata, so no cap here
    # can be derived from the live distribution. See summary_verify.py, where finding out its cap
    # was ELEVEN TIMES too generous took a dedicated 139-audit study; the doctrine recorded there is
    # the one applied here - "The cap does not prevent a runaway; it bounds what one costs."
    # A reply that hits this cap now RAISES rather than quietly reporting "-".
    doi_max_output_tokens: int = 2048
    # Long-edge pixel target for the DOI read's page images, used ONLY on the vLLM path, where the
    # PDF cannot be sent at all. Deliberately NOT summary_image_long_edge_px (1024): that figure was
    # measured on SEGMENTATION, which judges page layout, whereas this reads a small labelled date
    # field. Different task, different number - exactly as ocr_base_dpi (200) is already a separate
    # number for reading text off these same pages.
    #
    # 1300 is chosen against Qwen3-VL's documented arithmetic: one visual token covers 32x32 px, so
    # a letter page at a 1300px long edge is ~1004x1300 = 1.305M px = ~1274 tokens, landing on the
    # ~1280-token upper end its guidance recommends for small-field reading. It renders at ~118 dpi,
    # so a 10pt form label is ~16px tall against a documented legibility floor of ~10-12px; at 1024
    # it is ~13px, i.e. AT that floor. Cross-check that the arithmetic describes OUR renders: at
    # 1024 the same formula predicts 791 tokens and llm/tokens.py measures 827, 4.5% apart.
    #
    # NOTE THE CEILING, because raising this alone does nothing: summary_image_dpi (120) caps
    # page_dpi, so a letter page tops out near 1320px however large the target. 1300 sits under that
    # by design. To go higher, raise summary_image_dpi with it.
    doi_image_long_edge_px: int = 1300

    # Output budget for the transcript page-number read. WAS 400, INLINE, and it is the same
    # anti-pattern doi_max_output_tokens above documents: thinking is dynamic here too (the call
    # passes summary_thinking_budget), and "max_output_tokens ... INCLUDING THOUGHT TOKENS" means
    # the whole 400 can go on thought. The answer is six small objects - about 100 tokens - so the
    # rest was never the point.
    #
    # THE CONSEQUENCE IS NOT THE DOI READ'S, and that difference is worth stating because it is why
    # the fix took a different shape. A truncated reply here is invalid JSON, so `json.loads` raises
    # and the fail-safe logs; nothing is written to storage on the sentinel. It costs a deposition
    # its page citations rather than deleting a stored date. Adrian chose to raise on truncation
    # anyway, against the recommendation to keep the "never raises" contract, so the sentinel now
    # only ever means "the offset could not be established".
    #
    # 2048 mirrors doi_max_output_tokens for the same reason: with truncation now DETECTED rather
    # than silent, an over-generous cap fails loudly if it is ever wrong.
    deposition_max_output_tokens: int = 2048

    # Long-edge pixel target for the transcript page-number read's images, used ONLY on the vLLM
    # path. A printed page number sits in a corner in small type, so this is the same small-field
    # read as the injury date and inherits 1300 on the same Qwen3-VL arithmetic - see
    # doi_image_long_edge_px above for the derivation and the cross-check.
    #
    # ITS OWN SETTING rather than reading doi_image_long_edge_px, because a setting named for one
    # stage must not silently drive two: tuning the DOI read would otherwise retune this one, which
    # is exactly the coupling summary_image_dpi->doi_image_long_edge_px already caused once.
    # `_STAGE_RENDER_TARGETS` is what gives it the same boot guard.
    #
    # NOT MEASURED on our own pages. 1300 is transferred reasoning, not a reading of whether a
    # transcript's printed corner number is legible at that render. ISSUE #333 carries what to run
    # and is to be resolved on the next rented pod. `_MIN_AGREEING` is 2, so a partial legibility
    # failure surfaces as None - no citations - rather than as a wrong page number.
    deposition_image_long_edge_px: int = 1300

    # Which BACKEND answers a model call: "gemini", "openai" or "vllm".
    #
    # This supersedes summary_provider, which selected a vendor for the SUMMARIZE STAGE ONLY. Seven
    # other services call google-genai directly and never consulted it, so "point the app at another
    # model" was not expressible at all - setting GENAI_MODEL to a non-Gemini name also silently
    # retargets the verify pass, because verify_model derives from it below.
    #
    # A separate key rather than a third value of summary_provider, because the two answer different
    # questions: that one scoped a vendor to one stage, this one scopes a backend to the pipeline.
    llm_backend: str = "gemini"
    # Per-stage overrides as comma-separated "stage=backend" pairs, e.g. "segment=gemini,classify=gemini".
    # Empty means every stage follows llm_backend.
    #
    # A single global flag would make the only available move all-or-nothing, and a SPLIT outcome is
    # the likely one rather than the exotic one. Measured on the 2026-09-11 gate: Qwen3.6-35B-A3B-FP8
    # scored level with Gemini on segmentation (B 0.690 vs 0.670, inside a 19.1% noise floor) but
    # inverted the error DIRECTION - under-segmenting 0.85:1 where Gemini over-segments 1.93:1 and
    # production runs 92:8 toward over-segmentation - and trailed on categorization, 77.9% against
    # Gemini's 82.9% (McNemar exact p = 0.0884 on 176 paired rows, not significant). Being unable to
    # hold one stage on Gemini while moving the rest would turn a per-stage judgement into one bet.
    #
    # Parsed and VALIDATED in _derive: an unknown stage name or backend refuses startup rather than
    # being ignored. A typo that silently leaves a stage on the old backend is exactly the failure
    # this key exists to prevent.
    llm_backend_overrides: str = ""

    # Self-hosted vLLM. Its own settings rather than reusing the openai_* ones, even though the wire
    # dialect is OpenAI's: sharing them would put one key in charge of choosing between a public API
    # and our own box, and the guard that matters differs (ZDR approval for OpenAI, an approved
    # destination for vLLM).
    vllm_base_url: str = ""
    vllm_api_key: str = ""
    # NO DEFAULT, for the same reason the three OpenAI model keys have none. A vLLM server serves
    # exactly ONE model and the name must match what is loaded, so a default here 404s against any pod
    # serving anything else. `_derive` refuses to start when a resolved backend is vllm and this is
    # unset. The served name IS stable across pods because we set it explicitly - see .env.example.
    vllm_model: str = ""
    # Read deadline in SECONDS, and much longer than the Gemini path's 120s because it is a different
    # kind of limit. genai_http_timeout_ms is forwarded to Vertex as a SERVER-side deadline; this one
    # is purely client-side, and the pod has no proxy in front of it since the SSH tunnel replaced
    # RunPod's Cloudflare-fronted HTTP proxy and its hard 100-second ceiling. Uncapped segmentation
    # windows were measured at 179s, so 120 here would cut real work off mid-call.
    vllm_read_timeout_s: float = 600.0
    # Pacer ceilings, DISABLED (0) deliberately until a trustworthy number exists.
    #
    # Both meters at 0 makes pacing.acquire admit immediately. That is the honest state: vLLM QUEUES
    # instead of returning 429, so the AIMD controller never sees a rejection and can never lower the
    # rate - which leaves the ceiling as the SOLE control, and a wrong ceiling worse than none. Every
    # throughput figure we hold describes an unconstrained sweep (it sent no response_format) with
    # prefix caching active at a 13.0% hit rate, so none of them can set this yet. Derive from the
    # corrected sweep, then set both.
    vllm_max_rpm: int = 0
    vllm_max_tpm: int = 0
    # CVE floor for the served vLLM. Recorded here so the requirement is visible and overridable; it
    # is ENFORCED by a live probe of the server's GET /version during startup, NOT in _derive -
    # Settings is constructed by pytest, alembic and every eval script, none of which can reach a pod.
    # GET /version is unauthenticated and returns {"version": "..."} (verified on the 0.28.0 build).
    vllm_min_version: str = "0.24.0"
    # Comma-separated approved destination origins, honoured ONLY outside production - see
    # _approved_vllm_origins for why prod ignores rather than rejects it.
    vllm_approved_origins: str = ""
    # MIRRORS the pod's `--limit-mm-per-prompt {"image":40}` and must be changed with it. This is not
    # a free parameter: its only job is to equal the serve flag, so that `vllm_segment_max_pages`
    # below can be checked against something real at boot. If the harness changes that flag and this
    # does not follow, `_validate_vllm_backend` still READS like a check while proving nothing -
    # which is worse than having no check, because it invites trust.
    vllm_max_images_per_prompt: int = 40

    # Which vendor answers the summarize stage's calls (body, title, audit). "gemini" is the current
    # behaviour and stays the default: the provider abstraction landed first specifically so it could
    # ship without changing which model runs. Switching this to "openai" additionally requires the
    # per-call model keys and, in production, the ZDR acknowledgement - see services/llm/.
    summary_provider: str = "gemini"
    # Per-call-type models for the three summarize-stage calls. On the GEMINI path `_derive` fills
    # these in (body = summary_model, title and audit = gemini-2.5-flash). This began as a cut from
    # three 2.5-pro calls per row to one; the body moved to 3.5-flash on 2026-08-14, so the saving is
    # smaller now, but the split stands on its own - a title is extraction and an audit is a check,
    # and neither needs the body model whatever that happens to be.
    #
    # On the OPENAI path there is NO default, deliberately: OpenAI's own guidance is to fix an
    # accuracy target on the most capable model and then step down to the cheapest that still hits it.
    # A silent default is how an unvalidated model reaches production, so selecting that provider
    # without setting all three stays a startup error - see the check in `_derive`.
    #
    # They are separate keys because the three calls need different capability: the body call reads
    # page images and applies a long format spec, while the title is extraction from OCR text and the
    # audit is a check. Title and audit may well sit a tier or two below the body.
    summary_body_model: str = ""
    summary_title_model: str = ""
    audit_model: str = ""
    # The model the BODY call falls back to when Vertex will not serve the configured one. Only ever
    # reached after `genai_retry.generate_with_retry` has spent its whole budget and is still getting
    # 429s, and only for the body - the title and audit run flash already, so there is nowhere below
    # them to go. Set to "none" to disable and let the row fail instead - "" cannot mean disabled,
    # because an unset key is also "" and that has to resolve to the default.
    #
    # This exists because the body left 2.5-pro for availability, not quality: on 2026-08-13 Vertex
    # refused 2.5-pro for this project outright, 0 of 8 on the configured endpoint, and every
    # summarize job failed. It recovered by itself the next day. That can recur without warning, so a
    # hard-pinned pro needs somewhere to land. Resolved in `_derive` rather than defaulted here so the
    # openai branch still sees "" for an unset key.
    summary_body_fallback_model: str = ""
    openai_api_key: str = ""
    # PHI gate. A signed BAA is NOT sufficient on its own: OpenAI additionally requires Zero Data
    # Retention (or Modified Abuse Monitoring / Eyes Off) approved on the ORGANIZATION. Confirmed
    # 2026-08-05 as the org default. This flag is an explicit human acknowledgement of that, so a
    # future box cannot start sending records to an org whose retention setting nobody checked.
    openai_zdr_acknowledged: bool = False

    # Concurrency + retry (become RQ worker knobs in P4; caps guard the shared Vertex quota).
    #
    # Row-level concurrency inside ONE summarize job. 2 -> 5 on 2026-08-25, and 5 is not a new number:
    # it is the value `docker-compose.yml`'s own note computes and then does not use. That note says,
    # measured 2026-08-03, "two concurrent chains at ~15.4s per call is exactly 7.8/min ... by Little's
    # Law the saturation point for a 20 rpm budget is 20/60 * 15.4 = ~5 concurrent rows, so 5 fills the
    # existing budget without raising it." The conclusion was 5; the value stayed 2.
    #
    # And the budget it was computed against has since TRIPLED: VERTEX_MAX_RPM was 20 then and is 60
    # now. So the setting is sized for a ceiling that no longer applies.
    #
    # Measured on the box 2026-08-25 over the 43 newest summarize jobs: 11.3h producing 1,851
    # summaries, i.e. 22.0s per summary at 2 lanes, which is ~14.7s per call and matches the 15.4s
    # above. One job therefore draws about 8 calls/min against a 60 rpm ceiling - roughly 14%
    # utilisation. Summarize is the second-largest stage in the pipeline (11.3h against segment's
    # 15.9h), so this is where the remaining wall-clock is.
    #
    # 5 RATHER THAN MORE, and the ceiling is not the reason to stop. The compose note names the real
    # failure mode: `rate_limit.acquire()` abandons its wait after MAX_ACQUIRE_WAIT_S (300s) and
    # proceeds ANYWAY, so enough queued callers stop being rate limited at all. Its own arithmetic put
    # 3 summarize workers x 5 chains against 20 rpm at a ~30s mean wait, well inside the abandon
    # threshold - and at 60 rpm that wait is shorter still. Going past 5 leaves the regime the note
    # measured; 5 is the value it endorsed at a stricter ceiling than we now run.
    #
    # LEFT AT 2, AND THE REASON IS A RACE THIS CHANGE EXPOSED rather than the ceiling.
    #
    # Raising it to 5 was written, tested and reverted on 2026-08-25. `summarize_document` consumes
    # results through `drain_pool`, which is `as_completed` - COMPLETION order, not submission order -
    # and the give-up condition is `generated == 0 and transient_failures >= giveup_after_failures`.
    # So whether a job ENDS or PAUSES depends on whether N failures happen to complete before the
    # first success, and with more lanes in flight that becomes likely rather than rare. Measured: at
    # 5 lanes, `test_summarize_does_not_give_up_once_a_row_has_succeeded` and
    # `test_a_notice_row_is_not_counted_as_proof_the_model_answers` fail on 3 of 6 runs and pass on
    # the other 3.
    #
    # That is the wrong outcome by the tests' own statement of intent: a document where the model IS
    # answering some rows should PAUSE and retry the rest, not be ended as though the model were
    # refusing everything. The bug is latent at 2 as well - it is a race, not a threshold - but the
    # concurrency is what makes it probable.
    #
    # So the throughput is available and it is not takeable yet. Fix the give-up decision first so it
    # does not depend on completion order, then raise this. Reverts by env with no rebuild either way,
    # and `vertex:metrics:*` in Redis is where the pacer records admission.
    #
    # UPDATE: that race is fixed - `summarize_document` now records the give-up as a CANDIDATE and
    # decides after the loop, so the end-versus-pause outcome no longer depends on completion order,
    # pinned at 1/2/5/8 lanes (#155).
    #
    # 2 -> 5 on 2026-08-31, which is the number the note above computes and then does not use. Held
    # back once more after the fix because one full-file run in 18 had failed in a way that would not
    # reproduce, and the bar set here was "a clean reason for that one run, not more throughput
    # evidence". Both halves are now answered.
    #
    # The reason: the pool cannot be lane-sensitive. It submits `summarize_row` and NOTHING else -
    # every DB write happens in the main thread as `drain_pool` yields, and the summarize path holds
    # no mutable module-level state at all (prompts and schemas are constants; pacer state lives in
    # Redis, where it is atomic). So a row's work is a pure function of its arguments plus the
    # network, and the failure was in the harness rather than in the product.
    #
    # The evidence, so the absence means something against a 1-in-18 event: 39 runs of
    # `tests/test_jobs.py` at 5 lanes and 3 full suites, zero failures.
    #
    # The throughput, re-derived on fresh data because the original arithmetic was against
    # VERTEX_MAX_RPM=20 and the ceiling is 60 now. One 229-page record, 56 rows, 2026-08-31:
    # 1,065s / 28 rows per lane = 38.0s per row = 12.7s per call, against #154's 14.7s over 43 jobs.
    # 168 calls in 1,065s is 9.5/min, 16% of the ceiling. At 5 lanes: ~426s (2.50x) and 39% of the
    # ceiling - inside the regime the note above measured, and well short of the abandon-the-wait
    # failure mode that is the reason not to go further.
    #
    # Reverts by env with no rebuild, and `vertex:metrics:*` in Redis is where the pacer records
    # admission if this ever needs re-checking under real load.
    pipeline_workers: int = 5
    # Bound on "pause and auto-resume forever": when this many rows have failed transiently and NOT
    # ONE has succeeded, the model is refusing everything and resuming only replays the same wall.
    # Zero successes is the discriminator, not the failure count alone - a blip with some rows getting
    # through must still pause and retry. 3 is deliberately small because zero-of-three is already a
    # strong signal: on 2026-08-13 job 1000173 ground for 96 minutes against a 0/8-admitted model,
    # then died as an unclassified rq.timeouts.JobTimeoutException, so the reviewer waited an hour and
    # a half to be told nothing. Measured 2026-08-14: admission recovered to 8/8 on its own, so this
    # condition is transient and external - which is exactly why a job must not wait it out.
    summarize_giveup_after_failures: int = 3
    classify_workers: int = 4
    # Injury-date reads at the END of segmentation: one isolated vision call per sub-document, so
    # they parallelise like categorization does. Its own knob rather than borrowing another
    # stage's, following segment_window_workers / classify_workers / page_text_workers.
    doi_workers: int = 4
    # RQ per-job wall-clock cap (seconds). The old Flask app ran the pipeline in-process with no
    # cap; RQ's 180s default is far too short - a 200+ page record needs minutes per vision window
    # plus one Vertex call per identified document. The effective cap is SIZE-AWARE:
    # max(job_timeout, page_count * job_timeout_per_page), so a small record still fails fast while a
    # 2600-page record gets hours. Tune via JOB_TIMEOUT / JOB_TIMEOUT_PER_PAGE.
    job_timeout: int = 3600
    job_timeout_per_page: float = 20.0
    # Within-request transient retries at the genai seam. Bumped 6 -> 8 so a brief shared-quota
    # 429 / 5xx burst rides out inside a single call on the NON-resumable paths (segmentation /
    # verify / classify, which have no pause/resume); a sustained outage still exhausts and fails
    # the job with a friendly terminal message rather than hanging.
    genai_max_retries: int = 8
    genai_retry_base_delay: float = 2.0
    genai_retry_max_delay: float = 30.0

    # Per-attempt deadline (ms) for the Vertex/genai client. google-genai defaults to no timeout, so
    # a stalled call blocks forever.
    #
    # This is NOT only a client-side guard: google-genai forwards it to Vertex as the SERVER-side
    # deadline, so exceeding it returns a server 504 DEADLINE_EXCEEDED, not an httpx timeout (proven
    # 2026-08-12 - an 8000ms value produced a server 504 at 6.2s). It therefore caps how long ONE
    # call may legitimately take, and a 504 is deliberately NOT retried because the same deadline
    # binds every attempt (see services/genai_retry).
    #
    # Left at 120s deliberately, and now on evidence rather than assumption. The old comment claimed
    # "120s covers a large vision window"; that was false - an uncapped 241-page window needed 179s.
    # What makes 120s correct is window_max_pages, which caps a window at 100 pages. The measured
    # curve (windows.py) is 160 pages -> 54.5s, 200 -> 106.3s, 241 -> 179.0s, so a capped window sits
    # comfortably inside even the 160-page point. A deadline above 120s IS honoured (the 179s call
    # completed under a 600s deadline), so raising this remains available if a capped window ever
    # runs long.
    #
    # CORRECTED 2026-09-16: this said "capped at 160 pages". The cap is 100 and has been for some
    # time; 160 is a point on the measurement above, not the cap. Safe direction, wrong record.
    genai_http_timeout_ms: int = 120000
    # Per-1,000-estimated-input-tokens allowance that SCALES the deadline above, in ms. The pair
    # works exactly like `effective_job_timeout` below - a flat floor for ordinary requests, scaling
    # for large ones - and for the same reason: a fixed limit is only safe when something else
    # bounds the request, and on this path nothing does.
    #
    # That is the whole defect. 120s was set on evidence, but the evidence is about SEGMENTATION,
    # where `window_max_pages` caps a window at 100 pages and the measured curve stays inside it.
    # A SUMMARIZE row is however many pages the segmenter drew, and nothing bounds it - so the
    # larger the record, the likelier a row exceeds a limit chosen for a bounded request.
    #
    # 3800 is calibrated, not guessed. Job 1000308's lost row measured 31,394 estimated tokens
    # (33,058 OCR characters, a 14,520-character system prompt, and 15 images at 1,300 each), and
    # 120s over that is 3,822 ms per 1k. So this value REPRODUCES today's deadline at the largest
    # size we have measured and scales from there: twice the row, twice the allowance. Nothing that
    # fits today gets a shorter deadline, because the floor above still applies.
    #
    # Deliberately NOT capped. A ceiling is the wall this removes, and one already exists a level
    # up: `pool_timeout` abandons a stalled pool just under the RQ job timeout, so a pathological
    # request is bounded there rather than by a number guessed here.
    genai_timeout_per_1k_tokens_ms: int = 3800
    # What ONE retry gets after a 504, as a multiple of that call's own deadline. 0 disables the
    # retry, which is the behaviour before 2026-09-11: a deadline failed the row outright.
    #
    # Scaling alone does not cover this. Job 1000308's row was NOT too large for its deadline -
    # re-run
    # three times on 2026-09-11 it took 51.7s, 50.1s and 77.5s against a 120s limit. It went
    # over 120s
    # once, transiently, and was lost permanently for it. So a 504 is not the purely deterministic
    # event `errors.is_deadline_exceeded` described; at ~1.5x headroom a slow moment tips a normal
    # row over, and the retry is what recovers it.
    #
    # The two settings answer two different causes and neither substitutes for the other: scaling
    # gives a genuinely large request enough time on the FIRST attempt, the retry recovers a normal
    # request that hit a slow moment.
    genai_deadline_retry_multiplier: float = 2.5

    # Per-call OCR (Tesseract) wall-clock cap (seconds). A hung/oversized page is killed and
    # skipped rather than blocking a worker thread forever (the concurrent-OCR deadlock backstop;
    # OMP_THREAD_LIMIT=1 in compose is the primary fix).
    ocr_timeout_seconds: int = 120

    # Rasterization DPI for OCR. 200 was never a decision - pdf2image's default applied because
    # _rasterize was called with no dpi at all. Explicit now so it is visible and tunable.
    ocr_base_dpi: int = 200

    # Optional cap on the rendered long edge in pixels; 0 DISABLES it (the default, deliberately).
    # When set, the DPI is lowered so an oversized page fits, and never raised.
    #
    # It is off because capping was measured on 2026-08-19 and DID NOT PAY. On a 2700pt page (7500px
    # at 200 DPI) a 3500px cap cut OCR from 7.4s to 1.5s - 4.2x - but over 20 such pages it lost 6.0%
    # of recognized characters, one page dropping 59%. Raising the cap did not recover it: at 6500px
    # (DPI 135, only 1.7x faster) the loss was still 3.8%. The premise that upsampling an oversized
    # page is pure waste is WRONG - at 72 DPI an 8pt glyph is 8px tall, below what Tesseract needs to
    # resolve, so the extra pixels buy real accuracy.
    #
    # That missing instrument now EXISTS - scripts/eval/ocr_cap_word_recall.py (#237) - and its
    # first run makes the case against capping far stronger than the character count did. On a
    # 182-page record whose every page is 3456pt, 12 pages sampled, against an uncapped reference:
    #
    #   cap    speed    pooled word recall    worst page    below 0.90    invented words
    #   3500   7.81x                 0.684         0.000       11 of 12               352
    #   6500   2.04x                 0.708         0.125       11 of 12               563
    #
    # So the real cost is 32% of the WORDS, not 6.0% of the characters - character volume
    # understated it fivefold, which is the same trap that made the PDF-text-layer experiment look
    # like a win (see the note atop services/ocr.py). Two pages returned essentially nothing at all.
    #
    # And it kills the obvious follow-up. "Cap only the expensive pages" cannot work here: the two
    # slowest reference pages (120.5s and 41.4s) are exactly the two the cap destroyed, scoring
    # 0.000 and 0.042. The speedup is concentrated precisely where the loss is total.
    #
    # Measured on one document class, so it does not prove a cap can never pay anywhere. It does
    # mean nobody enables this without running that script on the records they care about first.
    ocr_max_long_edge_px: int = 0

    # Safety margin (seconds) subtracted from the size-aware job_timeout to bound every
    # ThreadPoolExecutor drain (see pool_timeout). The pool wait always fires JUST before RQ's
    # SIGKILL, so no as_completed() waits unbounded, yet it scales with page count.
    future_timeout_margin_seconds: int = 120

    # Resumable summarize (item 7): after this many CONSECUTIVE transient failures (shared-quota
    # 429 / 5xx / disconnect) the run stops mid-batch, saves progress, and schedules a resume this
    # many seconds later - retrying the remaining rows forever until quota frees up. Only transient
    # pressure pauses; a permanent failure ends the run "needs attention" instead.
    summarize_pause_after: int = 3
    summarize_resume_delay: int = 60

    # Thinking tokens are pure overhead for our structured extraction calls, and on 2.5-flash they
    # silently consume max_output_tokens. Default OFF (budget 0); set >0 or -1 (model-dynamic) via
    # env to re-enable if a task regresses. Applied centrally at the genai seam.
    gemini_thinking_budget: int = 0

    # Segmentation is the exception: an A/B on labeled cases showed thinking-OFF regresses strict
    # doc-F1 (it over-segments more), so the segmentation window call keeps dynamic thinking (-1)
    # while every other call inherits gemini_thinking_budget. Env-overridable.
    segment_thinking_budget: int = -1

    # Concurrency for the one-time per-page OCR pass (services/page_text.populate_document).
    # Deliberately its own knob rather than reusing CLASSIFY_WORKERS: this is pure Tesseract CPU on
    # the same box that runs the Vertex pacing work, and OMP_THREAD_LIMIT=1 in compose is what stops
    # concurrent tesseract processes deadlocking.
    #
    # 4 -> 6 on 2026-08-24, on measurement rather than on principle. This pass is roughly HALF the
    # wall-clock of a segment job - the same 297-page record segmented three times on 2026-08-17 took
    # 1,463s, then 810s and 715s once its page text was stored - and segmentation is the largest stage
    # in the pipeline (15.9h across 57 jobs, against 13.7h for summarize). Measured in the api
    # container over 32 sampled pages of a 2,673-page record, rasterizing and OCR-ing in memory:
    #
    #     threads   1      2      4      6      8
    #     seconds  60.2   30.7   17.0   12.7   10.9
    #     speedup  1.00x  1.96x  3.54x  4.75x  5.51x
    #
    # Character output was IDENTICAL at every setting and no page failed, so this is throughput and
    # not corruption, and the tesseract deadlock this comment warns about did not appear even at 8.
    # 4 -> 6 is 1.34x on the pass, so roughly 12% off the segment job.
    #
    # 6 rather than 8, which measured faster, because the box has 8 cores and also runs six RQ
    # workers, postgres, redis, api and web: 6 leaves two cores for everything else. The contention
    # risk was checked rather than assumed - up to FOUR segment jobs have run concurrently on the box
    # (21 overlapping pairs historically), which at 6 threads each would be 24 threads on 8 cores. Per
    # page that shows no penalty today: jobs running alone averaged 3.64 s/page, with 2-3 others 4.47,
    # and the 4+ bucket 2.95, so there is no monotone contention effect to protect. If one appears,
    # PAGE_TEXT_WORKERS reverts this by env with no deploy.
    page_text_workers: int = 6

    # Global Vertex request ceiling (requests/minute) enforced by a Redis token bucket at the seam,
    # so the aggregate rate across every worker process never trips dynamic-shared-quota 429s. Tune
    # empirically: raise until near throttling, then back off ~20%.
    vertex_max_rpm: int = 60
    # Upper bounds for the adaptive pacer (services/llm/pacing.py). These are SAFETY BOUNDS, not
    # tuning knobs: an AIMD controller finds the working rate from 429 feedback and can only ever
    # sit at or below these. A fixed rate was measured to be unworkable - Vertex publishes no
    # remaining-capacity header and no RetryInfo, and the serviceable rate moved more than 4x
    # between 2026-08-03 and 2026-08-05.
    #
    # vertex_max_tpm is a bound, not a published figure: Google does not state a tokens-per-minute
    # limit for dynamic shared quota, and a paired experiment could not establish whether DSQ meters
    # requests or tokens (pool depletion dominated the result). Metering both is correct either way.
    vertex_max_tpm: int = 4_000_000
    # OpenAI publishes both per project and returns them on every response, so these are only the
    # cold-start bound; observe_limits() replaces them with the real values after the first call.
    # Measured on the account 2026-08-03: 5,000 RPM, 1M TPM flagship / 2M mini-nano tiers.
    openai_max_rpm: int = 5_000
    openai_max_tpm: int = 1_000_000

    # Independent segmentation windows run on a small thread pool (each still crosses the seam, so
    # the limiter caps the aggregate). Speed lever; keep modest so it does not dominate the quota.
    segment_window_workers: int = 3

    # Duplicate detection. `dupe_jaccard_threshold` is the candidate finder's word-set cut. The two
    # similarity knobs are char-level difflib scores over DATE-MASKED text and they gate DIFFERENT
    # steps, which is why there are two:
    #   `dupe_similarity_override` is what lets two sub-documents with DIFFERENT dates be considered
    #     copies at all - both as a cross-date admission in cluster_rows and as the escape hatch in
    #     duplicate_gate. #81 raised this default 0.90 -> 0.99 on 2026-08-06, reasoning that the date
    #     now leads the rule so this knob guards a date MISMATCH specifically rather than a missing
    #     title, and should fire only for text that is essentially identical. Adrian's instinct was
    #     100%; that cannot fire, because two scans of one page are OCR'd separately and never come
    #     out character-identical - measured on 22 live clusters, real duplicates bottomed out at
    #     0.994, the worst false positive at 0.823. Masking dates out before scoring is what makes a
    #     high value reachable at all for a re-scan whose only difference is a stamped date.
    #     THAT RAISE NEVER REACHED A CONTAINER - see HISTORY below - and its evidence no longer holds.
    #   `dupe_model_override` skips the confirm call entirely - at that similarity the text has already
    #     answered the question the model would be asked, and the confirm step's silent "these are all
    #     distinct" verdict is a known way to lose a real duplicate. Left at 0.95: it guards a
    #     different question (spend a Vertex call or not) and the evidence for it has not changed.
    #
    # !! THE 0.994 / 0.823 SEPARATION ABOVE DOES NOT SURVIVE. Both figures were measured through
    # difflib's autojunk suppression, which `dedup._min_difflib` stopped applying in #148. The
    # re-derivation was done on the corrected scale on 2026-08-25 and there is no separation left to
    # cut at any value.
    #
    # The label set: `review_rows.dupe_dismissed` records a reviewer REJECTING a duplicate group. A
    # dismissal is a deliberate action so `true` is a reliable false positive, while `false` is this
    # column's default and means nothing on its own. So the set is restricted to the 9 documents where
    # a reviewer dismissed at least one group - they demonstrably worked through that document, which
    # makes the groups they left alone real acceptances. 39 clusters, 19 dismissed and 20 kept,
    # similarity recomputed from stored source_text so every cluster sits on one scale.
    #
    #     corrected scale   worst false positive 1.000   lowest real duplicate 0.529   -> TOTAL OVERLAP
    #     old scale (#81)   worst false positive 0.823   lowest real duplicate 0.994   -> gap
    #
    # 18 of the 19 false positives sit at or above the duplicate floor. Widening the comparison window
    # does not rescue it either - see `dedup._min_difflib`, where 1500/3000/6000/full text all overlap.
    #
    # WHY IT CANNOT WORK, and it is the reviewers' own rule rather than a defect: a duplicate whose
    # second scan OCR'd badly scores LOW ("sometimes the scan quality is different, so we can pick
    # which one is the best"), and a recurring form on different dates scores HIGH and is correctly not
    # a duplicate. Measured examples: a dismissed cluster at 0.993 on full text, a kept one at 0.554.
    # The distinction is whether two documents are the same EVENT, which text similarity does not
    # measure. That is also why the gate leads on `same_date and (same_title or same_category)` and
    # this knob is only the cross-date escape hatch.
    #
    # SO TREAT THIS AS A RECALL DIAL, NOT A DERIVABLE THRESHOLD. On the labelled set, counting only the
    # similarity path into the gate:
    #
    #     threshold   false positives admitted   real duplicates lost
    #        0.90              14                        7
    #        0.95              12                        9
    #        0.97               7                       13
    #        0.99               4                       14
    #        0.995              1                       14
    #
    # 0.99 spends 14 real duplicates to avoid 4 false positives. Whether that is the right trade is a
    # judgement about which error costs more, and the reviewers have answered that in words rather than
    # in data - "don't automatically delete anything, let the reviewer determine which one to keep" -
    # which argues a missed duplicate is worse than a surfaced one. Open on issue #125.
    #
    # HISTORY, and it is why this default is 0.90 rather than 0.99. #67 added
    # `DUPE_SIMILARITY_OVERRIDE: ${DUPE_SIMILARITY_OVERRIDE:-0.90}` to `docker-compose.yml` while 0.90
    # was still the default here, then #81 raised this one to 0.99 and did not touch compose. Because
    # compose passes the key EXPLICITLY, a container reads the compose default and never this file, so
    # 0.99 has never run anywhere: every container has served 0.90 continuously since #67.
    #
    # I previously recorded here that the box `.env` pinned 0.99 on 2026-08-25 and that "the value now
    # running matches this file for the first time". BOTH HALVES WERE WRONG, and the correction is the
    # reason this line changed. Read off the box on 2026-08-25: `.env` line 18 is
    # `DUPE_SIMILARITY_OVERRIDE=0.90`, and the running api resolves `dupe_similarity_override` to 0.9.
    # So production is now EXPLICITLY pinned to the value it had always served, and 0.99 remained a
    # code default that had never been deployed.
    #
    # 0.90 is also the direction the evidence points rather than merely the status quo. The trade table
    # above shows 0.99 spending 14 real duplicates to avoid 4 false positives, and the reviewers' rule
    # is that nothing is deleted automatically - "let the reviewer determine which one to keep" - which
    # makes a missed duplicate the more expensive error. Aligning here changes NO deployed behaviour:
    # compose and the box `.env` both already say 0.90. It only stops the code contradicting them, and
    # it does not preempt the threshold decision still open on #125.
    dupe_jaccard_threshold: float = 0.70
    dupe_similarity_override: float = 0.90
    dupe_model_override: float = 0.95

    # How long the UI waits for a COOPERATIVE stop before offering "Force stop". The cooperative path
    # normally lands within a second, because the retry backoff polls for a cancel between one-second
    # sleep slices - so this is patience for the pathological case (a worker wedged somewhere that
    # reaches no check at all), not an expected wait. Raising it delays the escape hatch; lowering it
    # invites a hard kill that leaves orphan recovery to tidy up a half-finished run.
    job_cancel_grace_seconds: int = 10

    # Segmentation + verification tuning (ported verbatim).
    window_budget_mb: float = 12.5
    window_overlap: int = 30
    # Hard cap on pages in ONE segmentation vision call. window_budget_mb bounds request SIZE; this
    # bounds request DURATION, which bytes cannot - a byte-light record packs a huge page count into
    # one budget-sized window. Document 68cb2500 (~52KB/page) put all 241 of its pages in a single
    # 12.5 MB window and failed 6/6 times, because that call needs longer than the deadline.
    #
    # 100 is a round number chosen to sit clearly BELOW the observed failure onset rather than at the
    # edge of it. Measured on the box 2026-08-12 (scripts/eval/window_duration_curve.py): 80 pages
    # took 20.2s, 120 took 39.9s, 160 took 54.5s, 200 took 106.3s, 241 took 179.0s - all against a
    # 120s deadline. Production windows of 180 and 188 pages errored 2/3 and 1/2, so the failure zone
    # starts near 180; 100 lands around 30s, comfortably clear even with segment_window_workers
    # windows running at once.
    #
    # The cost of a lower cap is more windows per record - more calls, more spend, and more seams
    # where over-segmentation can appear. The overlap plus the ownership merge exist to handle seams,
    # and the recall A/B on the affected labelled cases is a follow-up, not a blocker.
    window_max_pages: int = 100
    # The vLLM path's own page bound, because NEITHER bound above binds it. Windows are packed to
    # 12.5 MB of raw PDF BYTES with `window_max_pages` as an outer limit, and once a window is
    # rasterised neither describes what the pod sees: one page becomes one image, and the pod refuses
    # above `--limit-mm-per-prompt`. Gemini is untouched by this - it takes the PDF and rasterises
    # nothing.
    #
    # 30 is MEASURED rather than picked. The benchmark harness records "our largest segmentation
    # window is 27 page-images and summarize sends up to 15" (16_pod_bootstrap.sh), and its serve
    # matrix says "we send 30" against a pod limit of 40. So this covers the observed maximum and
    # still leaves 10 images of headroom.
    #
    # NOTE which limit binds: at 827 tokens per image (llm/tokens.py, measured on the pod 2026-09-11)
    # thirty images is ~24,800 tokens against a 131,072 context, so context length is nowhere near
    # the constraint - the image COUNT is. A sizing argument that reasons from context length is
    # reasoning about the wrong limit.
    vllm_segment_max_pages: int = 30
    verify_merge: bool = True
    verify_use_text: bool = True
    verify_suspect_cap: int = 200
    # Run the boundary check ONLY on rows the trigger heuristic selects (same category and date as
    # the previous row, or a row of at most SHORT_ROW_PAGES pages), instead of on every boundary.
    #
    # The cap above was meant to bound this and does not: 200 against roughly 88 boundaries per
    # record means it never binds, so the whole `rest` set is checked as well. A boolean expresses
    # the intent that a number cannot - "the triggered set, whatever size it is".
    #
    # MEASURED on 28 reviewer-corrected records (2,456 boundaries, 239 reviewer merges): every
    # boundary gives 57.4% precision at 49.0% recall; the triggered set alone gives 60.2% at 45.6%
    # from 31% fewer calls. So it trades 8 of 117 correct suggestions for 759 fewer model calls, and
    # precision goes UP because the boundaries it drops are the ones the oracle was worst on.
    #
    # THOSE FIGURES PRE-DATE the images-first reordering in `verify_pass._same_document` and have
    # not been re-measured. The SHAPE of the trade still holds; the levels are unverified.
    #
    # Defaults FALSE, which is today's behaviour. It is a live change to what a reviewer is shown,
    # so it ships as a capability and gets turned on deliberately rather than by upgrading.
    verify_triggered_only: bool = False
    bundle_summarize_cap: int = 40

    def effective_job_timeout(self, pages: int) -> int:
        """The size-aware RQ wall-clock cap (seconds) for a document of ``pages`` pages: a flat
        floor for small records, scaling by page count for large ones. Single source of the
        formula shared by services.jobs.enqueue and worker.tasks."""
        return max(self.job_timeout, int(pages * self.job_timeout_per_page))

    def pool_timeout(self, pages: int) -> int:
        """The wall-clock ceiling (seconds) for one ThreadPoolExecutor drain: the size-aware job
        timeout minus a margin, so a stalled pool is abandoned JUST before RQ's SIGKILL (which
        would otherwise orphan the job) yet a legitimately long pool on a large record is not cut
        short. Floored at 1 so a tiny job_timeout in a test never yields a non-positive timeout."""
        return max(1, self.effective_job_timeout(pages) - self.future_timeout_margin_seconds)

    def effective_genai_timeout_ms(self, est_tokens: int) -> int:
        """The size-aware per-request deadline (ms) for a call of ``est_tokens`` input tokens.

        Deliberately the same shape as `effective_job_timeout` above - a flat floor, scaling
        past it -
        because it answers the same question one level down. The floor keeps small calls exactly
        where they are; only a request larger than the floor already covers is given more.

        Sits here rather than in the retry seam so the formula has ONE home, the way the job-timeout
        pair does: `genai_retry` applies it and nothing else computes a deadline.
        """
        scaled = int(est_tokens * self.genai_timeout_per_1k_tokens_ms / 1000)
        return max(self.genai_http_timeout_ms, scaled)

    @model_validator(mode="after")
    def _derive(self) -> "Settings":
        default_model = _GEMINI_FLASH_MODEL if self.use_vertex else "gemini-flash-latest"
        self.genai_model = self.genai_model or default_model
        # Summarization uses 3.5-flash as of 2026-08-14, replacing the 2.5-pro chosen in an earlier
        # A/B for condensing + faithfulness on long records. Two reasons, in order of weight:
        #
        # 1. Operational. On 2026-08-13 Vertex stopped admitting 2.5-pro for this project outright
        #    (0/8 on the configured endpoint, rejections in ~0.1s) and every summarize job failed. It
        #    recovered to 8/8 by 2026-08-14 with nothing changed on our side, so the condition is
        #    external and can recur without warning. 3.5-flash was 8/8 throughout.
        # 2. Quality is a WASH, and deliberately not claimed as an argument for flash. Scored
        #    2026-08-14 against the frozen human baselines, both arms 39/39 on identical rows: pro sat
        #    nearer the human length (1.41x vs 1.59x on category 1) and 2 points higher on point
        #    precision, while flash retained normal findings far better on category 3 (56% vs 33%
        #    against a human 49%) and its audit parsed 39/39 where pro's failed 3 times. At n=30 and
        #    n=9 the precision gaps are inside noise; the length ratio is the one real difference.
        #
        # Known limit of that evidence: only categories 1 and 3 had rows. Nine scored zero, including
        # every long-document category - which is exactly where a model difference would show. Revisit
        # if a record set covering them becomes available.
        #
        # Summary-ONLY - genai_model (segmentation, header/DOI) and classify_model (categorization)
        # are untouched, so neither can regress. SUMMARY_MODEL overrides.
        self.summary_model = self.summary_model or "gemini-3.5-flash"
        self.verify_model = self.verify_model or self.genai_model
        if self.environment == "prod" and not self.use_vertex:
            raise RuntimeError(
                "GOOGLE_GENAI_USE_VERTEXAI must be true in production: PHI may only go to the "
                "BAA-covered Vertex endpoint, never the Developer API."
            )
        self.summary_provider = (self.summary_provider or "gemini").strip().lower()
        self.llm_backend = (self.llm_backend or "gemini").strip().lower()
        # Order matters, and it is stricter than before. The two vendor names are normalised directly
        # above and the override string is parsed next; all FOUR helpers below branch on the result,
        # so none may run before that.
        self._validate_backend_selection()
        self._apply_gemini_call_defaults()
        self._validate_openai_provider()
        self._validate_vllm_backend()
        # LAST, and that is load-bearing: it reads `vllm_model`, which the validation directly above
        # is what guarantees is non-empty. Run earlier it would happily pin the summarize triple to
        # "" and turn a loud refusal-to-boot into three empty model names at call time.
        self._apply_vllm_call_defaults()
        return self

    def _apply_gemini_call_defaults(self) -> None:
        """Per-call-type model defaults, for a summarize stage that is actually answered by Gemini.

        The guard used to read "not openai", and its docstring argued that a third provider SHOULD
        inherit these rather than start with empty keys. That reasoning does not survive a third
        provider arriving: a vLLM server serves exactly ONE model, so inheriting three Gemini names
        would produce three 404s on the first call rather than a working default. Inheriting is only
        safe for a vendor that serves the whole Gemini catalogue, which is to say for Gemini.

        The vllm path is resolved by `_apply_vllm_call_defaults` below rather than left empty. It WAS
        left empty here, under a note crediting the wiring to "T5/T6" - but T5 grew the Protocol and
        T6 passed `stage`, so neither resolved a model and no task ever did. The gap was not visible
        from this function, because the name that leaked was `summary_model`, defaulted in `_derive`
        OUTSIDE this Gemini-only branch.
        """
        if self.summary_provider == "openai" or self.backend_for("summarize") != "gemini":
            return
        # Gemini per-call-type defaults. The body call reads page images and applies a long
        # format spec, so it keeps summary_model. The title is extraction from OCR text and the
        # audit is a check, so both step down to flash. Justified by call reduction alone,
        # independent of which model the body happens to be running.
        #
        # Set HERE rather than as field defaults so the openai branch still sees "" for an
        # unset key and can refuse to start. A field default would silently satisfy that guard.
        self.summary_body_model = self.summary_body_model or self.summary_model
        self.summary_title_model = self.summary_title_model or _GEMINI_FLASH_MODEL
        self.audit_model = self.audit_model or _GEMINI_FLASH_MODEL
        # Defaulted ON rather than opt-in: the failure it guards against is an outage of the
        # configured body model, and someone raising SUMMARY_MODEL to a pro tier is exactly the
        # person who will not have thought about it. Harmless when the body already IS this model
        # - summarize_engine skips a fallback that equals the model that just failed.
        _fb = self.summary_body_fallback_model.strip()
        self.summary_body_fallback_model = (
            "" if _fb.lower() in ("none", "off") else (_fb or "gemini-3.5-flash")
        )

    def _apply_vllm_call_defaults(self) -> None:
        """Point the summarize triple at the one model a vLLM server actually serves.

        No per-call-type tiering to express here, unlike Gemini: a vLLM process serves exactly ONE
        model, so body, title and audit all resolve to the same name. That is the same fact that
        makes inheriting the Gemini triple wrong rather than merely untidy.

        WHY THIS EXISTS. Until 2026-09-15 these keys were left empty on the vllm path and nothing
        filled them, so `LLM_BACKEND=vllm` reached the pod with a GEMINI model name and 404'd on the
        first call. The name did not come from the empty keys - it came from `summary_model`, which
        `_derive` defaults to "gemini-3.5-flash" for every backend, and which four call sites passed
        explicitly (api/admin.py, api/documents.py x3). Those now ask `model_for("body")`, which is
        what makes this function reachable at all; setting the triple alone would have fixed nothing.

        An explicitly configured key still wins, matching the Gemini branch: an operator who sets
        SUMMARY_TITLE_MODEL has said something deliberate, and a second server can serve a second
        model even though one process cannot.
        """
        if self.backend_for("summarize") != "vllm":
            return
        self.summary_body_model = self.summary_body_model or self.vllm_model
        self.summary_title_model = self.summary_title_model or self.vllm_model
        self.audit_model = self.audit_model or self.vllm_model

    def _validate_openai_provider(self) -> None:
        """Refuse to start an OpenAI-backed deployment that is missing a key, a model, or ZDR.

        KEYED ON BOTH SELECTORS, and that is the point of the change rather than tidiness. This used
        to read `summary_provider != "openai"` alone. Once llm_backend can also select OpenAI, that
        test leaves a hole with PHI on the other side of it: LLM_BACKEND=openai with SUMMARY_PROVIDER
        left at its default "gemini" would return here immediately, so the ZDR acknowledgement below
        is never checked and production starts happily sending medical records to OpenAI.

        A per-stage override opens the same hole, which is why this asks `resolved_backends()` rather
        than reading the global value. The condition only ever widens what is checked.
        """
        if self.summary_provider != "openai" and "openai" not in self.resolved_backends():
            return
        # Fail at startup, not on the first summary. A worker that boots and then errors per row
        # burns a job and leaves the reviewer with a half-processed document.
        missing = [
            name
            for name, value in (
                ("OPENAI_API_KEY", self.openai_api_key),
                ("SUMMARY_BODY_MODEL", self.summary_body_model),
                ("SUMMARY_TITLE_MODEL", self.summary_title_model),
                ("AUDIT_MODEL", self.audit_model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "SUMMARY_PROVIDER=openai requires " + ", ".join(missing) + ". There is no "
                "default model on purpose: pick one by measuring it against the frozen human "
                "baselines, not by inheriting a guess."
            )
        if self.environment == "prod" and not self.openai_zdr_acknowledged:
            raise RuntimeError(
                "OPENAI_ZDR_ACKNOWLEDGED must be true to send PHI to OpenAI in production. A "
                "signed BAA is not sufficient on its own - Zero Data Retention (or Modified "
                "Abuse Monitoring / Eyes Off) must also be approved on the organization. Check "
                "Settings > Organization > Data controls > Data retention before setting this."
            )

    def _validate_backend_selection(self) -> None:
        """Refuse to start on a backend name or an override entry nobody can act on.

        Fails at startup on a typo rather than ignoring it. An ignored override leaves that stage on
        the backend it was already using while the operator believes it moved - which looks exactly
        like the change having worked, until somebody reads a provenance column weeks later and
        cannot tell whether the setting was wrong or the code was.
        """
        if self.llm_backend not in _LLM_BACKENDS:
            raise RuntimeError(
                f"LLM_BACKEND={self.llm_backend!r} is not a known backend; "
                f"expected one of {list(_LLM_BACKENDS)}."
            )
        # Called for its exceptions. The parsed result is cached, so every later backend_for on this
        # process reuses it rather than re-validating.
        _parsed_overrides(self.llm_backend_overrides)

    def resolved_backends(self) -> set[str]:
        """Every backend some stage can actually reach: the global default plus every override.

        Guards key on THIS rather than on llm_backend alone. A single per-stage override is enough to
        send PHI to a vendor the global setting never mentions, so a guard reading only the global
        value would pass while the traffic went elsewhere.
        """
        overridden = (backend for _, backend in _parsed_overrides(self.llm_backend_overrides))
        return {self.llm_backend, *overridden}

    def backend_for(self, stage: str) -> str:
        """The backend that answers one stage's calls.

        Mirrors ``model_for``: resolved from config, read ONCE where the caller can persist it, and
        never re-read mid-job. Record it alongside the model - with per-stage overrides a single job
        can legitimately span two backends, and a row that does not say which one answered it cannot
        be attributed afterwards.
        """
        if stage not in _LLM_STAGES:
            raise KeyError(f"unknown stage {stage!r}; expected one of {list(_LLM_STAGES)}")
        for overridden_stage, backend in _parsed_overrides(self.llm_backend_overrides):
            if overridden_stage == stage:
                return backend
        return self.llm_backend

    def thinking_for(self, stage: str) -> int:
        """The Gemini thinking budget for one stage, preserving exactly today's per-stage values.

        Three budgets exist because each was set for its own reason and none of them generalises.
        Segmentation keeps dynamic thinking because an A/B showed thinking-OFF regresses strict
        doc-F1 by over-segmenting. The summarize family keeps it because the 2026-08-14 arm that
        selected 3.5-flash ran at -1, so the quality measurement only holds at that value. Everything
        else inherits 0, because thinking is pure overhead on a structured extraction call.

        GEMINI ONLY. The vLLM path sends thinking explicitly off and never sends a budget: a budgeted
        vLLM call spends its output allowance on reasoning and returns an EMPTY summary. Measured
        2026-09-11 on a 1,314-page record - 25 of 476 rows, 5.3%, every one finish_reason=length at
        exactly the 8,192 cap with 23,878 to 30,891 characters of reasoning and no summary at all.
        """
        if stage not in _LLM_STAGES:
            raise KeyError(f"unknown stage {stage!r}; expected one of {list(_LLM_STAGES)}")
        if stage == "segment":
            return self.segment_thinking_budget
        if stage in ("summarize", "doi", "deposition"):
            return self.summary_thinking_budget
        return self.gemini_thinking_budget

    def _approved_vllm_origins(self) -> tuple[str, ...]:
        """The destination origins approved to receive PHI.

        In production this is the CODE constant and nothing else. An env override is IGNORED rather
        than rejected, so a stale value left in a dev .env cannot brick a production deploy over a
        setting that is not even meant to apply there. Outside production the override does apply, so
        pointing a local stack at a scratch endpoint needs no code edit - which is what stops someone
        commenting the guard out instead, a far worse end state than an override that was designed.

        Env-editability in production would buy very little. The response to a dead pod is
        LLM_BACKEND=gemini plus a redeploy, which needs no allowlist change at all, so the usual
        incident-operability argument for a tunable does not apply here.
        """
        if self.environment == "prod" or not self.vllm_approved_origins.strip():
            return _APPROVED_VLLM_ORIGINS
        parsed = tuple(_origin(item) for item in self.vllm_approved_origins.split(","))
        return tuple(item for item in parsed if item)

    def _validate_vllm_backend(self) -> None:
        """Refuse to start a vLLM deployment that is missing config or aimed somewhere unapproved."""
        if "vllm" not in self.resolved_backends():
            return
        missing = [
            name
            for name, value in (
                ("VLLM_BASE_URL", self.vllm_base_url),
                ("VLLM_MODEL", self.vllm_model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "a vllm backend requires " + ", ".join(missing) + ". A vLLM server serves exactly "
                "one model and there is no catalogue to fall back on, so an unset model would "
                "inherit a Gemini name and 404 on the first call."
            )
        self._validate_image_counts()
        self._validate_render_targets()
        if self.environment != "prod":
            return
        # Keyed on the DESTINATION, not the backend name. `llm_backend == "vllm"` asserts only which
        # wire dialect we speak; VLLM_BASE_URL is what decides where the record actually goes.
        origin = _origin(self.vllm_base_url)
        approved = self._approved_vllm_origins()
        if origin not in approved:
            raise RuntimeError(
                f"VLLM_BASE_URL resolves to origin {origin or '(unparseable)'}, which is not "
                f"approved to receive PHI in production. Approved: {list(approved)}. This is a "
                "compliance control rather than a misconfiguration - widen it deliberately in "
                "app/config.py, where the change is visible in a diff and needs a deploy."
            )

    def _stage_image_caps(self) -> dict[str, tuple[int, str]]:
        """Every stage that sends PAGE IMAGES to a pod: how many at most, and what changes it.

        FOUR stages rasterise, not one. The guard below compared only segmentation until 2026-09-16,
        which was safe for as long as `vllm_max_images_per_prompt` was pinned in code and unreachable
        - 15, 10 and 6 are all under 40, so the unchecked three could not be wrong. Making that
        limit settable is correct and is the point of this change, but it opens a configuration
        nobody could previously reach: a pod at `--limit-mm-per-prompt 10` would refuse segmentation
        at boot, you would lower VLLM_SEGMENT_MAX_PAGES to clear it, the app would start, and
        summarize would still send 15 - refused by the pod at RUNTIME with nothing having warned.

        The second element is what an operator has to change, which is not the same thing for every
        stage: two are settings and two are module constants. A constant is still worth refusing
        over - if the pod genuinely cannot carry 10 images, the DOI read cannot run there, and
        saying so at boot beats discovering it per row.
        """
        return {
            "summarize": (self.summary_image_max_pages, "SUMMARY_IMAGE_MAX_PAGES"),
            "segment": (self.vllm_segment_max_pages, "VLLM_SEGMENT_MAX_PAGES"),
            "doi": (_DOI_IMAGE_CAP, "summary_doi._MAX_PAGES (a constant, deliberately)"),
            "deposition": (
                _DEPOSITION_IMAGE_CAP,
                "deposition_pages._MAX_PAGES (a constant, deliberately)",
            ),
        }

    def _validate_image_counts(self) -> None:
        """Refuse to start when a stage would send more images than the pod accepts in one request.

        One page becomes one image on this path, so a stage bound above `--limit-mm-per-prompt` is a
        run that dies on its first request of that shape. Checked in DEV as well as prod, unlike the
        origin check: a wrong bound is not a PHI question, it is wasted time on a rented GPU, which
        costs the same in either environment.

        PER STAGE, skipping any stage not on vllm, for the same reason `_validate_render_targets`
        does: Gemini takes a PDF for segmentation and the two isolated reads, so those never
        rasterise there. Summarize is the exception worth knowing - it sends page images on BOTH
        backends - but its count only has to fit a POD's limit, so the skip is still correct.
        """
        for stage, (cap, lever) in self._stage_image_caps().items():
            if self.backend_for(stage) != "vllm" or cap <= self.vllm_max_images_per_prompt:
                continue
            raise RuntimeError(
                f"the {stage} stage sends up to {cap} page images per request, above "
                f"VLLM_MAX_IMAGES_PER_PROMPT of {self.vllm_max_images_per_prompt}. One page becomes "
                "one image on this path, so the pod would refuse the first request of that shape. "
                "Either serve the pod with a higher --limit-mm-per-prompt and raise "
                f"VLLM_MAX_IMAGES_PER_PROMPT to match it, or lower {lever}."
            )

    def _validate_render_targets(self) -> None:
        """Refuse to start when `summary_image_dpi` caps a stage below its own render target.

        THE CEILING BELONGS TO ANOTHER STAGE, and that is the whole reason this exists.
        `rasterise.page_dpi` fits the dpi to the requested pixel target and then caps it at
        `summary_image_dpi` - a SUMMARIZE setting. So lowering summarize's dpi silently drops these
        reads below the resolution their targets were chosen for, and nothing says so: the pages
        come back, just smaller. The person who would break it is tuning summarization and will
        never read those reads' docstrings.

        A BOOT GUARD RATHER THAN A TEST, deliberately. `model_config` sets no `env_prefix` and no
        alias, so every field here is env-overridable by its own name: `SUMMARY_IMAGE_DPI=110` in a
        deployment breaks this with no code diff and no CI run to catch it. A test cannot see that.
        This also refuses the opposite direction - raising a stage's target without raising the
        ceiling - which `test_the_dpi_ceiling_still_binds_so_a_large_request_is_not_granted` pins as
        rasteriser BEHAVIOUR but no one was refusing as a CONFIGURATION.

        PER STAGE, and skipping any stage not on vllm, because Gemini takes the PDF and rasterises
        nothing: a deployment whose DOI read never renders an image must not be refused over a pixel
        target it will never consult. Generalised from a doi-only check when `deposition` gained its
        own target; a second copy of this arithmetic would have been a second thing to drift.

        RE-DERIVES page_dpi's arithmetic instead of calling it, because `rasterise` imports this
        module and the dependency can only run one way. That is a real weakness - this could drift
        from the function it describes and still read like a check - so
        `test_the_boot_guard_agrees_with_what_page_dpi_actually_renders` exists purely to tie the
        two together, comparing this verdict against the real function across the boundary.

        THE TOLERANCE IS ONE DPI STEP, not a margin of taste. A dpi is an integer, so fitting it to
        a pixel target loses up to `page_pt / 72` px (10.97 on a 790pt page) however the ceiling is
        set. A shortfall bigger than that is the ceiling binding rather than rounding.
        """
        for stage, setting in _STAGE_RENDER_TARGETS.items():
            if self.backend_for(stage) == "vllm":
                self._assert_target_is_reachable(stage, setting)

    def _assert_target_is_reachable(self, stage: str, setting: str) -> None:
        """One stage's render target against the dpi ceiling. See `_validate_render_targets`."""
        target = getattr(self, setting)
        page_pt = _SMALLEST_PAGE_LONG_EDGE_PT
        one_dpi_step = page_pt / 72.0
        fitted = int(target * 72.0 / page_pt)
        achieved = page_pt / 72.0 * max(1, min(self.summary_image_dpi, fitted))
        if achieved >= target - one_dpi_step:
            return
        raise RuntimeError(
            f"SUMMARY_IMAGE_DPI is {self.summary_image_dpi}, which caps the {stage} read at about "
            f"{achieved:.0f}px on the tightest page in the corpus, against its "
            f"{setting.upper()} target of {target}px. page_dpi fits the dpi to the target and then "
            "caps it at summary_image_dpi, a SUMMARIZE setting, so this read would render below "
            "the resolution its target was chosen for and nothing in the output would say so. "
            f"Either raise SUMMARY_IMAGE_DPI to at least {fitted}, or lower {setting.upper()} to "
            f"at most {int(achieved)}."
        )

    def model_for(self, kind: str) -> str:
        """The model that should answer one summarize-stage call: "body", "title" or "audit".

        One resolver for BOTH providers now: ``_derive`` populates all three keys on the Gemini path
        and refuses to start without them on the OpenAI path, so by the time this is reachable each
        one holds a real model name. It previously ignored the keys entirely on Gemini and returned
        summary_model for all three, which is why the per-call-type settings did nothing there.

        Read ONCE, at job creation, and persisted on the Job (see services/jobs.create_job). Do not
        call this per row: a job resumed after a config change must keep the models it started with,
        or one delivered document ends up written by two different models with no record of which.
        """
        return {
            "body": self.summary_body_model,
            "title": self.summary_title_model,
            "audit": self.audit_model,
        }[kind]

    def model_for_stage(self, stage: str) -> str:
        """The model answering one NON-summarize stage, resolved for the backend that will answer.

        A METHOD, not a field default, and that distinction is the whole design. `genai_model` is
        read by FOUR stages - segment, extract, doi and deposition - so rewriting it because
        `extract` moved to the pod would drag the other three there too, silently, and none of them
        crosses the provider seam yet. `_apply_vllm_call_defaults` may rewrite the summarize triple
        only because those three keys serve exactly one stage; nothing else here has that property.

        SUMMARIZE IS DELIBERATELY REFUSED. It resolves THREE models - body, title and audit, via
        `model_for(kind)`, read once and persisted on the Job. Returning one of them from a function
        named "the model for this stage" would invite a caller to use it for all three and quietly
        collapse the tiering.

        Gemini keeps today's per-stage settings exactly, so that path cannot regress. OpenAI is not
        expressible for these stages: `_validate_openai_provider` requires only the summarize triple,
        so an override sending `classify` to OpenAI would fall through to a Gemini name below. That
        is unreachable today - the sole OpenAI path is `summary_provider`, which is summarize-only -
        and closing it needs per-stage keys, which is a change of its own rather than a line here.
        """
        if stage not in _LLM_STAGES:
            raise KeyError(f"unknown stage {stage!r}; expected one of {list(_LLM_STAGES)}")
        if stage == "summarize":
            raise KeyError("summarize resolves three models; use model_for(kind)")
        if self.backend_for(stage) == "vllm":
            # One process serves one model, so every stage routed there asks for the same name.
            return self.vllm_model
        return {
            "segment": self.genai_model,
            "extract": self.genai_model,
            "dedup": self.classify_model,
            "classify": self.classify_model,
            "verify": self.verify_model,
            "doi": self.genai_model,
            "deposition": self.genai_model,
        }[stage]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env/.env
