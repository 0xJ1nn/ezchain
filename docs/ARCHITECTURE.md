# ARCHITECTURE — ezCHAIN

Status: **PAPER stack with production operations**. The web UI (`apps/web`), FastAPI edge
(`apps/api`), strategy runtime (`services/engine`, inline), domain/ledger
(`packages/core-py`) and DexScreener / Jupiter adapters (`packages/adapters`) are built,
with Alembic migrations, bearer auth, `/healthz`/`/readyz`/`/metrics`, structured logs and a
stress harness. Market observation is a bounded inline loop in the API. Graph strategies
are live on the PAPER path (`/api/v1/graph/*`, `/api/v1/backtests`). The **executor**, key
custody and a separate ingest worker are not built; live execution is disabled on every
chain, and Timescale/tick retention is deferred. This document is the contract every later
PR implements; deviations require an ADR.

## 1. Shape

**Modular monolith + two hard boundaries.** The core (API, engine, ingest workers) is one
deployable modular application with enforced package boundaries. The **executor** is a
separate process because it is the only key holder. The **ingest** workers can scale
separately because they are I/O-bound and chain-specific.

```mermaid
flowchart LR
  subgraph Edge
    W[apps/web<br/>Next.js]
  end
  subgraph Core["core (deployable)"]
    A[apps/api<br/>HTTP + WS]
    E[services/engine<br/>strategies + automations]
    R[risk policy<br/>package]
  end
  X[services/executor<br/>keys, sign, broadcast]
  I[services/ingest<br/>chain + market data]
  DB[(Postgres 16<br/>+ TimescaleDB)]
  OBJ[(S3/MinIO<br/>artifacts, exports)]
  N[(NATS JetStream)]
  RD[(Redis<br/>cache, rate limits)]

  W -->|REST + WS| A
  A --> DB
  A --> E
  E --> R
  E -->|intent| X
  X -->|signed tx| chain[(Chains / venues)]
  I -->|normalized events| N
  N --> E
  E --> DB
  X --> DB
  A --> N
  E --> OBJ
  I --> RD
  A --> RD
```

Why not microservices: v2 and v3 both described 9 services and shipped one process, so the
boundary was fiction; pumpbot's monolith made iteration fast but concurrency unsafe. A
modular monolith with real import boundaries and one separately-scaled executor gets the
iteration speed without lying about the topology.

## 2. Deployables

| Deployable | Language | Responsibility | Scaling |
|---|---|---|---|
| `apps/web` | TypeScript / Next.js | UI, routing, formatting, WS client | stateless, N |
| `apps/api` | Python 3.12 / FastAPI | auth, REST, WS gateway, read models, command API | stateless, N |
| `services/engine` | Python | strategy runtime, automation scheduler, signal routing, risk | 1 per workspace partition |
| `services/executor` | Python | key custody, signing, broadcast, fill confirmation | 1 per key shard |
| `services/ingest` | Python (Rust only if measured) | market/chain streams, normalization, candles | per source, N |
| `migrate` | Python | one-shot Alembic | run-to-completion |
| `apps/desktop` | TypeScript / Electron + Python | native shell, private sidecar supervision, tray/background | 1 per desktop user |

Single-node default = one `core` container (api+engine) + `executor` + `ingest` + data
services. The same images scale out unchanged.

### Desktop deployment

`apps/desktop` is a native Electron shell for a single user. It starts the same FastAPI
edge and the traced Next.js server as private loopback sidecars, with an embedded SQLite
database at `~/.config/ezCHAIN/ezchain.db` instead of Postgres. Both sidecars bind
`127.0.0.1` on ephemeral ports; the renderer gets no token or Node access. Closing the
window hides to the tray and the sidecars keep running; a watchdog restarts either one on
crash. AppImage and `.deb` are built from the repository; macOS/Windows targets need a
build on their own host. See [DESKTOP.md](DESKTOP.md).

## 3. Module map (monorepo)

```
apps/
  web/                     # Next.js 15, App Router, CSS tokens (ADR-0013)
  api/                     # FastAPI edge
services/
  engine/                  # strategy host, automation scheduler, signal router
  executor/                # vault, signer, broadcaster, confirmer
  ingest/                  # sources/{chain,venue,cex,social}, normalizers, candles
packages/
  core_py/                 # domain: money, ledger, orders, positions, risk policy
  sdk_py/                  # public strategy/automation SDK (Apache-2.0)
  adapters/
    chain_solana/ chain_evm/ chain_move/ chain_cosmos/ chain_bitcoin/ chain_ton/ ...
    venue_jupiter/ venue_uniswap/ venue_pancake/ venue_oneinch/ venue_cex/
  ui/                      # design tokens + primitives shared by web
  contracts/               # OpenAPI + event schemas (generated, checked in)
tools/                     # gates, generators
design/                    # wireframe + brief (source of truth for visuals)
infra/                     # compose, Caddy, Prometheus, Grafana
```

Enforced boundaries (import-linter):

- `core_py` imports nothing from `apps/`, `services/`, `adapters/`, `sdk_py`.
- `apps/api` never imports `services/executor`; only the executor HTTP client.
- `adapters/*` import `core_py` and their chain SDK; never another adapter.
- `sdk_py` imports only `core_py` types (no platform internals).
- `services/engine` never touches key material or signing libraries.

Module size budget: ≤ 800 LOC per module, ≤ 400 LOC per file outside generated code. A
module that needs more is two bounded contexts. (v3's 697 KB `service.py` is the counter-
example.)

## 4. Data flow

### 4.1 Market data

`ingest` connects to RPC/WS/REST per source → normalizes into `MarketEvent`
(`chain, venue, pair, price, liquidity, volume, fees, source, observed_at, units`) →
publishes to NATS `market.*` → engine consumes; API serves snapshots; candles are
materialized into Timescale hypertables.

Rules: every event declares units and freshness; a late event is flagged, not silently
reordered; duplicate `(source, pair, observed_at)` is idempotent.

**P0 inline observation.** `apps/api` also runs a bounded market monitor
(`EZCHAIN_MARKET_SCAN_INLINE`, default on) so the platform knows what it can see even when
no user has a token page open. One scan calls `DexScreenerClient.discover_markets(chain="all")`
once; that adapter call fans out one token lookup per registered chain (seeds first, four
in flight) because `/latest/dex/tokens` caps a response at 30 pairs, then round-robins the
ranked per-chain rows. The returned coverage tells the monitor which chains were already
attempted, so it does not re-probe them; a second bounded per-chain pass runs only when the
all-chain call raised or returned no coverage. Each observed pair is projected onto its base
token and persisted through `paper.record_snapshot` before it can appear in
`/api/v1/markets/overview` or `/api/v1/markets/stored`. A failed provider or chain records
`error` (coverage reason `error`) and the scan continues. Row counts and ages are DB-derived,
so the overview survives a restart; attempt times and error text are process-local. A chain
with no rows is `no_data`, and an old row is `stale` after
`max(60s, scan interval + last scan duration + 15s)` — never zero.

### 4.2 Candle history

Graph strategies need indicator inputs (EMA/RSI/ATR) and a backtest needs bars, so OHLCV
history is served from `market_candles` rather than recomputed per request:

- `GET /api/v1/tokens/{chain}/{address}/candles?timeframe=5m&limit=300` (read-only, no auth)
  returns closed bars oldest first, prices and volumes as decimal strings, unknown volume as
  `null`, plus `source`, `stale`, `pool_address` and a `reason` code when empty.
- The pool is the highest-liquidity pair of the token snapshot the markets router already
  uses; the provider call passes our token address as `token`, so the price belongs to our
  token even when it is the quote side.
- **The forming bar is dropped** (`open_time + timeframe > now`) before it is stored or
  served, so an indicator never sees a bar that keeps changing under the same timestamp.
- The table is consulted first. A request reaches GeckoTerminal only when the newest stored
  bar is not the latest closed bar for `now`, or when the stored series is shorter than
  `limit` (one bounded page backwards with `before_timestamp`). Re-fetching a bar updates it
  in place: the unique key is `(chain, token_address, timeframe, open_time)`.
- Chains are capabilities, not branches: `ChainMeta.geckoterminal_id` (adapter-owned) is the
  only place a provider network id appears, and `to_dict()` exposes `capabilities.candles`.
  A registered chain with no provider (Osmosis, Cosmos Hub) returns
  `CANDLES_UNSUPPORTED_CHAIN` — never a fabricated series.
- `EZCHAIN_DATA_MODE=fixture` synthesizes a deterministic series labelled `fixture` and does
  not write to the table, so a demo cannot be mistaken for market data.

### 4.3 Graph strategy evaluation

A `kind = "graph"` strategy is typed JSON evaluated by `ezchain_core.graph.evaluate_graph`.
The API composition root supplies a `GraphContextBuilder` to the engine: it takes the live
token snapshot, closed candles from `market_candles`, the ledger-replayed position and the
paper account, and freezes them into one `EvalContext`. The scheduler maps the resulting
`GraphDecision` onto the existing `OrderRequest` path; there is no second execution path.
A buy uses `amount_usd`, a sell uses `sell_fraction × position value` capped at that value,
and a hold is logged with its reason. The dry-run endpoint uses the same builder and
evaluator but has no executor handle. Variables are carried in the run log
(`inputs.graph_vars`), not a mutable balance.

### 4.4 Order

```mermaid
sequenceDiagram
  participant S as Strategy/Automation
  participant E as engine
  participant R as risk
  participant X as executor
  participant V as venue/chain
  participant L as ledger

  S->>E: Intent(side, size, pair, limits, idempotency_key)
  E->>R: evaluate(intent, portfolio snapshot)
  R-->>E: ALLOW / DENY(reasons[])
  E->>X: execute(intent, decision, quote_policy)
  X->>V: quote (buy + full-size sell)
  V-->>X: route, amounts, impact, fees
  X->>X: verify identity/divergence/rent
  X->>V: sign + broadcast
  V-->>X: tx hash
  X->>L: journal(pending)
  V-->>X: confirmation
  X->>L: journal(fill, fee, rent)
  L-->>E: position updated
  E-->>S: on_fill
```

Idempotency: `intent_id` is unique; executor dedupes by it; a retry after a timeout
queries the chain before re-signing. Every state change is an event, every event is
persisted before side effects (outbox).

### 4.5 Valuation

`PositionView` is assembled by one projector from ledger + market events. It carries
`mark_status`, `mark_age`, `source`, and `paper|live`. The UI formats, never computes.
Stale mark → label, not a stale number presented as live.

## 5. Persistence

- **Postgres 16** is the only source of truth.
- **`market_candles`** holds closed OHLCV bars now (`chain, token_address, pool_address,
  timeframe, open_time, ohlcv, source, fetched_at`, unique on
  `(chain, token_address, timeframe, open_time)`). It is a plain table for the PAPER slice;
  moving it to the Timescale hypertable above is a later, forward-only migration.
- **`backtests`** stores immutable graph replays (`id, strategy_id nullable, request,
  result, created_at`). `strategy_id` is an opaque historical string, not a foreign key:
  deleting a strategy never deletes its measurements. The API reads it; only the API
  writes it.
- **TimescaleDB** hypertables for ticks/candles with retention (raw 7–30 d, 1m 1 y).
- **Outbox** table written in the same tx as domain state; a relay publishes to NATS.
- **NATS JetStream** for fan-out, durable consumers, dead-letter queue; not a database.
- **Redis** only for cache, rate limits and ephemeral locks. Loss of Redis never loses money.
- **S3/MinIO** for backtest artifacts, model files, exports; content-addressed.
- **No money state in JSON files, process memory or volumes.** (v2's paper wallet JSON is
  the counter-example.)

## 6. Events and schemas

`packages/contracts/events/*.json` (versioned, generated from Pydantic):

| Event | Key fields |
|---|---|
| `market.tick` | chain, venue, pair, price, liq, source, observed_at, units |
| `intent.created` | intent_id, strategy_id, side, size, limits, mode |
| `risk.decided` | intent_id, decision, reasons[], snapshot_hash |
| `order.submitted` | order_id, route_id, tx_hash, quote_envelope |
| `order.filled` | order_id, filled_amount, effective_price, fees, rent |
| `position.changed` | position_id, state, ledger_seq |
| `wallet.synced` | wallet_id, chain, block, balance_delta |
| `automation.fired` | automation_id, rule_id, action, result |
| `alert.raised` | severity, type, entity, dedupe_key |

Schemas are additive; consumers ignore unknown fields; breaking changes get a new
`vN` topic and a migration window.

## 7. Reliability

- **At-least-once delivery**, idempotent handlers, dead-letter review page in ops.
- **Backpressure**: bounded queues; ingest drops with a counter when the consumer lags,
  never blocks the chain reader.
- **Circuit breakers** per source/venue with half-open probes; breaker state is visible.
- **Graceful shutdown**: stop accepting intents, drain in-flight orders, persist, exit.
- **Restart safety**: no in-memory open position; executor reconciles open orders on boot
  from DB + chain, and closes/replays orphaned intents explicitly.
- **Clock**: all timestamps UTC, DB `timestamptz`; decisions store the observation time.

`executor` is the only service allowed to write `ledger_entries` for fills; engine writes
only intent/decision rows. Ledger accounts: `wallet:<id>`, `strategy:<id>`,
`fees:<chain>`, `pnl:realized`, `pnl:unrealized` (view only).

## 8. Configuration

One Pydantic settings schema, layered: defaults < `ezchain.toml` < env < workspace DB
overrides (audited). No module-level constants. Secrets are references (`vault://…`,
`env://…`), resolved only in the executor or the relevant adapter. The web app receives
a sanitized `/config/public` projection.

## 9. Observability

- OpenTelemetry traces across API → engine → executor → venue, with `intent_id` as the
  correlation key.
- Prometheus metrics: intent rate, decision latency, quote latency, fill ratio, slippage
  realised vs quoted, ledger drift gauge, queue depth, source freshness, breaker state.
- Structured JSON logs, redacted by key-pattern allowlist.
- Audit log table (append-only) for config, keys, wallets, manual orders, kill switch.
- Ops page in the product mirrors those signals; Grafana is optional.

## 10. Testing strategy

| Layer | Gate |
|---|---|
| Core | property tests: ledger balances, money rounding, risk monotonicity |
| Adapters | shared contract suite per chain/venue + recorded fixtures |
| Strategies | SDK conformance suite + sandbox escape tests |
| Engine | deterministic replay: same events ⇒ same decisions |
| API | OpenAPI schema diff, authz matrix, rate limits |
| UI | component tests + one Playwright smoke per page |
| System | compose smoke: boot, paper trade, kill switch, restore backup |
| Nightly | soak: 24 h ingest + 10k paper intents; invariant check |

A PR merges only when `tools/ci.sh` is green; the system smoke runs before release.

## 11. Extension points

1. **Chain adapter** — `ChainAdapter` (accounts, balances, transfer build, confirmation,
   fee, address validation, capability flags).
2. **Venue adapter** — `VenueAdapter` (quote, build swap, simulate, broadcast).
3. **Strategy plugin** — `Strategy.on_*` returning intents; manifest with params schema,
   required capabilities, chains, venues and data scopes.
4. **Automation action** — a typed action registered by the platform (notify, rebalance,
   pause strategy, DCA, close position) + a user-composable rule.
5. **Signal source** — emits `Signal` with provenance; can be routed to strategies.

All extension points are versioned, permissioned and loaded by the same plugin loader.
