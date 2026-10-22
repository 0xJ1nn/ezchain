# INFRA — deployment contract

Status: **PAPER stack definition**. `infra/compose.yaml` builds the API and web images
from this repository, runs Postgres 16 and an Alembic job, and publishes only Caddy.
Images are built locally (`ezchain/api:local`, `ezchain/web:local`); no registry images
are published yet. Services that do not exist are recorded separately in
`infra/compose.blueprint.yaml` and are not part of the default stack.

The rule is unchanged: nothing is decorative. A service belongs in `compose.yaml` only
when code in this repository uses it, it has a health signal and a resource budget.

## 1. Topologies

| Topology | Who | Services |
|---|---|---|
| **PAPER (default)** | one trader, one machine | `postgres`, `migrate`, `api`, `web`, `caddy` |
| **PAPER + observability** | debugging | `--profile observability`: `prometheus`, optional `grafana` |
| **Blueprint (not built)** | future | `redis`, `nats`, `engine`, `executor`, `ingest`, `otel-collector` in [`infra/compose.blueprint.yaml`](../infra/compose.blueprint.yaml) |
| **Multi-user / team** | shared host | same images + external Postgres; horizontal API/web scaling is untested |
| **Air-gapped** | keys on a separate box | not possible yet — there is no executor or key custody |

Windows/macOS: Docker Desktop or Podman. Linux is the target. Kubernetes/Helm is not
required and no manifests are shipped.

## 2. Services in the PAPER stack

| Service | Image / build | Ports | Health | Budget |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 (internal) | `pg_isready` | 1 CPU / 1 GB |
| `migrate` | builds `apps/api/Dockerfile` as `ezchain/api` | — | exit code (`restart: "no"`) | 0.5 / 512 MB |
| `api` | builds `apps/api/Dockerfile` | 8000 (internal) | `GET /healthz` | 1 / 512 MB |
| `web` | builds `apps/web/Dockerfile` | 3000 (internal) | `GET /` | 0.5 / 256 MB |
| `caddy` | `caddy:2.8-alpine` | `${EZCHAIN_BIND}:${EZCHAIN_PORT}` → 8080 | `GET /` through the proxy | 0.25 / 128 MB |
| `prometheus` | `prom/prometheus:v2.54.1` | 9090 (localhost by default) | `/-/healthy` | 0.5 / 512 MB |
| `grafana` | `grafana/grafana:11.2.0` | 3001 (localhost by default) | `/api/health` | 0.5 / 256 MB |

`migrate` and `api` share the same image tag. Compose starts `migrate` after Postgres is
healthy and starts `api` only after `migrate` exits successfully
(`service_completed_successfully`). A failed migration therefore leaves the previous API
container running.

There is no TimescaleDB requirement: plain `postgres:16-alpine` is enough for the current
schema. Market snapshots and ledger entries are ordinary tables. Hypertables, retention
policies and continuous aggregates are not implemented.

## 3. Networks and edge

```
host :8080
   |
 caddy ── /api/* ───────────────► web:3000 ── (Next route handler adds bearer) ──► api:8000
   |── /healthz|/readyz|/metrics ──────────────────────────────────────────────► api:8000
   └── /* ───────────────────────► web:3000
```

- `data` is `internal: true`. Only `postgres`, `migrate` and `api` attach to it.
- `app` carries browser-facing traffic: `caddy`, `web`, `api`, and the observability
  tools.
- In the default stack, only Caddy publishes a host port. Postgres and the API are never
  published. The observability profile adds Prometheus and Grafana on `127.0.0.1` only.
- Caddy listens on `:8080` (not `localhost:8080`) so it works regardless of the host
  name used to reach the container. TLS is not terminated in this stack; put a TLS
  terminator in front for public use.
- `/api/*` deliberately goes to `web:3000`, not to `api:8000`. The Next.js route handler
  is the only component that knows `EZCHAIN_API_TOKEN`; the browser never receives it.
- Security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
  `Permissions-Policy`, `-Server`) are set by Caddy.

## 4. Configuration and secrets

`infra/.env.example` is the template. Copy it to `infra/.env`; Compose reads that file for
`${...}` interpolation and also passes it to the containers via `env_file`.

| Key | Required | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | Postgres password; also interpolated into the API database URL |
| `EZCHAIN_API_TOKEN` | yes | Shared bearer token for `/api/*`; at least 32 characters |
| `EZCHAIN_ENVIRONMENT` | yes | `prod` in the stack |
| `EZCHAIN_DATABASE_URL` | yes | `postgresql+psycopg://...@postgres:5432/ezchain`; Compose overrides it from `POSTGRES_PASSWORD` |
| `EZCHAIN_AUTO_CREATE_SCHEMA` | yes | `false`; Alembic owns the schema |
| `EZCHAIN_AUTH_ENABLED` | yes | `true` |
| `EZCHAIN_API_URL` | yes (web) | `http://api:8000` |
| `EZCHAIN_DATA_MODE` | no | `live` (default); `fixture` is for tests only |
| `EZCHAIN_ENGINE_INLINE` | no | `true`; engine runs in the API process |
| `EZCHAIN_MARKET_SCAN_INLINE` | no | `true`; bounded market observation in the API |
| `EZCHAIN_BIND` / `EZCHAIN_PORT` | no | Host bind address (`127.0.0.1`) and port (`8080`) |
| `EZCHAIN_DOMAIN` | no | Public hostname for links; does not configure TLS |

Notes:

- `env_file` does **not** expand `${POSTGRES_PASSWORD}`. That is why the database URL is
  built in `compose.yaml` (`environment:` overrides `env_file`). The line in
  `.env.example` documents the shape; the container receives the interpolated value.
- Percent-encode special characters in `POSTGRES_PASSWORD`, or use a long alphanumeric
  secret. `openssl rand -hex 32` avoids the problem entirely.
- Never commit `infra/.env`. `.gitignore` excludes `.env` and `.env.*` except
  `.env.example`.
- Secrets are not baked into images. The Docker build context is `.dockerignore`d against
  `.env` and key material.
- The API's `/healthz`, `/readyz` and `/metrics` are edge endpoints, not `/api/*`. Keep
  the default `EZCHAIN_BIND=127.0.0.1` unless you intentionally expose metrics.
- Future credentials (`REDIS_URL`, `NATS_URL`, `EZCHAIN_VAULT_KEY`, RPC keys) are in a
  clearly marked commented block in `.env.example`. They do nothing today.

Configuration precedence for the app: `compose environment` > `env_file` > defaults in
`apps/api/src/ezchain_api/config.py`. The Python settings schema remains the single home
for default numbers; do not duplicate limits in compose.

## 5. Migrations

- Alembic is the only schema writer. The API runs with
  `EZCHAIN_AUTO_CREATE_SCHEMA=false`; it never calls `metadata.create_all`.
- `migrate` is a one-shot service:
  `alembic -c /app/alembic.ini upgrade head`.
- Migrations are forward-only and non-destructive ([CONTRACT.md](CONTRACT.md) I8). A data
  reset is an operator action and must be documented and audited.
- The migration bundle lives in `apps/api/` (`alembic.ini`, `alembic/`) and is copied into
  the API image at `/app`. The same image is the migration job; there is no separate
  migration image to drift.
- A failed migration stops the rollout: `api` does not start until `migrate` exits 0.
- Before an upgrade, take a dump (§7). Rollback of code is redeploying the previous
  image; schema rollback is an incident, not a routine command.

## 6. Health, metrics and alerts

| Endpoint | Owner | Semantics |
|---|---|---|
| `/healthz` | `api` | Liveness. Process can serve HTTP; must not depend on the database. |
| `/readyz` | `api` | Readiness. Checks the dependencies needed to serve paper traffic (database). |
| `/metrics` | `api` | Prometheus text format. |
| `/` | `web` | Web container health. |

Prometheus config scrapes only `api:8000` and itself. Alerts cover the API scrape target,
the API's `ezchain_up` gauge, the HTTP request counter and the paper-order counter. There
are no executor, queue, breaker, drift or backup alerts because those metrics do not
exist.

The observability profile:

```bash
docker compose -f infra/compose.yaml --profile observability up -d prometheus
# optional dashboard, no dashboards are provisioned yet
docker compose -f infra/compose.yaml --profile observability up -d grafana
```

Grafana is bound to `127.0.0.1:3001` and has no provisioned dashboards. It falls back to
the image's default admin credentials unless `EZCHAIN_GRAFANA_ADMIN_PASSWORD` is set; do
that before enabling the profile on a shared host.

## 7. Storage, backup and restore

| Data | Location | Backup |
|---|---|---|
| Domain, ledger, market snapshots, strategy runs | volume `pgdata` | operator `pg_dump -Fc`, verified restore |
| Caddy certificates/state | `caddydata`, `caddyconfig` | not required for PAPER (plain HTTP) |
| Prometheus TSDB | `promdata` | not backed up; monitoring history only |
| Grafana state | `grafanadata` | not backed up in PAPER |

There is no S3/MinIO artifact store, no NATS JetStream and no Redis in this stack.

Dump:

```bash
docker compose -f infra/compose.yaml exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "ezchain-$(date +%F).dump"
```

Restore drill (non-destructive: restore into a scratch database first):

```bash
docker compose -f infra/compose.yaml exec -T postgres sh -c \
  'createdb -U "$POSTGRES_USER" ezchain_restore'
docker compose -f infra/compose.yaml exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d ezchain_restore --no-owner' < ezchain-YYYY-MM-DD.dump
```

Verify the scratch database (row counts, ledger reconciliation) before replacing the
live one. A disaster restore into `ezchain` is a deliberate, audited action: stop `api`
and `web`, restore, then start `migrate` and `api` again. A restore that cannot reproduce
the ledger to the last entry is a failed drill.

## 8. Upgrade and rollback

1. Dump the database (§7) and record the dump's SHA-256.
2. `git fetch` the release, check out the tag, and build:
   `docker compose -f infra/compose.yaml build`.
3. `docker compose -f infra/compose.yaml up -d migrate` and confirm exit 0.
4. `docker compose -f infra/compose.yaml up -d api web caddy`.
5. Check `/healthz`, `/readyz`, `/metrics` and the reconciliation endpoint.
6. Rollback = redeploy the previous image tag. Do not run a down-migration; migrations
   are forward-only.

Pinning: use `EZCHAIN_VERSION=<git-sha-or-semver>` to tag locally built images. `latest`
is never a production tag.

## 9. Not in the PAPER stack (and why)

| Service | Why it is absent |
|---|---|
| TimescaleDB | The current schema has no hypertables; plain Postgres 16 is sufficient. Add it with the first candle/retention use case, not before. |
| Redis | No cache, rate-limit or lock path uses it. The adapters have in-process limits. |
| NATS | No event bus; the engine is inline and the market scanner writes to Postgres. |
| Standalone `engine` | `services/engine` is installed as a package and runs inside `api` (`EZCHAIN_ENGINE_INLINE=true`). |
| `executor` | Not built. There is no key custody, signing or broadcast path. |
| `ingest` | Not built. Market observation is request-driven and bounded inside the API. |
| OTel collector | `infra/otel/collector.yaml` exists, but the app does not export OTLP yet. |

Each of these is listed in `infra/compose.blueprint.yaml` with its blocking reason. Do not
add a service to `compose.yaml` before the code path that uses it exists.

## 10. Local development (no containers)

```bash
createdb ezchain
cp .env.example .env
PYTHON=python3.11 bash tools/setup-python.sh
bash tools/dev.sh                # API :8000, web :3000
```

Ports: web 3000, API 8000, Caddy 8080, Prometheus 9090, Grafana 3001. No local `sudo`, no
host database, no global installs. Everything in the Docker stack runs in containers
except the two dev servers above.

## 11. CI/CD status

GitHub Actions is not available on this account. The mandatory local gate is
`bash tools/ci.sh` (agent stubs, design tokens, wireframe, links, secret scan, and runtime
gates when the toolchains are installed). Image builds are not part of that gate; they
require a Docker runtime. Compose and Prometheus files are YAML-validated by the operator.
