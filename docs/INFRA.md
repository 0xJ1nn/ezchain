# INFRA — deployment contract

Status: **PAPER stack definition**. `infra/compose.yaml` builds the API and web images
from this repository, runs Postgres 16 and an Alembic job, and publishes only Caddy.
Images are built locally (`ezchain/api:local`, `ezchain/web:local`); no registry images
are published yet. Services that do not exist are recorded separately in
`infra/compose.blueprint.yaml` and are not part of the default stack.

The rule is unchanged: nothing is decorative. A service belongs in `compose.yaml` only
when code in this repository uses it, it has a health signal and a resource budget.

The supported path for a server you own is the one-command installer in
[tools/install/install.sh](../tools/install/install.sh); [docs/SELF-HOSTING.md](SELF-HOSTING.md)
covers sizing, install, secrets, TLS and backup. This file is the contract that installer
drives.

## 0. Runnable today vs blueprint

| Area | State |
|---|---|
| Local API + Next web + local Postgres (`tools/dev.sh`) | runnable |
| Alembic baseline and `ezchain-api migrate` | runnable and verified against a fresh Postgres database |
| `/healthz`, `/readyz`, `/metrics`, JSON request logs | runnable |
| Docker Compose, Caddy edge, images, Prometheus/Grafana | **verified** on 2026-10-22 (Docker 29.9.2 / Compose v5.6.1): image build + `compose up` succeeded, all services healthy, migration at head. See [docs/evidence/p0-production/docker-up.md](evidence/p0-production/docker-up.md) |
| Compose drill at the current head (`0022_strategy_creator`) | **not run in a container**: the authoring host has no Docker daemon. The daemon-free structure/chain phase passed, and the application phases were replayed on a host Postgres. See [docs/evidence/compose-drill/README.md](evidence/compose-drill/README.md) |

The Compose/Caddy files and YAML are reviewed statically and the app-side contracts
(`/healthz`, `/readyz`, `/metrics`, migrations, auth, `/api/[...path]`) are tested. The first
`docker compose up --build` has now been run successfully on 2026-10-22; see
[docs/evidence/p0-production/docker-up.md](evidence/p0-production/docker-up.md).

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
| `api` | builds `apps/api/Dockerfile` | 8000 (internal) | `GET /readyz` (compose); Dockerfile liveness `/healthz` | 1 / 512 MB |
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
   |── /healthz|/readyz ───────────────────────────────────────────────────────► api:8000
   └── /* ───────────────────────► web:3000

 api:8000/metrics is not published. Prometheus scrapes it over the app network with
 the operator token.
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
| `EZCHAIN_PUBLIC_URL` | yes (web) | exact browser origin, e.g. `http://localhost:8080`; used by the proxy same-origin check |
| `EZCHAIN_LOG_LEVEL` | no | `INFO`; structured access logs |
| `EZCHAIN_DATA_MODE` | no | `live` (default); `fixture` is for tests only |
| `EZCHAIN_TRUSTED_PROXY_CIDRS` | no | JSON list of reverse-proxy addresses allowed to supply `X-Forwarded-For`; empty trusts no additional proxy |
| `EZCHAIN_ENGINE_INLINE` | no | `true`; engine runs in the API process |
| `EZCHAIN_MARKET_SCAN_INLINE` | no | `true`; bounded market observation in the API |
| `EZCHAIN_BIND` / `EZCHAIN_PORT` | no | Host bind address (`127.0.0.1`) and port (`8080`) |
| `EZCHAIN_DOMAIN` | no | Public hostname for links; does not configure TLS |

Notes:

- `env_file` does **not** expand `${POSTGRES_PASSWORD}`. The database URL is built in
  `compose.yaml` from `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`; do not set it in
  `.env`.
- Percent-encode special characters in `POSTGRES_PASSWORD`, or use a long alphanumeric
  secret. `openssl rand -hex 32` avoids the problem entirely.
- Never commit `infra/.env`. `.gitignore` excludes `.env` and `.env.*` except
  `.env.example`.
- Secrets are not baked into images. The Docker build context is `.dockerignore`d against
  `.env` and key material.
- The API's `/healthz` and `/readyz` are public edge endpoints, not `/api/*`. The edge
  does not publish `/metrics`: it exposes order/fill counts and requires
  `EZCHAIN_API_TOKEN` unless the caller is loopback. Scrape `api:8000` over the private
  network with the token; keep the default `EZCHAIN_BIND=127.0.0.1`.
- The edge injects one deployment bearer token for every caller. It is not per-user auth;
  anyone who can reach the edge can mutate the paper account. Run it on a trusted host
  until per-user auth exists.
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
| `/metrics` | `api` | Prometheus text format. Loopback or the operator token. |
| `/` | `web` | Web container health. |

Prometheus config scrapes only `api:8000` and itself. The API serves `/metrics` to
loopback or with the operator token; the scrape reads that token from
`/etc/prometheus/api-token`, which Compose fills from `EZCHAIN_API_TOKEN`. Alerts cover
the API scrape target, the API's `ezchain_up` gauge, the HTTP request counter, the
paper-order counter, and the four operational families sampled on each scrape (source
freshness and its budget, breaker state, queue depth and capacity, ledger drift). There
are no executor or backup alerts because those metrics do not exist.

The observability profile:

```bash
docker compose -f infra/compose.yaml --profile observability up -d prometheus
# optional dashboard, no dashboards are provisioned yet
docker compose -f infra/compose.yaml --profile observability up -d grafana
```

Grafana is bound to `127.0.0.1:3001` and has no provisioned dashboards. It falls back to
the image's default admin credentials unless `EZCHAIN_GRAFANA_ADMIN_PASSWORD` is set; do
that before enabling the profile on a shared host.

### Stream health and the staleness gate

The pump.fun firehose is a data-layer seam, not an HTTP route, so its health is a dict the
operator process reads rather than an endpoint: `FirehoseFeed.health(now)` in
`ezchain_adapters.firehose` and `FirehoseSource.health(now)` in `ezchain_ingest.source`.
Every field is measured, never defaulted. The feed reports the flat keys below; the source
reports the same facts as `gate.as_dict()` (`live`, `stale`, `age_s`, `reason`,
`stale_after_s`) plus its own counter dict, with `queue` carrying the packet census:

| Field | Surface | Meaning |
|---|---|---|
| `last_event_age_seconds` / `gate.age_s` | feed / source | Age of the newest *decoded* observation at the read clock. `null` before the first event — an unobserved stream is not a fresh one. |
| `reconnects` | feed | Reconnect count recorded by `record_reconnect()`; the source's own socket owner reports them. |
| `dropped` | both | Observations discarded from the bounded buffer under pressure (also in `queue.dropped`). |
| `refused_stale` | both | Buffered observations the entry read discarded because the observation itself was older than the budget. Counted per item, separate from `dropped`. |
| `decoded_ratio` / `rejected_ratio` | both | Decoded over decoded plus rejected. `null` when nothing has arrived, never a perfect `1.0`. |

Three behaviours follow from it:

- **A gap is untradeable, and a gap does not end at the first fresh event.**
  `FirehoseFeed.entries(now)` and `FirehoseSource.pull(now)` raise `StaleFeed` /
  `pumpfire.health.StaleStream` once the newest observation is older than the budget
  (60 s); the refusal carries the age and the budget and drains nothing. Once the feed is
  live again, each drained observation is *still* aged on its own `observed_at` (feed) or
  its own handoff clock (source), and any item older than the budget is refused and counted
  in `refused_stale`. A recovered feed therefore serves only observations it can defend; a
  pre-gap backlog never rides in on a new event's liveness.
- **Liveness is decoded trade data, not socket traffic.** `FirehoseSource.receive` decodes
  first and observes the gate only on a decoded event. A payload the adapter rejects is
  counted and never refreshes the gate, so a provider that only emits rejects goes stale and
  `pull()` serves nothing.
- **Pressure costs the oldest data, not the reader.** The handoff buffer is bounded
  (`queue_size`, default 500), drops the oldest observation when full and counts each
  drop. Its census is synchronous, so reading depth and counters never blocks the socket
  reader.

`entries()`/`pull()` are the only entry reads. `FirehoseFeed.snapshot()`,
`FirehoseFeed.fresh_events()` and `FirehoseFeed.stream()` are display surfaces: they include
observations from before a gap so a viewer can see what the feed last carried, and each row
carries a read-time `age_seconds` plus the contract's own `stale` flag (that flag applies the
contract's default 60 s budget, not a feed configured differently), so an old row is visibly
old. A display row is not an entry and is never a trading decision — a `stream()` delta is
stamped with the clock it is read at, not with its own timestamp.

A stale transition is recorded by `ingest()` when a late event closes the gap, as well as by
any reader, and keyed by the observation the gap started from, so the count is one per
transition and does not repeat on repeated reads. A gap that is still open and that nobody
read can only be reported at the first read: the feed has no timer of its own.

`tests/adapters/fixtures/firehose_sample.json` holds one record of real pump.fun traffic and
five synthetic records of the same wire shapes. Every signature, mint, wallet and creator in
the file is a sentinel and the captured record's amounts, price, timestamp and reserves are
the recorded ones; every record replays through the same `normalize_firehose_event` path
every test and every live event uses.

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

The repository ships a scripted path that also records what the dump contained, so a
restore can be proven rather than assumed:

```bash
python -m tools.ops backup --database "$EZCHAIN_DATABASE_URL" --dest backups/
python -m tools.ops restore --database "$SCRATCH_URL" --manifest backups/<label>.manifest.json
python -m tools.ops rehearse --admin-database "$ADMIN_URL" --dest backups/<drill> --report backups/<drill>/report.json
```

`backup` writes the dump and a manifest holding its SHA-256 plus a per-table row digest
taken from the same exported snapshot. `restore` rebuilds a fresh database (the name must
contain `test`, `scratch` or `_ci`), then requires every ledger table to match the manifest
row-for-row and the API's reconciliation report to be `ok: true`; it exits `1` when a
restore is short. `rehearse` runs the whole drill — build, back up, drop the source,
restore, verify — and records the elapsed recovery time. The procedure and the failure
table are in [RUNBOOK §8](RUNBOOK.md#8-restore-drill--disaster-recovery).

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

The Docker path is **not verified** for the scripted tooling: the authoring host had no
Docker daemon. The tooling runs against the host `pg_dump`/`pg_restore` client binaries and
any reachable Postgres; inside the Compose stack those binaries are in the `postgres`
container, so run the commands there or with `PGHOST` pointing at it. The measured drill in
[evidence](evidence/release-gate/backup-restore-rehearsal.md) used a local Postgres 16, not
the Compose stack.

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

Local ports: web 3000 and API 8000. In the Compose stack those are container-internal;
the only host port is Caddy on `EZCHAIN_PORT` (8080), with Prometheus 9090 and Grafana
3001 published on loopback only under the observability profile. No local `sudo`, no host
database, no global installs.

## 11. CI/CD status

GitHub Actions is not available on this account. The mandatory local gate is
`bash tools/ci.sh` (agent stubs, design tokens, wireframe, links, secret scan, and runtime
gates when the toolchains are installed). Image builds are not part of that gate; they
require a Docker runtime. Compose and Prometheus files are YAML-validated by the operator.

The Compose drill is opt-in for the same reason: `EZCHAIN_GATE_DRILL` defaults to `off`.
`static` runs the daemon-free structure and migration-chain checks; `run` runs the whole
drill and needs a Docker daemon. The script is `tools/ops/drill.sh`, and a host without a
daemon gets exit `2` (refused) rather than a green step — see
[§12.9](RUNBOOK.md#129-compose-drill) and
[the evidence pack](evidence/compose-drill/README.md).
