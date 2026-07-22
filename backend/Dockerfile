# syntax=docker/dockerfile:1
# Backend image for the FastAPI API and the RQ workers (one shared codebase). Tesseract + Poppler
# are required by the OCR / PDF-rasterize pipeline on every tier. Torch (the classifier extra) is
# added ONLY for the segment worker via the UV_EXTRAS build arg, so the API and summarize worker
# stay torch-free and light.
FROM python:3.12-slim

# uv (pinned for reproducibility).
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

# System binaries the pipeline shells out to: pytesseract -> OCR, pdf2image -> PDF rasterize.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

# Which optional-dependency groups to install. Web tier + summarize worker use "--extra docs"
# (torch-free); the segment worker overrides to "--extra docs --extra classifier".
ARG UV_EXTRAS="--extra docs"

# Dependencies first so this layer caches independently of the app code.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev ${UV_EXTRAS}

# Bake the segmentation classifier's embedding model into the image so the segment worker never
# reaches huggingface.co at runtime (HF_HUB_OFFLINE=1 above). Only the classifier build installs
# sentence-transformers, so guard on UV_EXTRAS; the api/summarize images skip this, and the offline
# env is harmless there (those tiers never import sentence-transformers). Depends only on the deps
# layer, so it caches independently of app code.
RUN case "$UV_EXTRAS" in \
      *classifier*) \
        HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 /app/.venv/bin/python -c \
          "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" ;; \
      *) echo "no classifier extra: skipping embedding-model bake" ;; \
    esac

# App code (app/, scripts/, alembic/, alembic.ini).
COPY . .

# Use the project venv on PATH (uvicorn, alembic, python); PYTHONPATH so `python scripts/x.py`
# can import the app package without a per-command PYTHONPATH.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
