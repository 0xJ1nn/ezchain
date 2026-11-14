# syntax=docker/dockerfile:1.7
#
# ezCHAIN API image.
# Build context is the repository root:
#   docker build -f apps/api/Dockerfile -t ezchain/api .
#
# The image is also the Alembic migration job; compose runs the same tag with
# `alembic -c /app/alembic.ini upgrade head` before the API starts.

# Base image pinned by manifest-list digest, resolved 2026-11-14.
#   python:3.11-slim -> sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535
# The digest is the multi-arch index, so the same line resolves to the right child on
# amd64, arm64 and the rest. `:3.11-slim` is kept in the reference because it is the tag a
# human reads to update it; Docker builds the digest, never the tag. To move it:
#   docker buildx imagetools inspect python:3.11-slim   # take the top-level Digest
# then change the digest below and this comment together, in one commit.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Runtime and migration dependencies (fastapi, uvicorn, sqlalchemy, psycopg,
# pydantic, alembic, ...). The test/lint toolchain is not installed here.
# The lock is the install source: `pip install --require-hashes` refuses a wheel whose
# digest is not one of the recorded ones, so a compromised or substituted artifact fails
# the build instead of shipping. requirements-runtime.txt stays the human-readable range
# declaration; requirements-runtime.lock is what installs.
COPY requirements-runtime.lock ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install --require-hashes -r requirements-runtime.lock

# Local workspace packages. `--no-deps` is deliberate: requirements-runtime.lock above
# is the single source of runtime pins (including the SDK's `packaging` dependency).
COPY packages/core-py ./packages/core-py
COPY packages/adapters ./packages/adapters
COPY packages/sdk-py ./packages/sdk-py
COPY apps/api ./apps/api
COPY services/engine ./services/engine
RUN python -m pip install --prefix=/install --no-deps \
        ./packages/core-py \
        ./packages/adapters \
        ./packages/sdk-py \
        ./apps/api \
        ./services/engine

# Same base, same digest as the builder above; keep the two in step.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Unprivileged runtime user. The uid is fixed so volume and log ownership is
# predictable across hosts.
RUN groupadd --gid 10001 ezchain \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ezchain

# Site-packages and console scripts (uvicorn, alembic) from the builder.
COPY --from=builder /install /usr/local

WORKDIR /app

# Application source plus apps/api/alembic.ini and apps/api/alembic/ when the
# migration bundle is present. The installed package in site-packages is what
# uvicorn imports; the source tree is here for Alembic's script_location.
COPY --from=builder --chown=10001:10001 /build/apps/api /app

USER 10001:10001

EXPOSE 8000

# Liveness only: /healthz must not depend on the database. Readiness lives at
# /readyz and is what compose/orchestrators should use before sending traffic.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"]

CMD ["uvicorn", "ezchain_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
