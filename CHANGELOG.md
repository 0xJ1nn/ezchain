# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). SemVer.

## [Unreleased]

### Added

- **P0 live paper slice**: FastAPI edge, Postgres schema, `packages/core-py` domain and
  append-only ledger, DexScreener/Jupiter adapters, and an inline strategy scheduler.
- Live multi-chain market browsing with a 25-chain registry, source and `observed_at` on
  every row, bounded cache/rate limit and fail-closed normalization.
- Jupiter paper quotes with a mandatory full-size sell route; a `paper_mark` for
  non-Solana chains, capped and labelled as not executable.
- Idempotent paper orders, fills, positions, cash/realized/unrealized PnL and risk limits,
  all derived from append-only ledger entries.
- User-defined declarative strategies (momentum, dip, DCA, threshold), pause/resume,
  run-now, interval gate and an append-only run log.
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

- `README.md`, `ROADMAP.md`, `docs/ARCHITECTURE.md` and `docs/PLAN-LIVE-PAPER.md` now
  describe the P0 live paper slice; the web defaults to live and tests set `fixture`.
- `docs/CHAINS.md` now presents a **target** support matrix with a `Verified today`
  column; Solana is marked paper-verified, not live-verified.
- `docs/PRODUCT.md` labels the v1 scope table as a target, not shipped.

### Fixed

- Position-tile charts no longer widen the page at 390 px (#23).
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

- Nothing yet.

## [0.0.0] — 2026-10-20

Initial design drop. No runtime code.
