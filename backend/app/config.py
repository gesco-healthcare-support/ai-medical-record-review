"""Application settings (ported from the Flask mrr_ai/config.py).

Lazy via get_settings() so importing the package does not require the env to be present
(tests/tooling import freely; a real run reads .env). Required secrets have no default, so
instantiation fails fast if they are missing. Postgres + Redis + Vertex-only per the plan.
"""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"  # "prod" hard-requires Vertex (BAA)

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
    google_cloud_location: str = "us-central1"
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
    # Summary faithfulness verify pass: a second temp-0 call rewrites each summary to drop
    # statements unsupported by / contradicting its OCR source (problem #3), keeping the raw output
    # too. On by default; a regression reverts via env with no redeploy. Distinct from the
    # segmentation verify_* knobs below.
    summary_verify: bool = True
    # DOI extraction: the segmentation model propagates the claim's date of injury onto documents
    # that never state one (it sees a whole window at once). Instead, at summarize time a per-
    # sub-document ISOLATED vision call reads the DOI from THAT document alone (no neighbours to
    # copy from), so a summary shows a DOI only when its own document states one. On by default; a
    # regression reverts via env with no redeploy.
    summary_doi_extract: bool = True
    # Summarization also sends the page IMAGES alongside the OCR text (multimodal): the images
    # recover tables, checkboxes, and handwriting the OCR garbles (an eval on real sub-docs showed it
    # adds missing vitals/allergies with no loss). Env-toggle to revert to OCR-only.
    summary_multimodal: bool = True
    # Cap page images per sub-document so a long record cannot blow the request payload/latency; the
    # full OCR text still covers every page beyond the cap.
    summary_image_max_pages: int = 15
    # DPI for the summary page images (lean JPEG); 120 was enough to read tables/handwriting in the eval.
    summary_image_dpi: int = 120
    # 2.5-pro is a thinking model and REJECTS the seam's default thinking_budget=0; give summary
    # calls dynamic thinking (-1). If SUMMARY_MODEL is reverted to a flash tier, set this to 0.
    summary_thinking_budget: int = -1

    # Concurrency + retry (become RQ worker knobs in P4; caps guard the shared Vertex quota).
    pipeline_workers: int = 2
    classify_workers: int = 4
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

    # Per-attempt HTTP timeout (ms) for the Vertex/genai client. google-genai defaults to no
    # timeout, so a stalled call blocks forever; bounding it turns a stall into an httpx timeout
    # that generate_with_retry already catches + retries. 120s covers a large vision window.
    genai_http_timeout_ms: int = 120000

    # Per-call OCR (Tesseract) wall-clock cap (seconds). A hung/oversized page is killed and
    # skipped rather than blocking a worker thread forever (the concurrent-OCR deadlock backstop;
    # OMP_THREAD_LIMIT=1 in compose is the primary fix).
    ocr_timeout_seconds: int = 120

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

    # Global Vertex request ceiling (requests/minute) enforced by a Redis token bucket at the seam,
    # so the aggregate rate across every worker process never trips dynamic-shared-quota 429s. Tune
    # empirically: raise until near throttling, then back off ~20%.
    vertex_max_rpm: int = 60

    # Independent segmentation windows run on a small thread pool (each still crosses the seam, so
    # the limiter caps the aggregate). Speed lever; keep modest so it does not dominate the quota.
    segment_window_workers: int = 3

    # Duplicate detection. `dupe_jaccard_threshold` is the candidate finder's word-set cut (was a
    # hardcoded default argument). The two similarity knobs are char-level difflib scores and they
    # gate DIFFERENT steps, which is why there are two:
    #   `dupe_similarity_override` lets high-scoring content pass the date+title accuracy gate, for
    #     the genuine re-scan that carries two different dates. Measured on 22 live clusters: real
    #     duplicates scored 0.994 and above, the worst false positive 0.823. Anything in 0.85-0.95
    #     separates that sample; 0.90 sits furthest from both edges.
    #   `dupe_model_override` is higher and skips the confirm call entirely - at that similarity the
    #     text has already answered the question the model would be asked, and the confirm step's
    #     silent "these are all distinct" verdict is a known way to lose a real duplicate.
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
    verify_merge: bool = True
    verify_use_text: bool = True
    verify_suspect_cap: int = 200
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

    @model_validator(mode="after")
    def _derive(self) -> "Settings":
        default_model = "gemini-2.5-flash" if self.use_vertex else "gemini-flash-latest"
        self.genai_model = self.genai_model or default_model
        # Summarization uses 2.5-pro: its condensing + faithfulness on long/complex records beat
        # 2.5-flash in a real A/B. Summary-ONLY - genai_model (segmentation, header/DOI) and
        # classify_model (categorization) are untouched, so neither can regress. SUMMARY_MODEL overrides.
        self.summary_model = self.summary_model or "gemini-2.5-pro"
        self.verify_model = self.verify_model or self.genai_model
        if self.environment == "prod" and not self.use_vertex:
            raise RuntimeError(
                "GOOGLE_GENAI_USE_VERTEXAI must be true in production: PHI may only go to the "
                "BAA-covered Vertex endpoint, never the Developer API."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from env/.env
