"""External service clients, created once and imported where needed.

env validation runs here so the clients are never built with missing secrets,
regardless of which module imports this first.
"""

import os

from google import genai
from openai import OpenAI

from mrr_ai.config import validate_env

validate_env()

genai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
# OPENAI_API_KEY is read from the environment by the OpenAI client (see .env.example).
client = OpenAI()
