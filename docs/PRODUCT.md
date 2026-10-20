# PRODUCT — ezCHAIN

## 1. What it is

A self-hostable, open-source crypto trading workstation. One place for:

- **Portfolio** across every chain and every wallet (hot, hardware, watch-only, exchange).
- **Execution** through one pipeline, paper → live, with exact quotes and honest fees.
- **Automation** with user-defined rules; strategies are plugins the user installs.
- **Research** with a real ledger, replayable backtests, signals and attribution.
- **Operations** that a single person can run: one compose file, one dashboard, one log.

## 2. What it is not

- Not a strategy. No bundled alpha, no "profitable bot", no signals marketplace with
  manufactured returns.
- Not a custodian. Keys stay on the user's machine; ezCHAIN never holds funds.
- Not a copy-trading funnel. Wallet tracking is a data source, not the product.
- Not a chain-specific bot. Solana is an adapter, not the architecture.
- Not a high-frequency colo stack. Target: human-scale strategies, 1s–1h horizons,
  optional sub-second execution per venue.

## 3. Users

| User | Needs | Success |
|---|---|---|
| **Solo trader** (primary) | one screen for positions, wallets, risk; add own strategy without forking | runs a strategy in paper, promotes to live with confidence |
| **Strategy author** | stable SDK, replayable data, backtest, no platform internals | publishes a plugin; users install it with clear permissions |
| **Self-host operator** | boring deploy, backups, upgrades, observability | one-node install, restore drill passes |
| **Team** (later) | shared workspace, roles, audit | trader/viewer/owner separation |

## 4. Product principles

1. **Show the truth, including the ugly state.** `NO SELL ROUTE`, stale marks, paper
   badges, unsynced wallets. No fake zeros, no optimistic fills.
2. **Every surface answers a question.** Dashboard: *am I safe and up?* Positions: *what
   is happening now?* Wallet: *where is my money?* Automation: *what will happen next?*
3. **Progressive depth.** One-glance tile → drawer → full page → raw event. Never the
   reverse.
4. **User control over risk.** Global kill switch visible on every page; per-chain,
   per-wallet, per-strategy limits one click away.
5. **No lock-in.** CSV/Parquet export, open API, plugin SDK, standard Postgres.

## 5. Scope for v1 (M0–M3)

| Area | v1 scope |
|---|---|
| Chains | Solana, Ethereum, Base, Arbitrum, Polygon, BSC (T1); Sui, Aptos, TON, Bitcoin watch-only (T2) |
| Venues | Jupiter, Uniswap v3/v4, 1inch, PancakeSwap, CEX read-only (Binance/Coinbase/Bybit) |
| Wallets | local encrypted hot wallet, watch-only, hardware (Ledger), per-wallet policies |
| Positions | spot + perp-read (CEX), one tile model, manual close/write-off |
| Automations | trigger→condition→action rules, schedules, DCA, trailing exits, risk guards |
| Strategies | SDK, local install, paper gate, per-strategy params and limits |
| Signals | on-chain, price, wallet, social; feed + routing rules |
| Research | backtests with fees/slippage/fill model, replay, comparison |
| Ops | single-node compose, Postgres backups, metrics, audit log |

Out of scope for v1: derivatives execution, fiat on-ramp, lending, copy-trading
marketplace, mobile native apps (responsive web first), managed hosting.

## 6. Success metrics

- **Time to first paper trade** < 10 minutes from `docker compose up`.
- **Ledger drift** = 0 in the invariant test suite and in production reconciliation.
- **Strategy iteration loop** < 5 minutes (edit → backtest → paper).
- **Recovery**: restore a node from backup and reconcile to the last ledger entry.
- **Support load**: every failure explains itself in the UI with a reason code.

## 7. Non-negotiable UX promises

- A position's PnL is identical on dashboard, tile, detail and export.
- Every number has a source and an age on hover.
- Kill switch reachable in ≤ 2 interactions from anywhere.
- Paper/live mode is always visible, never inferred.
- Destructive actions (write-off, delete wallet, revoke key) require an armed confirm.
