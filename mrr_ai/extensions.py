"""External service clients, created once and imported where needed.

env validation runs here so the clients are never built with missing secrets,
regardless of which module imports this first.
"""

import os

from google import genai
from openai import OpenAI

from mrr_ai import config
from mrr_ai.config import validate_env

validate_env()


def _build_client():
    """google-genai client routed per GOOGLE_GENAI_USE_VERTEXAI (see .env.example).

    Vertex AI is the BAA-covered Gemini platform - PHI runs MUST set the flag. Auth on
    Vertex: GOOGLE_CLOUD_PROJECT set -> ADC (service-account impersonation works); unset
    -> the GCP API key in GEMINI_API_KEY against the Vertex endpoint. Mirrors the
    construction proven live by experiments/a1-segmentation/src/genai_client.py.
    """
    if config.USE_VERTEX:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
            return genai.Client(vertexai=True, project=project, location=location)
        return genai.Client(vertexai=True, api_key=os.environ["GEMINI_API_KEY"])
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


genai_client = _build_client()
# OPENAI_API_KEY is read from the environment by the OpenAI client (see .env.example).
client = OpenAI()
