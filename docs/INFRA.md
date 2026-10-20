# INFRA — deployment contract

Status: blueprint. `infra/compose.yaml` is the deployment contract for M1; images are not
built yet. Every service listed must exist, have a healthcheck, a resource budget and a
log/metric path. Nothing may be "decorative" (v1 shipped Postgres and Redis that no code
used).

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
- Only Caddy is published. API/executor are on an internal network. Postgres/Redis/NATS
  are never published.
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

## 6. Migrations and upgrades

- Images are immutable and tagged `MAJOR.MINOR.PATCH`; `latest` is never used in prod.
- `migrate` runs before api/engine start; it is idempotent and takes a pre-migration
  backup automatically.
- Forward-only. No destructive migrations (CONTRACT I8). A failed migration stops the
  rollout; the previous image remains.
- Upgrade procedure: backup → pull → migrate → start → `ezchain-admin verify` →
  reconcile → unpause. Documented in `docs/RUNBOOK.md` (M2).

## 7. Observability

- **Metrics** (Prometheus): HTTP RED, intent rate/latency, decision reasons, quote
  latency, fill ratio, slippage realised vs quoted, source freshness seconds, queue depth,
  breaker state, ledger drift (must be 0), vault unlock age.
- **Traces** (OTel): API → engine → executor → venue, sampled 10% (100% on error), with
  `intent_id`.
- **Logs** (Loki/JSON): structured, request id, actor, redacted. No secrets, no raw keys,
  no signed transactions.
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
