# The documentation site: docs/ built with Material for MkDocs, served by nginx.
#
# Build context is the REPO ROOT (docker-compose.yml service `docs`), because the pages live in
# docs/ and this tooling lives in docs-site/. The root .dockerignore excludes docs/ and *.md for the
# app images, so this build uses its own ignore file, docs-site/Dockerfile.dockerignore, which
# BuildKit picks up by name and which admits only what the site needs.
#
# The build runs with --strict, so a broken link fails the image build as well as CI.

FROM python:3.12-slim AS build

# uv, pinned to the same version as backend/Dockerfile.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /src/docs-site
COPY docs-site/pyproject.toml docs-site/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY docs-site/mkdocs.yml ./
COPY docs /src/docs
RUN uv run --frozen mkdocs build --strict --site-dir /out

FROM nginx:1.31
COPY docs-site/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /out /usr/share/nginx/html/docs
