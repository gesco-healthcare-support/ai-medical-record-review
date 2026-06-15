# syntax=docker/dockerfile:1
# Single-stage image; pre-MVP simplicity over minimal image size.
FROM python:3.12-slim

# uv (pinned for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

# System binaries the app shells out to:
#   tesseract-ocr -> pytesseract (OCR)
#   poppler-utils -> pdf2image (PDF -> image rasterization)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first so this layer caches independently of app code.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# App code
COPY . .

# Use the project venv on PATH.
ENV PATH="/app/.venv/bin:$PATH"

# Secrets (GEMINI_API_KEY, OPENAI_API_KEY) are injected at runtime, e.g.:
#   docker run --env-file .env -p 5010:5010 ai-medical-record-review
EXPOSE 5010
CMD ["python", "app.py"]
