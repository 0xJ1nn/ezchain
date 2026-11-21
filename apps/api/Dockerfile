# syntax=docker/dockerfile:1.7
#
# ezCHAIN API image.
# Build context is the repository root:
#   docker build -f apps/api/Dockerfile -t ezchain/api .
#
# The image is also the Alembic migration job; compose runs the same tag with
# `alembic -c /app/alembic.ini upgrade head` before the API starts.

# Python runtime for the API, plus the PyO3 bridge it cannot serve without.
#
# The bridge (`ezchain_bridge`) is a runtime prerequisite, not an optional accelerator:
# apps/api/src/ezchain_api/native.py and the money path import it and raise
# NativeBridgeUnavailable without it. A Python-only image therefore cannot serve a
# request, so the extension is built in the `bridge` stage below from the committed Cargo
# lock and copied into site-packages.

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

# The PyO3 bridge, in its own stage so a Python change never rebuilds it.
#
# `--locked` keeps rust/Cargo.lock authoritative. abi3-py39 is enabled in
# rust/ezchain-bridge/Cargo.toml, so the extension does not link against a specific
# libpython and needs no interpreter in this stage. The artifact is the cdylib
# rust/target/release/libezchain_bridge.so, copied into the runtime site-packages below.
#
# The Rust image is pinned by manifest-list digest, resolved 2026-11-21.
#   rust:1.100-slim-bookworm -> sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37
# The digest is the multi-arch index, so the same line resolves to the right child on
# amd64, arm64 and the rest. `:1.100-slim-bookworm` is kept in the reference because it
# is the tag a human reads to update it; Docker builds the digest, never the tag. To move
# it:
#   docker buildx imagetools inspect rust:1.100-slim-bookworm   # take the top-level Digest
# then change the digest below and this comment together, in one commit. The floor is set
# by pyo3 0.30, which requires Rust 1.85 or newer.
FROM rust:1.100-slim-bookworm@sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37 AS bridge

WORKDIR /build

# Only the Rust workspace: a change to Python code must not invalidate this layer.
COPY rust ./rust

RUN cargo build --locked --release -p ezchain-bridge --manifest-path rust/Cargo.toml

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

# The PyO3 bridge built in the `bridge` stage. Python imports it as `ezchain_bridge`,
# so the platform-specific cdylib is installed under that name in the same site-packages
# the builder populated. Without it the API and the money path fail closed with
# NativeBridgeUnavailable at import, before serving any request.
COPY --from=bridge /build/rust/target/release/libezchain_bridge.so /usr/local/lib/python3.11/site-packages/ezchain_bridge.so

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
