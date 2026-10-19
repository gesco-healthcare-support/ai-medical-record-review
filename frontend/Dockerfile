# syntax=docker/dockerfile:1
# Next.js production image. Stage 1 installs deps + builds; stage 2 runs the minimal standalone
# server emitted by next.config.ts `output: "standalone"`.
#
# Debian 13 (trixie), as the Python images are. node:22-slim is Debian 12, and on 2026-10-19 it
# still carried a perl-base missing four Critical security fixes; the trixie variant is the same
# Node.
FROM node:22-trixie-slim@sha256:154ba2f4d6fec323d28e4f4bb86bba4677f1223391a1979cf521304e03a98dfa AS build
WORKDIR /app
RUN corepack enable
# Lockfile first for a cacheable install layer.
COPY package.json pnpm-lock.yaml ./
RUN corepack pnpm install --frozen-lockfile
COPY . .
RUN corepack pnpm build

FROM node:22-trixie-slim@sha256:154ba2f4d6fec323d28e4f4bb86bba4677f1223391a1979cf521304e03a98dfa AS runtime
# Links the image to this repository in ghcr.io (see backend/Dockerfile).
LABEL org.opencontainers.image.source="https://github.com/gesco-healthcare-support/ai-medical-record-review"
# The base image ships the package managers (npm, npx, corepack, yarn); the standalone server needs
# only `node`, and the release policy scans everything in this image, so they are removed.
RUN rm -rf /usr/local/lib/node_modules /opt/yarn-v* \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# Standalone emits a self-contained server; static assets + public/ are copied alongside it.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
