# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- **PAPER Docker stack (issue #63)**: `apps/api/Dockerfile` and `apps/web/Dockerfile`
  build from the repository root as multi-stage, non-root images; `infra/compose.yaml`
  runs Postgres 16, a one-shot Alembic `migrate` job, the API, the web app and Caddy, with
  healthchecks, restart policies and resource budgets. Only Caddy publishes a port.
- Optional `observability` compose profile: Prometheus scrapes only the real
  `api:8000/metrics` endpoint; Grafana remains localhost-only and has no provisioned
  dashboards.
- `infra/compose.blueprint.yaml` records the future Redis/NATS/engine/executor/ingest/OTel
  services with their blocking reasons. They are profile-gated and not runnable.
- `Docker quick start` in `README.md`: required secrets, token generation, URLs, health
  endpoints and the internal-only OpenAPI UI.
- `docs/INFRA.md` now describes the actual PAPER topology, networks, migrations,
  health/metrics, backups and restore; `docs/RUNBOOK.md` adds migrate/upgrade/rollback,
  401, health and compose operations.
- **P0 live paper slice**: FastAPI edge, Postgres schema, `packages/core-py` domain and
  append-only ledger, DexScreener/Jupiter adapters, and an inline strategy scheduler.
- Live multi-chain market browsing with a 25-chain registry, source and `observed_at` on
  every row, bounded cache/rate limit and fail-closed normalization.
- Jupiter paper quotes with a mandatory full-size sell route; a `paper_mark` for
  non-Solana chains, capped and labelled as not executable.
- Idempotent paper orders, fills, positions, cash/realized/unrealized PnL and risk limits,
  all derived from append-only ledger entries.
- Continuous all-chain market observation ("market eyes"): a bounded inline scan discovers
  across every registered chain, persists observations, records `no_data`/`stale`/`error`
  instead of zero, and exposes `/api/v1/markets/overview` and `/api/v1/markets/stored`.
- All-chain market browsing now fans out one token lookup per registered chain (seeds first,
  at most 12 hints, four in flight) because `/latest/dex/tokens` caps each response at 30
  pairs. Rows are ranked within each chain and round-robined before the limit, so every
  data-bearing chain is represented instead of the global top few; the response carries
  optional per-chain `coverage` and `ordered_by`. Verified seeds were added for Blast,
  Mantle, Linea, Scroll and zkSync.
- User-defined declarative strategies (momentum, dip, DCA, threshold), pause/resume,
  run-now, interval gate and an append-only run log.
- Read-only paper reconciliation (`GET /api/v1/paper/reconciliation`): journal balance,
  cash replay, position sign, fill/order integrity and `quote_kind` counts, all derived
  from persisted rows.
- Persisted strategy-scoped operator kill switch (`/api/v1/system/kill-switch`); armed
  switches block strategy-originated orders, log a hold, and keep manual paper orders
  available.
- Live web surfaces: markets, token page with paper ticket, positions and strategies;
  `EZCHAIN_WEB_MODE=fixture` keeps Playwright deterministic and labelled.
- Python setup/dev scripts (`tools/setup-python.sh`, `tools/dev.sh`) and pytest coverage
  for adapters, paper accounting, strategies and quotes.
- `apps/web` M1 scaffold: Next.js shell, dashboard, positions page with four tile states,
  markets screener and token page with a fail-closed quote panel.
- Generated design tokens (`design/tokens.json` → `design/tokens.css` and
  `packages/ui/src/tokens.css`) and the `tools/build-tokens.mjs` gate.
- Playwright smoke/screenshot checks for dashboard, positions and markets at 1440 and
  390 px, plus token page desktop, mobile and fail-closed screenshots.
- Markets render evidence: wireframe-vs-implementation and mobile screenshots under
  `docs/evidence/m1-ui/`; token page evidence adds desktop, mobile and fail-closed shots.
- Project scaffold: product, architecture, domain model, chain adapter contract,
  strategy SDK, security, infra and PR workflow docs.
- 31-page low-fidelity wireframe in `design/wireframe/` with a manifest and gates.
- Performance screen: attribution, R-multiple distribution, benchmark and cost drag.
- Deployment blueprint: compose services, Caddy edge, Prometheus alerts, OTel collector.
- CI gates: agent stubs, design tokens, wireframe integrity, markdown links, secret scan,
  and the pnpm lint/typecheck/build/test runtime gate.

### Changed

- Caddy now sends `/api/*` to `web:3000` so the Next.js route handler injects the bearer
  token; `/healthz`, `/readyz` and `/metrics` go to `api:8000`, and everything else is the
  web app. The edge listens on `:8080` instead of matching `localhost`.
- `infra/.env.example` now matches the real app: psycopg URL, `EZCHAIN_ENVIRONMENT=prod`,
  `EZCHAIN_AUTO_CREATE_SCHEMA=false`, `EZCHAIN_AUTH_ENABLED=true`, `EZCHAIN_API_TOKEN`,
  container API URL and the inline engine/market flags. Future-only keys are commented.
- Prometheus scrapes only `api:8000` and itself. Alerts now reference metrics the API
  exposes (`ezchain_up`, `ezchain_http_requests_total`, `ezchain_paper_orders_total`);
  executor, queue, breaker, drift and backup alerts were removed.
- `README.md` and infra docs now describe the runnable PAPER stack instead of calling
  Compose a non-runnable blueprint.
- `README.md`, `ROADMAP.md`, `docs/ARCHITECTURE.md` and `docs/PLAN-LIVE-PAPER.md` now
  describe the P0 live paper slice; the web defaults to live and tests set `fixture`.
- `docs/CHAINS.md` now presents a **target** support matrix with a `Verified today`
  column; Solana is marked paper-verified, not live-verified.
- `docs/PRODUCT.md` labels the v1 scope table as a target, not shipped.

### Fixed

- Market sorting is None-safe for every `sort` mode: missing `volume`/`liquidity` stays
  `null` on the row, sorts last, and is never displayed as zero.
- Market coverage now distinguishes `error` from a successful empty `no_pairs`, and the
  overview totals no longer count a chain as both covered and errored. Chains the all-chain
  fan-out already attempted are not re-probed, keeping a successful monitor scan within its
  request budget.
- Chain staleness now includes the last scan duration, so a slow pass does not make every
  chain look stale before the next pass.
- The manual paper-order route no longer accepts strategy attribution; the order service
  also rejects strategy-attributed orders while the kill switch is armed, before quoting.
- Concurrent first boots no longer fail when both processes seed the kill-switch row; the
  loser rolls back and reads the winner.
- Reconciliation cash is checked against genesis plus persisted fill cash flow instead of
  a tautological replay; disarming the kill switch now requires a reason (422).
- Paper sells are no longer blocked by order-size, paper-mark-size or daily-loss limits;
  those limits gate new risk, not exits. Fail-closed price/liquidity/route/freshness gates
  still apply to sells (#65, PR #66).
- Position-tile charts no longer widen the page at 390 px (#23).
- Deleting a strategy now removes its `strategy_runs` rows and the strategy in one
  transaction instead of failing on the foreign key; historical orders keep their
  `strategy_id` string. Pause instead of deleting when the decision log must be retained.
- Concurrent run-now and scheduler evaluation of one strategy is resolved by the existing
  order idempotency key: at most one order and one fill per window.
- Dashboard bank activity and the position-tile peak now agree with the token fixture;
  a cross-page test guards the constants (#38).
- DexScreener token lookup now resolves the requested side (base or quote) and derives a
  quote-side USD price from `priceNative`, or fails closed.
- Paper sells are no longer denied by position-size limits; buy/sell journals balance, and
  no-route quote errors map to `NO_SELL_ROUTE` (409).
- API timestamps are normalized to UTC; paper positions without a mark stay unknown.

### Removed

- Decorative default-stack services that no code used: TimescaleDB (replaced by plain
  Postgres 16), Redis, NATS, the standalone engine, executor and ingest. They are either
  inline in the API today or not built; the intended future shape is in
  `infra/compose.blueprint.yaml`.
- `asyncpg`, bare `DATABASE_URL`, `EZCHAIN_ENV=local`, and the active NATS/Redis/vault
  keys from `infra/.env.example`. The only database URL is the psycopg one.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
