# ROADMAP — ezCHAIN

Status: living document, updated when a milestone opens or closes. The roadmap is not
permission for uncontrolled development: each item becomes a focused issue before work
starts (`project.md` §30).

The current code is the **PAPER stack with production operations**: 22/25 chain market
observation, per-chain quote ladders, a paper ledger, declarative and visual graph
strategies, a backtester, migrations, auth, observability, a complete operator surface and
a full-code stress harness. Live execution, keys, wallet custody and a live UI are not
built; the unmerged live-executor foundation has a blocked review and is not a claim.

## Done — P0: Live paper slice

Goal: live multi-chain browsing, a real paper ledger, and user-defined strategies that
run in paper mode. No keys, no live money. Full contract:
[`docs/PLAN-LIVE-PAPER.md`](docs/PLAN-LIVE-PAPER.md).

- [x] P0.0 foundation: FastAPI, Postgres, adapter/core/engine packages, proxy, CI.
- [x] P0.1 live data: DexScreener multi-chain + Jupiter quotes.
- [x] P0.2 paper ledger: idempotent orders, fills, positions, PnL, risk.
- [x] P0.3 strategy engine: declarative momentum/dip/DCA/threshold rules.
- [x] P0.4 web live markets and token pages.
- [x] P0.5 web paper ticket and strategy builder.
- [x] P0.6 integration: full gate, live smoke, restart/API-down drills, docs.
- [ ] P0.7 follow-ups: dashboard equity history/tiles (#57), engine repository boundary
  (#58), EVM venue (#59), evidence pack (#60).

Exit: a user can browse live markets, place a paper buy/sell, see the ledger and position,
create a strategy and watch it place paper orders. Every number carries source, age and
`PAPER`; no key material exists.

## Done — production operations (paper)

- [x] Market eyes: `services/ingest`-equivalent inline scan, 22/25 chains, overview/stored.
- [x] All-chain browse fan-out/round-robin with coverage and 30-pair-cap regression tests.
- [x] Paper/strategy hardening: delete cascade, reconciliation, kill switch, honest
  `ORDER_REJECTED`, UTC timestamps, sell-side risk exemptions, pair-age gate.
- [x] Production ops: Alembic 0001–0003, genesis/fill uniqueness, bearer auth + CSRF
  proxy, `/healthz`/`/readyz`/`/metrics`, JSON logs, runtime-only image deps.
- [x] Runnable PAPER Compose stack and Caddy edge; Docker build/up **not verified** here.
- [x] Stress harness (smoke/soak/idempotency/chaos) with deduplicated issue filing;
  #71 detected and closed by #72.
- [x] Web truth: fixture badges, DCA/dip vocabulary, Playwright build guard.
- [ ] Docker build/`compose up` on a machine with a container runtime.
- [ ] Per-user auth/RBAC; one deployment token today.
- [ ] Dashboard equity history/attribution/risk/wallets remain fixture previews (#57).
- [ ] EVM/venue execution and executor/vault (out of paper scope).

## Current — M1: UI close-out

Finish the fixture-driven surfaces and make the gate trustworthy.

**Done:** shell/dashboard (#16), positions + four tile states (#18), markets (#21),
token + fail-closed SCAM (#22), render harness (#15), mobile overflow (#23), cross-page
constants (#38), evidence (#24), strategies/graph builder/signals (#12), risk/alerts
(#13), automations as the real scheduler (#11), every system page (#14), performance,
activity/ledger, reports, chains, operations and the gallery routes. The placeholder
catch-all is gone.

**Next:**

- Remaining position-tile states: trail-armed, banked, laddered, dust, stale, closed,
  quarantined (#8) — the live table and server `PositionView` are already real.
- Wallet metadata/custody and the live-executor hardening slice (blocked review).
- Watchlists and real discovery filters (#33).
- Dark "Instrument" visual pass from `design/BRIEF.md`.
- Make `tools/ci.sh` deterministic; the same commit must not pass and fail (#36).

**Known M1 gaps:** responsive tables still scroll instead of becoming cards; markets
filters/watchlist are preview-only (#33); position detail links to the generic placeholder;
the dark “Instrument” visual pass is still pending; ADR-0014–0019 and this roadmap need
maintainer review (#39).

Exit: `main` renders the full M1 surface at 1440 and 390 px with no external requests,
`bash tools/ci.sh` is green on five consecutive runs, and no screen implies live data.

## Next — M2: Data & ingest

One real, replayable data path before any execution.

- `services/ingest` with the `MarketEvent` contract, replay and backpressure (#25).
- Postgres/Timescale persistence and forward-only migrations (#26).
- Source freshness, provider health and circuit breakers that the existing alerts can
  observe (#27).
- One stable error/reason-code taxonomy across API, executor and UI (#28).
- Start with one chain and one source. Do not build the 20-chain matrix.

Exit: a fixture source and one live read-only source produce normalized, replayable
events with visible source and age; stale sources fail closed and alert.

## Later — M3: Execution & Jupiter

- Executor skeleton and the single `Intent → RiskDecision → Quote → Order → Fill →
  Journal` pipeline against a fake venue (#29).
- Jupiter adapter with a mandatory full-size sell quote, simulation and paper broadcaster
  (#30).
- Capped live pilot only after the paper gate.

Exit: one paper trade and one capped live trade reconcile to the receipt, with duplicate
intents producing one order and no key material outside the executor.

## Later — M4: Keys & paper/live

- Executor vault and the no-browser-key custody flow (#31).
- Paper gate enforcement, server-derived PAPER/LIVE, 2FA and kill switch (#32).

Exit: a live intent without a paper-gate record is denied; the mode badge cannot be
hardcoded; the kill switch is honored by the executor.

## Later — M5: Discovery & watchlists

- Watchlists as owned entities, with alerts and an optional automation scope (#33).
- Discovery/new-pairs feed with positive safety evidence (#34).

Exit: watchlist state survives restart; a token with unknown safety evidence is not shown
as tradeable; no copy-trading surface exists.

## Experimental — pump.fun firehose

A two-day spike decides whether near-native pump.fun data is viable (#35). Working
hypothesis: Yellowstone/Geyser gRPC decoding the pump.fun and PumpSwap programs; PumpPortal
new-token/migration events as discovery hints only. Live pump.fun trading is not on the
roadmap until the spike, a venue adapter, safety evidence and ADR-0009 are all in place.

## Explicitly out of scope for v1

Derivatives execution, fiat on-ramp, lending, copy-trading marketplace, native mobile
apps, managed hosting and subscription/payment billing. See `docs/PRODUCT.md` §5.
