# syntax=docker/dockerfile:1
# Next.js production image. Stage 1 installs deps + builds; stage 2 runs the minimal standalone
# server emitted by next.config.ts `output: "standalone"`.
FROM node:22-slim AS build
WORKDIR /app
RUN corepack enable
# Lockfile first for a cacheable install layer.
COPY package.json pnpm-lock.yaml ./
RUN corepack pnpm install --frozen-lockfile
COPY . .
RUN corepack pnpm build

FROM node:22-slim AS runtime
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
