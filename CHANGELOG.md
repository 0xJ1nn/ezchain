# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- **Paper account read models**: `GET /api/v1/paper/ledger`, `/performance`,
  `/attribution`, `/limits`, `/alerts` and `/export/{orders,fills,ledger}.csv` project the
  append-only ledger into UI-facing read models (cash, equity, realized/unrealized PnL,
  per-token attribution, risk limits, derived operator alerts and CSV exports). Every
  number is server-computed; an unknown mark is `null`, and no endpoint mutates state.
- `docs/evidence/p0-production/`: live market/paper/strategy/auth/API-down/restart and
  stress evidence for the PAPER production operations.
- `docs/PRODUCTION-READINESS.md`: verified vs unverified matrix, operator checklist and the
  release rule for the PAPER stack (398 Python + 36 Playwright tests, live auth-enabled
  smoke, stress suite, migrations).
- Stress harness bearer support via `EZCHAIN_STRESS_API_TOKEN`, with HTTPS/loopback
  enforcement and report redaction (#79).

- **PAPER Docker stack (issue #63)**: `apps/api/Dockerfile` and `apps/web/Dockerfile`
  build from the repository root as multi-stage, non-root images; the API image installs
  `requirements-runtime.txt` only (no pytest/ruff/mypy); `infra/compose.yaml`
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
- **Alembic baseline** (`0001_baseline`) for the current paper schema, generated against a
  fresh Postgres database. `ezchain-api migrate` / `python -m ezchain_api.migrate` runs
  `alembic upgrade head`; `/readyz` returns 503 unless the database is reachable and
  `alembic_version` equals the script head. A test rejects destructive `upgrade()` bodies
  (CONTRACT I8).
- **Mutation auth**: every non-`GET`/`HEAD`/`OPTIONS` request under `/api/` requires
  `Authorization: Bearer $EZCHAIN_API_TOKEN`; reads stay open. Production refuses to boot
  with an unsafe config (no token, short token, `auto_create_schema`, fixture mode, `*`
  CORS, default/passwordless database URL). The Next server proxies browser `/api/*` calls
  and injects the token server-side, so it never reaches the browser.
- **Observability**: one JSON access log line per request with a safe/echoed
  `X-Request-ID`, route template and duration (no tokens, bodies or query values);
  `/healthz` liveness, `/readyz` readiness, and a dependency-free Prometheus `/metrics`
  with bounded labels (`ezchain_http_requests_total`, request-duration histogram,
  paper-order/fill counters, `ezchain_up`).
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
- PAPER-only stress harness (`tools/stress/`): smoke, soak, idempotency and chaos modes,
  pure invariant checks for orders/fills/positions/account, latency budgets, JSON+Markdown
  evidence, and opt-in deduplicated GitHub issue filing by failure fingerprint.
- `tools/stress/run.sh` (venv-aware), `tools/stress/README.md` with cron/launchd examples,
  and pytest coverage for the pure checkers and issue dedupe.

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
- `README.md` quick start now runs the Alembic baseline and documents bearer auth;
  `CONTRIBUTING.md` no longer claims the Python services do not exist. `docs/INFRA.md`
  separates runnable local pieces from the unverified Docker blueprint, and
  `docs/RUNBOOK.md` documents migrate-on-deploy, legacy `stamp 0001_baseline` then upgrade, probe semantics and
  401 triage.
- `README.md`, `ROADMAP.md`, `docs/ARCHITECTURE.md` and `docs/PLAN-LIVE-PAPER.md` now
  describe the P0 live paper slice; the web defaults to live and tests set `fixture`.
- `docs/CHAINS.md` now presents a **target** support matrix with a `Verified today`
  column; Solana is marked paper-verified, not live-verified.
- `docs/PRODUCT.md` labels the v1 scope table as a target, not shipped.

### Fixed

- The market monitor now refreshes marks for open paper positions after each scan,
  including quote-side tokens that discovery would not store, bounded by
  `max_open_positions` (#88).
- Playwright fails fast with an actionable message when a `next dev` server is running
  on :3000, instead of corrupting the shared `apps/web/.next` manifest and producing a
  flaky 500. The `.next-test` approach was reverted because Next rewrites tracked type
  files for the active build directory (#36).
- Playwright uses a separate `.next-test` build directory, so a running `next dev` on
  :3000 cannot corrupt the gate server on :3100. The gate now stays green with a dev
  server active (#36).
- Market sorting is None-safe for every `sort` mode: missing `volume`/`liquidity` stays
  `null` on the row, sorts last, and is never displayed as zero.
- Market coverage now distinguishes `error` from a successful empty `no_pairs`, and the
  overview totals no longer count a chain as both covered and errored. Chains the all-chain
  fan-out already attempted are not re-probed, keeping a successful monitor scan within its
  request budget.
- Chain staleness now includes the last scan duration, so a slow pass does not make every
  chain look stale before the next pass.
- The documented pair-age gate is enforced for paper buys: `PAIR_AGE_UNKNOWN` when the
  source omits creation time and `PAIR_TOO_YOUNG` below `EZCHAIN_MIN_PAIR_AGE_MINUTES`
  (default 10). Sells stay exempt so an exit is never blocked (#67).
- Strategy runs now record `hold` / `ORDER_REJECTED` when the paper order service rejects
  an order, instead of claiming a buy or sell that never filled. The rejected order id and
  reason stay in the run inputs for diagnosis (#71).
- The manual paper-order route no longer accepts strategy attribution; the order service
  also rejects strategy-attributed orders while the kill switch is armed, before quoting.
- Concurrent first boots no longer fail when both processes seed the kill-switch row; the
  loser rolls back and reads the winner.
- Reconciliation cash is checked against genesis plus persisted fill cash flow instead of
  a tautological replay; disarming the kill switch now requires a reason (422).
- The dashboard labels every fixture-driven panel `FIXTURE` in live mode; the Risk panel
  no longer says "live checks" (#76).
- The web strategy builder's DCA and dip vocabularies now match the server validator:
  DCA no longer offers take-profit/stop-loss and dip no longer offers `exit_change_pct`.
  Creating either strategy no longer returns `STRATEGY_PARAM_UNKNOWN` (#73).
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
- Deleting a strategy now removes its run rows in the same transaction, so a strategy with
  history no longer fails with a 500 (found by the stress harness cleanup check).

### Removed

- Removed unused `paper_mark_fee_bps` and the dead `cents_to_usd`/`usd_to_cents`
  helpers. Money is documented as decimal USD strings and exact `NUMERIC` storage, not
  integer cents (#86).
- Decorative default-stack services that no code used: TimescaleDB (replaced by plain
  Postgres 16), Redis, NATS, the standalone engine, executor and ingest. They are either
  inline in the API today or not built; the intended future shape is in
  `infra/compose.blueprint.yaml`.
- `asyncpg`, bare `DATABASE_URL`, `EZCHAIN_ENV=local`, and the active NATS/Redis/vault
  keys from `infra/.env.example`. The only database URL is the psycopg one.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
