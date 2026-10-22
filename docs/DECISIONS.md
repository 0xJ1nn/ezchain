# DECISIONS — ADR log

Format: context → options → decision → consequences. Superseded ADRs keep their entry and
gain a `Superseded by` line.

---

## ADR-0001 — Modular monolith plus an isolated executor

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v2 and v3 documented 9 microservices and shipped one process; the boundary
was fiction. pumpbot's monolith was fast to iterate but had no module boundaries and
unsafe concurrency. ezCHAIN needs both iteration speed and a real security boundary.

**Options:** (a) microservices per domain, (b) one monolith, (c) modular monolith with
separately deployed executor and ingest.

**Decision:** (c). One `core` deployable contains api + engine with enforced package
boundaries; `executor` (keys) and `ingest` (I/O) are separate processes. Splitting further
requires a measured need and an ADR.

**Consequences:** simpler deploys, one DB, real key isolation. Domain boundaries must be
enforced by import-linter, not convention. Scaling is coarse until a split is justified.

---

## ADR-0002 — Postgres source of truth, transactional outbox, NATS

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v1 shipped Postgres/Redis that no code used. v2 used Postgres tables as an
event bus; v3 removed Redis and used in-process fanout. pumpbot used SQLite WAL and hit
"database is locked" and multi-GB WAL.

**Options:** (a) Postgres-only with polling, (b) Kafka, (c) NATS JetStream + outbox,
(d) Redis Streams.

**Decision:** Postgres for state, an outbox table written in the same transaction, a relay
to NATS JetStream for fan-out, Redis only for cache/rate limits.

**Consequences:** no lost events, replayable consumers, one durable store. Operationally
one more service; NATS is not a system of record.

---

## ADR-0003 — Append-only ledger, derived balances

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** pumpbot's best idea was `balance == SUM(wallet_rows)`. v2 cached balances and
wrote the ledger fire-and-forget; v1 accepted ghost reconciliation. Every bot had PnL
disagreements between screens.

**Decision:** append-only double-entry `ledger_entries`; balances and position sizes are
projections; fills and journal commit atomically; chain reconciliation writes an explicit
adjustment entry and blocks new intents.

**Consequences:** auditability and one PnL formula. Slightly more write amplification and
a projector to maintain; property tests are mandatory.

---

## ADR-0004 — Intent-based SDK, no strategies in-tree

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v3 compiled ~40 strategy lanes; pumpbot hard-wired ML as the only lane; every
bot mixed research code with runtime. Strategies were the source of most change and most
risk.

**Decision:** strategies are external plugins returning intents; the platform owns risk,
sizing, quoting and execution. No strategy code in the repo. Mandatory paper gate before
live.

**Consequences:** the platform can be stable while strategies iterate. The SDK becomes a
public compatibility surface; sandboxing is required.

---

## ADR-0005 — Capability-based chain adapters

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** all four bots were Solana-only with chain logic spread across the engine.
"All chains" cannot mean a chain-name switch.

**Decision:** `ChainAdapter` + `Capabilities`; core branches only on capabilities; a chain
is a package plus a registry row; a shared contract suite gates every adapter.

**Consequences:** slow first adapter, cheap subsequent ones. Some chains need capability
extensions (rent, object model) — those change the interface by ADR, not by conditionals.

---

## ADR-0006 — One execution pipeline including paper

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v1 double-counted fees because paper was a second formula; v2 had dual PnL
math; pumpbot's paper mirror is why its measurements were credible.

**Decision:** `Intent → RiskDecision → Quote → Order → Fill → Journal` for every mode;
only the venue adapter and custodian differ. Paper rejects when a live quote would reject.

**Consequences:** honest paper results, one test surface. Paper throughput is limited by
real quote latency; that is intentional.

---

## ADR-0007 — Forward-only, non-destructive migrations

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v2 had ~30 destructive "clean_slate" migrations; v3 needed an out-of-repo
shim to build a fresh DB; pumpbot used silent `ALTER TABLE` in `try/except`.

**Decision:** Alembic, forward-only, CI creates a DB from scratch on every PR. Data resets
are operator actions, audited. Wipe patterns fail the migration lint.

**Consequences:** schema history is reproducible and safe. Test data cleanup must use
scoped fixtures, not truncation of production tables.

---

## ADR-0008 — Wireframe first, tokens downstream

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** v2 produced 22 visual mockups, shipped one, and documented no decisions;
tile-lab proved that structure (not skin) determines usability.

**Decision:** the grayscale wireframe is the structural source of truth; visual design is
a downstream pass driven by `design/BRIEF.md`; deviations are recorded.

**Consequences:** design can start before implementation and without re-litigating layout.
Two artifacts to keep in sync; the manifest + link checker enforce it.

---

## ADR-0009 — Paper gate before live

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** every bot traded unvalidated strategies; v2 and v3 published negative-EV
results only after weeks of live-like paper. No platform enforced a gate.

**Decision:** ≥14 days, ≥100 intents, ≥20 fills, no invariant breach, same-window backtest
with declared costs, owner approval. Gate result is stored per strategy version and shown.

**Consequences:** slower promotion, far fewer incidents. A profitability promise is still
not made; the gate is about process and safety.

---

## ADR-0010 — License split: AGPL app, Apache SDK

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** ezcore is GPL-3.0; klikey has no license. A hosted trading app should keep
improvements open, while strategy authors need a permissive SDK.

**Decision:** app and adapters AGPL-3.0-or-later; `packages/sdk-py` and docs Apache-2.0;
trademarks separate (`TRADEMARKS.md`).

**Consequences:** network-use copyleft for the platform; strategies can be proprietary.
Contributors grant inbound=outbound + relicensing in CONTRIBUTING.

---

## ADR-0011 — Money as integers, decimals at the boundary

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** pumpbot mixed units and produced phantom prices; v3 had a 1000× lamport bug;
v2 used floats in places.

**Decision:** `Money` = integer minor units; `TokenAmount` = raw integer + decimals;
`Price` = Decimal with declared quote; events declare units; adapters quantize with a
recorded reason.

**Consequences:** exact accounting. More type ceremony; serialization must keep raw
strings, not JSON numbers.

---

## ADR-0012 — One view model for positions

**Date:** 2026-10-20 · **Status:** Accepted

**Context:** three tile implementations with different math; economic vs cash return
confusion; per-page recomputation.

**Decision:** server exposes `PositionView`; clients format only; tile spec in
`docs/WIREFRAME.md` is normative; no PnL arithmetic in the UI.

**Consequences:** consistency and testability. Any new field requires an API + tile +
docs change in one PR.

---

## ADR-0013 — M1 web UI uses CSS custom properties, not Tailwind

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** `docs/ARCHITECTURE.md` named Tailwind and TanStack Query for `apps/web`.
The M1 scaffold has no API to query, and the wireframe already ships a complete CSS
vocabulary. Adding Tailwind now would create two styling systems and a second set of
defaults before the visual pass.

**Decision:** `apps/web` uses Next.js 15 App Router, TypeScript, CSS custom properties
generated from `design/tokens.json`, and plain global CSS derived from the wireframe.
TanStack Query is deferred until `apps/api` exists. Tailwind may be adopted by a later
ADR if the visual pass needs it; it is not a dependency today.

**Consequences:** one styling source of truth, a small dependency surface, and a
build that works offline. Revisit when the API and data-fetching layer land; do not add
a second styling system silently.

---

## ADR-0014 — "Verified" means an adapter contract suite passed, not a plan

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** `docs/CHAINS.md` listed six chains as T1 (live-mainnet verified) and fourteen
more as T2/T3 while no adapter package, ingest service or executor existed. The M1 audit
found zero implemented chains and zero network calls in the web app. The matrix was a
target written as a status.

**Options:** (a) keep the matrix as a target and trust readers to know it, (b) delete the
matrix until adapters exist, (c) keep it but add an explicit verified-today state.

**Decision:** (c). `docs/CHAINS.md` is a **target** support matrix with a
`Verified today` column; every row starts at `—`. A tier may only be promoted when the
adapter's contract suite passes against live mainnet and recorded fixtures (§5).
`docs/PRODUCT.md` labels its scope table as a target, not shipped.

**Consequences:** the docs no longer claim capabilities the code lacks. Adapter PRs must
carry the contract-suite evidence that changes the matrix; a docs-only promotion is
rejected.

---

## ADR-0015 — Jupiter first, paper-first, one execution path

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** Solana is the declared first-class chain and Jupiter routes across Raydium
and PumpSwap. Building three AMM integrations before the pipeline exists would multiply
surface area before the first order is proven.

**Options:** (a) direct Raydium/PumpSwap pool adapters first, (b) Jupiter aggregator
first, (c) all venues in parallel.

**Decision:** (b). `venue_jupiter` implements `quote`, `build_swap`, `simulate`, `submit`
and `status`. A buy quote is rejected unless a full-size sell quote exists. Paper mode
uses the same adapter with a simulated broadcaster. Live trading follows a capped pilot
after the paper gate. Direct AMM adapters are later work.

**Consequences:** one adapter exercises the venue contract and the `NO_SELL_ROUTE` path.
Aggregator availability, rate limits, route manipulation and quote staleness become
money-path risks and must fail closed. No third-party execution API sits in the money
path.

---

## ADR-0016 — No browser key entry; watch-only by default

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** the onboarding and wallet wireframes disagreed: one refused seed phrases and
private keys, the other allowed importing an existing key from Security → Vault. No key
handling exists yet, so the contradiction can be fixed before code.

**Options:** (a) browser key entry with client-side encryption, (b) executor-local
creation/import only, (c) watch-only only, no hot wallet.

**Decision:** (b). The web UI accepts public addresses and xpubs for watch-only wallets.
Hot-wallet creation/import happens through an executor-local CLI or OS-keystore-backed
flow; key material never enters the browser, API responses, fixtures, logs or traces.
The executor is the only key holder (CONTRACT I4). Compose must not pass the vault key to
`web`, `ingest` or `engine`.

**Consequences:** safer custody and a smaller web attack surface. Onboarding needs a
local step; hosted/remote setups need a documented operator flow. Hardware wallets remain
the preferred live signer.

---

## ADR-0017 — pump.fun data comes from the chain, not a frontend API

**Date:** 2026-10-22 · **Status:** Proposed (pending spike #35)

**Context:** near-native pump.fun trading needs sub-second launch, trade and graduation
events. Options include Yellowstone/Geyser gRPC, PumpPortal WebSocket, pump.fun frontend
APIs, raw shreds and Jito ShredStream. The product is explicitly "not a high-frequency
colo stack" (`docs/PRODUCT.md`).

**Options:** (a) Geyser gRPC decoding the pump.fun and PumpSwap programs, (b) PumpPortal
WebSocket, (c) pump.fun frontend API scraping, (d) raw shreds.

**Decision:** (a) is the primary candidate: replayable, ToS-clean, and compatible with
the existing Helius/Triton posture. (b) is a fallback for `subscribeNewToken` and
`subscribeMigration` as **discovery hints only**, cross-checked against gRPC; never for
marks, fills or execution. (c) is rejected on ToS grounds. (d) is rejected as
inconsistent with the stated product positioning and cost. Jito ShredStream is treated
as discontinued per vendor documentation (2026-09-05). Live pump.fun trading is a later
milestone gated on a venue adapter, positive safety evidence and ADR-0009.

**Consequences:** ingest cost and decoding work land in M2; execution stays on the
`VenueAdapter` path. The spike in #35 must return measured numbers before this ADR moves
to Accepted.

---

## ADR-0018 — Watchlists are first-class, not copy-trading

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** the wireframe shows watchlist toggles and counts, but there is no domain
entity or API. "Watch to buy" was requested as a product behaviour. Copy-trading is
explicitly out of v1 scope.

**Decision:** add `Watchlist` / `WatchlistItem` as owned entities with persistence and an
API. Alerts may scope to a watchlist item, and automations gain an optional `watchlist`
scope. Adding a token to a watchlist never places an order; it only enables observation
and user-configured alerts/automations. Wallet tracking remains a data source, not a
copy-trade product.

**Consequences:** markets and token pages need real read models; the M1 "not wired"
preview is removed in M5. No social/copy-trade surface is implied.

---

## ADR-0019 — Stable reason codes and fail-closed UI states

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** the wireframe names `MARK_STALE`, `NO_SELL_ROUTE`, `EXECUTOR_DOWN`,
`RISK_DENIED`, `QUOTE_EXPIRED` and others, but there is no schema, constant set or UI
mapping. The web app has no `error.tsx`, and the catch-all returns 200 for arbitrary
nested paths.

**Decision:** every failure that can block or move money has one stable reason code with
an HTTP/WS mapping, a retryable flag, a severity, a user message and operator detail.
Codes live in one contract package; API, executor and UI import the same source. Unknown
failures render as `UNKNOWN` and fail closed. Unimplemented nested routes return 404, not
a 200 placeholder.

**Consequences:** one vocabulary across services and screens; contract tests assert every
code renders. New failure modes require a code, not an ad-hoc string.
