# syntax=docker/dockerfile:1.7
#
# ezCHAIN API image.
# Build context is the repository root:
#   docker build -f apps/api/Dockerfile -t ezchain/api .
#
# The image is also the Alembic migration job; compose runs the same tag with
# `alembic -c /app/alembic.ini upgrade head` before the API starts.

FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Runtime and migration dependencies (fastapi, uvicorn, sqlalchemy, psycopg,
# pydantic, alembic, ...). The repository has one requirements file today.
COPY requirements-runtime.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install -r requirements-runtime.txt

# Local workspace packages. `--no-deps` is deliberate: requirements-runtime.txt above
# is the single source of runtime pins, and the workspace packages declare no external
# dependencies of their own.
COPY packages/core-py ./packages/core-py
COPY packages/adapters ./packages/adapters
COPY apps/api ./apps/api
COPY services/engine ./services/engine
RUN python -m pip install --prefix=/install --no-deps \
        ./packages/core-py \
        ./packages/adapters \
        ./apps/api \
        ./services/engine

FROM python:3.11-slim AS runtime

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
