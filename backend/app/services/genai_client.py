"""google-genai client, built lazily and cached (ported from mrr_ai.extensions._build_client).

Vertex AI is the BAA-covered Gemini platform - PHI runs MUST use it in production (enforced by
config's prod fail-fast). Auth on Vertex: GOOGLE_CLOUD_PROJECT set -> ADC; unset -> the API key
against the Vertex endpoint. OpenAI is dropped (Vertex-only, per the re-platform decision). Lazy
+ cached so importing the module needs neither credentials nor a network call.
"""

from functools import lru_cache

from google import genai

from app.config import get_settings


@lru_cache
def get_genai_client() -> genai.Client:
    settings = get_settings()
    if settings.use_vertex:
        if settings.google_cloud_project:
            return genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
        return genai.Client(vertexai=True, api_key=settings.gemini_api_key)
    return genai.Client(api_key=settings.gemini_api_key)
