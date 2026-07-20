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
    genai_max_retries: int = 6
    genai_retry_base_delay: float = 2.0
    genai_retry_max_delay: float = 30.0

    # Thinking tokens are pure overhead for our structured extraction/segmentation calls, and on
    # 2.5-flash they silently consume max_output_tokens. Default OFF (budget 0); set >0 or -1
    # (model-dynamic) via env to re-enable if a task regresses. Applied centrally at the genai seam.
    gemini_thinking_budget: int = 0

    # Global Vertex request ceiling (requests/minute) enforced by a Redis token bucket at the seam,
    # so the aggregate rate across every worker process never trips dynamic-shared-quota 429s. Tune
    # empirically: raise until near throttling, then back off ~20%.
    vertex_max_rpm: int = 60

    # Independent segmentation windows run on a small thread pool (each still crosses the seam, so
    # the limiter caps the aggregate). Speed lever; keep modest so it does not dominate the quota.
    segment_window_workers: int = 3

    # Segmentation + verification tuning (ported verbatim).
    window_budget_mb: float = 12.5
    window_overlap: int = 30
    verify_merge: bool = True
    verify_use_text: bool = True
    verify_suspect_cap: int = 200
    bundle_summarize_cap: int = 40

    @model_validator(mode="after")
    def _derive(self) -> "Settings":
        default_model = "gemini-2.5-flash" if self.use_vertex else "gemini-flash-latest"
        self.genai_model = self.genai_model or default_model
        self.summary_model = self.summary_model or self.genai_model
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
