# TODO — ezCHAIN P0 + M1 close-out

Status as of **2026-10-22**. The app is a **live paper slice**: live multi-chain browsing,
Jupiter paper quotes on Solana, an append-only paper ledger and user-defined strategies.
There are no keys, no signing and no live execution. Wallet/automation/system pages remain
fixtures or placeholders.

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

Known P0 limits: non-Solana paper fills are capped `paper_mark`; no strategy PnL per row;
no live execution. See `docs/PLAN-LIVE-PAPER.md`.

## Now — M1

### 1. Wallets + wallet detail — #10 (next)

- `/wallets`: multi-wallet cards, custody tiers, per-chain balances, sync state, policy.
- `/wallets/[id]` or `/wallets/[id]` with wallet detail: balances, allowances, activity,
  policy, key custody, sweep.
- Fixtures only. No key material, no signing, no live balances.
- Wireframe: `design/wireframe/08-wallets.html`, `09-wallet-detail.html`.
- Evidence: `wallets-wireframe-vs-implementation.png`, `wallet-detail-*.png`, mobile.

### 2. Automations + builder — #11

- `/automations`, `/automations/builder`.
- List/status/last run/pause/log; trigger → conditions → actions; scope; dry run.
- Wireframe: `10-automations.html`, `11-automation-builder.html`.
- Dry run is labelled `SIMULATED`, never live.

### 3. Strategies + strategy detail + signals — #12

- `/strategies`, `/strategies/[id]`, `/signals`.
- Installed plugins only; manifest, params, grants, paper-gate evidence, intents, logs.
- No strategy code in-tree; no alpha or profitability promise.
- Wireframe: `12-strategies.html`, `13-strategy-detail.html`, `14-signals.html`.

### 4. Risk + alerts — #13

- `/risk`, `/alerts`.
- Exposure, limits, drawdown, breakers, kill-switch scope; alert rules/channels/quiet
  hours/delivery log.
- Kill switch reachable in ≤ 2 interactions from anywhere.
- Wireframe: `17-risk.html`, `15-alerts.html`.

### 5. Remaining system pages + component gallery — #14

- `/chains`, `/integrations`, `/settings`, `/security`, `/developer`, `/operations`,
  `/workspace`, `/states`, `/mobile`, `/components`.
- Reuse shell and primitives; no new design system.
- Wireframe: `20-` through `29-`.

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
- [ ] Position detail route. Tiles link to `/positions/...`, which currently renders the
      generic “not implemented” page. The real route is part of #8/#10 scope.
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
