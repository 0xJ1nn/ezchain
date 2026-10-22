# syntax=docker/dockerfile:1.7
#
# ezCHAIN web image (Next.js 15, App Router).
# Build context is the repository root:
#   docker build -f apps/web/Dockerfile -t ezchain/web .
#
# EZCHAIN_API_URL is baked into next.config's rewrite at build time. The runtime
# value from compose wins for server components and the /api route handler.

FROM node:22-alpine AS base

ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

# Pin the pnpm version declared in package.json's packageManager field.
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

WORKDIR /app

FROM base AS deps

# Only manifests and the lockfile: this layer is reused until a dependency changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN pnpm install --frozen-lockfile

FROM base AS builder

ARG EZCHAIN_API_URL=http://api:8000
ENV EZCHAIN_API_URL=${EZCHAIN_API_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY pnpm-lock.yaml pnpm-workspace.yaml package.json ./
COPY apps/web ./apps/web
COPY packages/ui ./packages/ui

RUN pnpm --filter @ezchain/web build

FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

# Unprivileged runtime user; uid 10001 matches the API image.
RUN addgroup -g 10001 -S ezchain \
    && adduser -u 10001 -S -G ezchain ezchain

WORKDIR /app

# pnpm installs into a shared store and links workspaces, so the root
# node_modules and the app-level node_modules both have to exist in the runtime.
COPY --from=builder --chown=ezchain:ezchain /app/node_modules ./node_modules
COPY --from=builder --chown=ezchain:ezchain /app/apps/web ./apps/web
COPY --from=builder --chown=ezchain:ezchain /app/packages ./packages
COPY --from=builder --chown=ezchain:ezchain /app/package.json ./package.json
COPY --from=builder --chown=ezchain:ezchain /app/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=builder --chown=ezchain:ezchain /app/pnpm-lock.yaml ./pnpm-lock.yaml

WORKDIR /app/apps/web

USER ezchain

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1

CMD ["node_modules/.bin/next", "start", "-p", "3000"]
