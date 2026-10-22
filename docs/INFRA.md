# INFRA — deployment contract

Status: blueprint. `infra/compose.yaml` is the deployment contract for M1; images are not
built yet. Every service listed must exist, have a healthcheck, a resource budget and a
log/metric path. Nothing may be "decorative" (v1 shipped Postgres and Redis that no code
used).

## 0. Runnable today vs blueprint

| Area | State |
|---|---|
| Local API + Next web + local Postgres (`tools/dev.sh`) | runnable |
| Alembic baseline and `ezchain-api migrate` | runnable and verified against a fresh Postgres database |
| `/healthz`, `/readyz`, `/metrics`, JSON request logs | runnable |
| Docker Compose, Caddy edge, images, Prometheus/Grafana/Loki | **blueprint** — not built or verified here |

The Docker topology is owned by a separate change. Do not read this document as evidence
that `docker compose up` works.

## 1. Topologies

| Topology | Who | Services |
|---|---|---|
| **Solo (default)** | one trader, one machine | compose `core`: postgres, redis, nats, api, engine, executor, ingest, web, caddy |
| **Solo + observability** | debugging | `--profile observability`: otel-collector, prometheus, grafana, loki |
| **Multi-user / team** | shared host | same images + external Postgres, S3, managed NATS; API/engine scale N |
| **Air-gapped** | keys on a separate box | executor on host B, NATS over mTLS, no outbound from core except RPC via allowlist proxy |

Windows/macOS: Docker Desktop or Podman. Linux: the target. The app must not require
Kubernetes; Helm can come later without changing images.

## 2. Compose services

| Service | Image | Ports | Health | Budget |
|---|---|---|---|---|
| `postgres` | `timescale/timescaledb:2.17-pg16` | 5432 (internal) | `pg_isready` | 1 CPU / 1 GB |
| `redis` | `redis:7-alpine` | 6379 (internal) | `redis-cli ping` | 0.5 / 256 MB |
| `nats` | `nats:2.10-alpine` | 4222 (internal), 8222 (internal) | `/healthz` | 0.5 / 256 MB |
| `api` | `ghcr.io/ezchain/api` | 8000 (internal) | `/healthz` | 1 / 512 MB |
| `engine` | `ghcr.io/ezchain/engine` | — | `/healthz` | 1 / 1 GB |
| `executor` | `ghcr.io/ezchain/executor` | 8010 (internal) | `/healthz` | 1 / 512 MB |
| `ingest` | `ghcr.io/ezchain/ingest` | — | `/healthz` | 2 / 1 GB |
| `web` | `ghcr.io/ezchain/web` | 3000 (internal) | `/` | 0.5 / 256 MB |
| `caddy` | `caddy:2-alpine` | 8080, 8443 | — | 0.25 / 128 MB |
| `otel-collector` | `otel/opentelemetry-collector-contrib` | 4317 | — | 0.5 / 256 MB |
| `prometheus` | `prom/prometheus` | 9090 | `/-/healthy` | 0.5 / 512 MB |
| `grafana` | `grafana/grafana` | 3001 | `/api/health` | 0.5 / 256 MB |
| `loki` | `grafana/loki` | 3100 | `/ready` | 0.5 / 512 MB |

No admin credentials in the compose file. `.env` holds `${POSTGRES_PASSWORD}` etc.;
Grafana anonymous access is off; it binds to localhost or a private network.

## 3. Storage

| Data | Location | Backup |
|---|---|---|
| Postgres (domain, ledger, config) | volume `pgdata` | nightly `pg_dump -Fc`, verified restore, 30 daily / 12 monthly |
| Timescale ticks/candles | same DB, hypertables | same dump; raw retention 30 d |
| NATS JetStream | volume `natsdata` | not backed up; replayable from DB outbox |
| Redis | none | never backup; cache only |
| Artifacts (backtests, exports, models) | `S3`/MinIO or volume `artifacts` | versioned bucket, lifecycle 90 d |
| Secrets | vault file / KMS | separate, sealed, escrowed recovery key (printed, offline) |

Restore drill is part of release: restore into a scratch container, run
`ezchain-admin reconcile`, assert zero drift, record the duration in `docs/RELEASES.md`.

## 4. Networking and TLS

- Caddy terminates TLS, serves the web app, proxies `/api` and `/ws`, sets CSP/HSTS.
The web container must set `EZCHAIN_PUBLIC_URL` to the public edge origin; the
`/api/[...path]` proxy rejects cross-site mutations with `403 CROSS_ORIGIN_DENIED`.
- Only Caddy is published. API/executor are on an internal network. Postgres/Redis/NATS
  are never published.
- **Browser `/api/*` must reach the Next server proxy (`web:3000`), not the API directly,
  when `EZCHAIN_API_TOKEN` is set.** The current compose/Caddy blueprint sends `/api/*`
  straight to `api:8000`; that edge change belongs to the Docker change. Direct API routes
  remain fine for server-to-server calls that carry the bearer token, and `/ws` is
  unaffected today because websockets are not a mutation path in P0.
- Modes: `localhost` (http, self-signed optional), `lan` (mkcert), `public` (ACME with a
  real domain). Auth cannot be disabled in `public` mode — the process refuses to boot.
- Executor accepts signed internal requests only (HMAC + mTLS); no browser path.

## 5. Configuration and secrets

```toml
# ezchain.toml — committed example, no secrets
[server] public_url = "http://localhost:8080"
[auth]   mode = "local"           # local | oidc
[chains] enabled = ["solana", "base"]
[risk]   max_daily_loss_usd = 250
```

Secrets are references:

```toml
[rpc.helius] api_key = "env:HELIUS_API_KEY"
[cex.binance] api_key = "vault:binance/api_key"
[vault] master_key = "env:EZCHAIN_VAULT_KEY"   # 32 bytes, base64
```

Precedence: defaults < `ezchain.toml` < env < DB workspace overrides (audited, with an
actor and a reason). A missing required secret fails startup with the exact key name and
how to provide it.

The API reads `EZCHAIN_DATABASE_URL` (the env prefix is `EZCHAIN_`). `DATABASE_URL` in the
compose environment is not read by the settings model; keep the two in sync until the
Docker change unifies them.

### 5.1 API auth

`GET`/`HEAD`/`OPTIONS` under `/api/` are open. Every other method requires
`Authorization: Bearer $EZCHAIN_API_TOKEN`, enforced by middleware so new routes are
covered by default. The Next server injects the token in its `/api/[...path]` proxy; the
browser never receives it. In dev/test an unset token allows mutations with one warning.
With `EZCHAIN_ENVIRONMENT=prod` the API refuses to start unless `EZCHAIN_AUTH_ENABLED=true`,
`EZCHAIN_API_TOKEN` is at least 32 characters, `EZCHAIN_AUTO_CREATE_SCHEMA=false`,
`EZCHAIN_DATABASE_URL` is non-default and includes a password, `EZCHAIN_DATA_MODE=live`,
and `EZCHAIN_CORS_ORIGINS` contains no `*`.

## 6. Migrations and upgrades

- Images are immutable and tagged `MAJOR.MINOR.PATCH`; `latest` is never used in prod.
- `migrate` runs before api/engine start; it is idempotent and takes a pre-migration
  backup automatically. The runner is `ezchain-api migrate` (or
  `python -m ezchain_api.migrate`); both call `alembic upgrade head`.
- The database URL comes from Settings, never from `alembic.ini`.
- Forward-only. No destructive migrations (CONTRACT I8). A guard test scans every
  `upgrade()` body for `drop_table`, `drop_column`, `TRUNCATE`, `DELETE FROM`, `wipe` and
  `clean_slate`; `downgrade()` may drop.
- Readiness is `/readyz`: it returns 503 unless `SELECT 1` succeeds **and**
  `alembic_version` equals the script head. `/healthz` is liveness only.
- Upgrade procedure: backup → pull → migrate → start → wait for `/readyz` →
  `ezchain-admin verify` → reconcile → unpause. Documented in `docs/RUNBOOK.md`.

## 7. Observability

- **Metrics** (Prometheus): HTTP RED, intent rate/latency, decision reasons, quote
  latency, fill ratio, slippage realised vs quoted, source freshness seconds, queue depth,
  breaker state, ledger drift (must be 0), vault unlock age. The API exposes
  `/metrics` with `ezchain_http_requests_total`, the
  `ezchain_http_request_duration_ms` histogram, `ezchain_paper_orders_total{status}`,
  `ezchain_paper_fills_total` and `ezchain_up`; labels are bounded by route template and a
  series cap.
- **Traces** (OTel): API → engine → executor → venue, sampled 10% (100% on error), with
  `intent_id`.
- **Logs** (Loki/JSON): one JSON line per request with `ts`, `level`, `logger`, `msg`,
  `request_id`, `method`, route template, `status` and `duration_ms`. No Authorization,
  bodies or query values. `X-Request-ID` is accepted only when it is 1–64 characters of
  `[A-Za-z0-9_-]`; otherwise a uuid4 is generated and returned in the response.
- **Alerts**: drift > 0, executor unreachable, chain source stale > 60 s, queue depth >
  10k, breaker open > 5 min, backup failed, disk > 80%, certificate < 14 d.
- **Ops page** in the product mirrors these; Grafana is optional.

## 8. Backup, recovery, disaster

| Scenario | RTO | Procedure |
|---|---|---|
| One service crashed | < 1 min | restart policy; state from DB |
| Node reboot | < 5 min | compose up; executor reconciles open orders |
| DB corruption | < 1 h | restore last verified dump; replay NATS/outbox |
| Lost vault key | **no recovery** | keys are unrecoverable by design; recovery phrase is the user's responsibility |
| Host loss | < 4 h | restore on new host from dump + artifacts |

The vault recovery phrase is shown once at creation, never stored server-side. Losing it
loses the hot wallets; watch-only and hardware wallets survive.

## 9. CI/CD

- Every PR: `tools/ci.sh` (format, lint, types, unit, invariants, adapter contract,
  OpenAPI diff, wireframe check, secret scan, compose config validation).
- `main`: build images, SBOM (syft), Trivy scan, sign with cosign, push to GHCR.
- Release: tag `vX.Y.Z`, changelog, release notes with known limitations, compose image
  digests, upgrade notes, restore-drill result.
- Nightly: fresh-DB migration test, 24 h soak, adapter fixtures against mainnet read-only,
  dependency audit.
- No workflow may be committed without being runnable (`workflow_dispatch` at minimum) —
  v3/ezcore both shipped dead CI.

## 10. Local development

```bash
cp infra/.env.example infra/.env
docker compose -f infra/compose.yaml --profile core --profile observability up -d
pnpm install && pnpm dev          # web + api with hot reload
uv sync --all-packages            # python workspace
tools/ci.sh
```

Ports: web 3000, api 8000, caddy 8080, grafana 3001, prometheus 9090.
No local `sudo`, no host database, no global installs. Everything runs in containers
except the two dev servers.
