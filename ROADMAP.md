# ROADMAP — ezCHAIN

Status: living document, updated when a milestone opens or closes. The roadmap is not
permission for uncontrolled development: each item becomes a focused issue before work
starts (`project.md` §30).

The current code is a **fixture-driven web UI only**. Nothing in M2–M5 exists yet.

## Current — M1: UI close-out

Finish the fixture-driven surfaces and make the gate trustworthy.

- Finish the remaining position-tile states (#8).
- Strategies, automations, wallets, risk/alerts and system pages (#10–#14).
- Make `tools/ci.sh` deterministic; the same commit must not pass and fail (#36).
- Keep every fixture labelled and every unimplemented route explicit.

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
