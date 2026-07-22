"""google-genai client, built lazily and cached (ported from mrr_ai.extensions._build_client).

Vertex AI is the BAA-covered Gemini platform - PHI runs MUST use it in production (enforced by
config's prod fail-fast). Auth on Vertex: GOOGLE_CLOUD_PROJECT set -> ADC; unset -> the API key
against the Vertex endpoint. OpenAI is dropped (Vertex-only, per the re-platform decision). Lazy
+ cached so importing the module needs neither credentials nor a network call.
"""

from functools import lru_cache

from google import genai
from google.genai import types

from app.config import get_settings


@lru_cache
def get_genai_client() -> genai.Client:
    settings = get_settings()
    # Bound every request (HttpOptions.timeout is in ms). google-genai defaults to no timeout, so a
    # stalled Vertex call would block a worker thread forever; with a timeout it raises an httpx
    # TimeoutException (a TransportError) that generate_with_retry already catches and retries.
    http_options = types.HttpOptions(timeout=settings.genai_http_timeout_ms)
    if settings.use_vertex:
        if settings.google_cloud_project:
            return genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
                http_options=http_options,
            )
        return genai.Client(
            vertexai=True, api_key=settings.gemini_api_key, http_options=http_options
        )
    return genai.Client(api_key=settings.gemini_api_key, http_options=http_options)
