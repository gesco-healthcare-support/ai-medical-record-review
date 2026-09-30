# syntax=docker/dockerfile:1
# Backend image for the FastAPI API and the RQ workers (one shared codebase). Tesseract + Poppler
# are required by the OCR / PDF-rasterize pipeline on every tier. Torch (the classifier extra) is
# added ONLY for the segment worker via the UV_EXTRAS build arg, so the API and summarize worker
# stay torch-free and light.
#
# Two stages. `build` installs the Python dependencies with uv; the final stage copies only the
# resulting virtualenv (and the baked embedding model), so uv, uvx and pip never reach the image
# that runs - they are build tools, and the release policy scans everything in the final image.
# Both stages use the same base digest, so the venv's interpreter path (/usr/local/bin/python3.12)
# is identical in each.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build

# uv (pinned for reproducibility). Build stage only.
COPY --from=ghcr.io/astral-sh/uv:0.11.2@sha256:c4f5de312ee66d46810635ffc5df34a1973ba753e7241ce3a08ef979ddd7bea5 /uv /uvx /bin/

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

# Bake the segmentation classifier's embedding model so the segment worker never reaches
# huggingface.co at runtime (HF_HUB_OFFLINE=1 in the final stage). Only the classifier build
# installs sentence-transformers, so guard on UV_EXTRAS; the api/summarize images skip this and
# still get an (empty) /opt/hf-cache for the final stage to copy. Depends only on the deps layer,
# so it caches independently of app code.
RUN mkdir -p /opt/hf-cache && case "$UV_EXTRAS" in \
      *classifier*) \
        HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 /app/.venv/bin/python -c \
          "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" ;; \
      *) echo "no classifier extra: skipping embedding-model bake" ;; \
    esac

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
# Links the image to this repository in ghcr.io, where the staging acceptance stage pushes it. Set at build
# time so the pushed image is byte-for-byte the one the stage scanned.
LABEL org.opencontainers.image.source="https://github.com/gesco-healthcare-support/ai-medical-record-review"

# System binaries the pipeline shells out to: pytesseract -> OCR, pdf2image -> PDF rasterize.
# pip ships with the base image but nothing runs it here (dependencies come from the build stage's
# venv), so it is removed with the other build tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes pip

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/opt/hf-cache

WORKDIR /app

COPY --from=build /opt/hf-cache /opt/hf-cache
COPY --from=build /app/.venv /app/.venv

# App code (app/, scripts/, alembic/, alembic.ini).
COPY . .

# Use the project venv on PATH (uvicorn, alembic, python); PYTHONPATH so `python scripts/x.py`
# can import the app package without a per-command PYTHONPATH.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# The commit this image was built from, read by Settings.build_sha and stamped on every job row so
# a stored summary can be traced to the code that produced it - not just to the prompt text, which
# the fingerprints already cover. Deploy passes it:
#   GIT_SHA=$(git rev-parse --short HEAD) docker compose build api
#
# Declared LAST on purpose. An ARG invalidates the build cache from the point it is USED, so putting
# this above the dependency install would re-run `uv sync` and re-bake the embedding model on every
# commit. Here it only rebuilds a metadata layer.
ARG GIT_SHA=unknown
ENV BUILD_SHA=$GIT_SHA

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
