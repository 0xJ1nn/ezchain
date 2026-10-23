# TODO — ezCHAIN P0 + M1 close-out

Status as of **2026-10-23**. The app is a **PAPER stack with production operations**:
continuous 22/25-chain market observation, per-chain quote ladders, an append-only paper
ledger, declarative and visual graph strategies, external Python strategy plugins in a
supervised worker, an MCP server for agents, backtests, strategy CRUD/scheduler,
migrations through `0007_strategy_plugins`, bearer auth, observability, a complete
operator surface and a full-code stress harness. There are no keys, no signing and no live
execution. Read [PRODUCTION-READINESS.md](PRODUCTION-READINESS.md).

Source of truth: [`project.md`](../project.md), [`AGENTS.md`](../AGENTS.md),
[`docs/CONTRACT.md`](CONTRACT.md), [`docs/PLAN-LIVE-PAPER.md`](PLAN-LIVE-PAPER.md),
[`docs/WIREFRAME.md`](WIREFRAME.md), [`ROADMAP.md`](../ROADMAP.md).

## Done

- [x] #6 governance: canonical `project.md`, ezCORE-aligned `AGENTS.md`, generated stubs.
- [x] #7 shell + dashboard (#16).
- [x] #15 Playwright render/screenshot harness.
- [x] #18 positions page + 9-zone tile in four states.
- [x] #21 markets screener with honest fixture states.
- [x] #22 token page + quote panel, Bonk and fail-closed SCAM states.
- [x] #23 mobile chart overflow; #24 markets evidence; #38 cross-page constants.
- [x] Evidence: `docs/evidence/m1-ui/` (dashboard, positions, markets, token, SCAM, mobile).

## Done — production operations (2026-10-22)

- [x] Market eyes: inline all-chain scan, overview/stored, 22/25 chains, coverage metadata.
- [x] Paper/strategy hardening: delete cascade, reconciliation, kill switch, honest
  `ORDER_REJECTED`, UTC timestamps, sell-side risk exemptions, pair-age gate.
- [x] Ops: Alembic 0001–0007 (fresh + legacy), bearer auth + CSRF proxy,
  `/healthz`/`/readyz`/`/metrics`, JSON logs, genesis/fill uniqueness.
- [x] Dockerfiles + runnable PAPER Compose/Caddy/Prometheus (build/up verified at head `0003`; not re-verified at `0006`).
- [x] Stress harness smoke/soak/idempotency/chaos + dedupe issue filing; #71 → #72 loop.
- [x] Dashboard fixture badges, DCA/dip vocabularies, Playwright build guard.
- [x] Docker `compose up --build` drill at head `0003` (2026-10-22); re-drill at `0006` is pending.
- [ ] Per-user auth/RBAC; `/metrics` exposure decision.
- [ ] Live dashboard equity history and live wallets (#57); EVM venue (#59).

## Done — strategy SDK v1 + agent surface (2026-10-23)

- [x] Apache-2.0 `ezchain-sdk`; manifest, params schema, context, intents, lifecycle.
- [x] Supervised plugin worker (timeout, rlimits, scrubbed env, quarantine, replay state).
- [x] Plugin catalogue/validation/reload API and `kind=plugin` strategies (0007).
- [x] MCP 2025-11-25 server (read tools/resources, paper-only writes, read-only default).
- [x] Server-derived execution mode and computed live-readiness endpoint.
- [ ] Plugin backtest harness; signed packages; bwrap/seccomp; per-user scoped tokens.

## Now — P0 live paper slice

- [x] P0.0 FastAPI, Postgres, package skeleton, Next proxy, Python gate.
- [x] P0.1 DexScreener multi-chain browsing + Jupiter quotes.
- [x] P0.2 Paper ledger/orders/fills/positions/PnL/risk.
- [x] P0.3 Momentum/dip/DCA/threshold evaluator + scheduler + run log.
- [x] P0.4 Live markets/token pages + paper ticket.
- [x] P0.5 Paper positions + strategy builder screens.
- [x] P0.6 integration: full gate, live Jupiter/paper smoke, restart persistence,
  API-down `UPSTREAM_UNREACHABLE`; evidence pack tracked in #60.
- [x] P0.7 dashboard KPI band reads the live paper account.
- [ ] P0.7b dashboard equity history and live open-position tiles (#57).
- [ ] Engine repository boundary (#58).
- [ ] EVM venue adapter to replace `paper_mark` (#59).

Known P0 limits: chains without a keyless quote provider use capped `paper_mark`; the
strategy *list* has no per-row PnL (per-strategy performance is available); no live
execution. See `docs/PLAN-LIVE-PAPER.md`.

## Now — M1

### 1. Wallets + wallet detail — #10 (surface done, backend open)

- `/wallets`: real paper-account truth and an explicit custody status; no dead "Add
  wallet" control. The wallet metadata/signing backend is the live-executor slice.
- `/wallets/[id]`: not built; no dead link points at it.
- No key material, no signing, no live balances.
- Remaining: wallet metadata registry, watch-only sync and the live custody flow.

### 2. Automations + builder — #11 (done as strategy scheduler)

- `/automations` shows the real strategy scheduler: status, interval, last run and links
  to each strategy. A generic trigger/action object is not part of this build.
- The graph builder is `/strategies/builder`; no separate automation builder is claimed.

### 3. Strategies + strategy detail + signals — #12 (done)

- `/strategies` and `/strategies/[id]`: real CRUD, graph builder, runs, pause/resume.
- `/signals`: live market movers with source and age; no safety or alpha claim.
- Plugin manifests/grants are now served by `/api/v1/plugins`; live promotion remains
  later work (the readiness endpoint reports evidence only).

### 4. Risk + alerts — #13 (done for PAPER)

- `/risk`: server limits, positions/marks, rejected attempts and the persisted kill switch.
- `/alerts`: server-derived reconciliation/mark/rejection/kill-switch alerts; no delivery
  channel exists yet and the page says so.
- Kill switch is reachable from the sidebar and dashboard.

### 5. Remaining system pages + component gallery — #14 (done)

- Every sidebar route is implemented; the placeholder catch-all is removed and unknown
  routes return 404. `/states`, `/mobile` and `/components` are real galleries.
- `/wallets` and `/security` state custody/live limits honestly rather than inventing a
  backend.

### 6. Remaining position-tile states — #8

- Already rendered: entering, pre-bank, riding, unsellable.
- Still needed: trail-armed, banked, laddered, dust, stale mark, closed, quarantined.
- One `PositionView`; never client-side PnL.
- Component: `apps/web/components/position-tile.tsx`; fixtures:
  `apps/web/lib/position-fixtures.ts`.

## Cross-cutting

- [ ] Responsive table → card conversion for markets/token/positions tables. Today the
      tables scroll horizontally at <760 px; the wireframe calls for cards
      (`design/wireframe/28-mobile.html`). The shared `ui.css` has `col-hide-sm`; a proper
      card primitive is still missing.
- [ ] Real filters/search/watchlist. Preview-only today; watchlists are #33.
- [ ] Position tiles/tables do not link anywhere yet; the dead `/positions/...`
      route is gone and dashboard rows link to `/tokens/{chain}/{address}`. Add a real
      token/detail link to the tile and table when their view models carry chain + token.
- [ ] #36 deterministic local gate: Playwright concurrency/port collisions and the
      `next build` cache need one clean run.
- [ ] #39 review the audit-drafted ADR-0014–0019 and `ROADMAP.md` before treating them as
      decisions of record. ADR-0015/0016/0018/0019 and the roadmap were not explicitly
      approved by the maintainer.
- [ ] Dark “Instrument” visual pass from `design/BRIEF.md`. M1 uses provisional light
      tokens; the visual pass is still pending.
- [ ] `SOL/USD` is repeated in fixture strings (`$147.62`). Move it to one fixture
      constant when the wallet/token fixtures are touched.

## Definition of done for any page PR

1. Page renders at 1440×900 and 390×844 with no external requests.
2. Unknown values are `—`, never `0`; real zeros stay visible.
3. No client-side PnL, quote or accounting math.
4. Fixture banner remains visible; no live-data claim without an API.
5. `bash tools/ci.sh` green; quote the exact result in the PR.
6. Rendered screenshot comparison committed under `docs/evidence/m1-ui/`.
7. Design/data deltas recorded in `design/OWNER-APPROVED-DELTAS.md`.
8. PR squashed; issue closed only when its acceptance is met.

## Commands

```bash
pnpm install
pnpm dev
bash tools/ci.sh
pnpm --filter @ezchain/web shots
```

Serve the wireframe separately: `npx serve design/wireframe -l 3001`.

## Do not

- Add live data, keys, telemetry, analytics or remote calls to the web app.
- Compute PnL or quotes in React.
- Make a failing fixture look live by removing the fixture label.
- Push to `main` directly; it is protected. Use a branch and PR.
