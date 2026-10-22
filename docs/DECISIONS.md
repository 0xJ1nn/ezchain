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

---

## ADR-0020 — Non-Solana paper fills use a capped DexScreener mark

**Date:** 2026-10-22 · **Status:** Accepted for P0

**Context:** live browsing covers every DexScreener chain, but only Solana has a
keyless executable quote adapter in P0 (Jupiter). Building EVM aggregator adapters for
the first slice would delay the paper loop and add API-key dependencies. A paper fill
still needs a real observed price, not a synthetic one.

**Decision:** Solana paper orders use Jupiter quotes. Other chains use the best DexScreener
pair price as a `paper_mark`: it carries source, `observed_at` and age; new risk is capped
at $250 per **buy**; it is labelled `PAPER · MARK` in the UI; and `live_execute` is
`false`. A `paper_mark` sell is reduce-only and may exceed the entry cap so a position can
always be closed. A `paper_mark` is not an executable route and cannot unlock live mode.

**Consequences:** the paper loop works across every browsable chain on day one without
pretending the fill is executable. The entry cap sizes new risk but never traps an exit.
EVM execution needs a real venue adapter and an ADR before it can move from `paper_mark`
to a quote. Slippage/impact on `paper_mark` fills is unknown and must be shown as unknown,
never zero.

---

## ADR-0021 — P0 runs the strategy scheduler inline in the API

**Date:** 2026-10-22 · **Status:** Accepted for P0

**Context:** the architecture separates `services/engine` from `apps/api`. For the first
working paper slice, a separate deployable, queue and worker lifecycle would add failure
modes before a single strategy has run. The engine logic is pure and already isolated in
`ezchain_core`/`ezchain_engine`.

**Decision:** when `EZCHAIN_ENGINE_INLINE=true`, the API runs a bounded asyncio task every
`EZCHAIN_ENGINE_INTERVAL_SECONDS`; it evaluates active strategies and calls the same
`PaperService` used by the HTTP route. The task is disabled in tests and can be replaced
by a separate `services/engine` process without changing the evaluator or the order
service.

**Consequences:** one process to run locally. The API must keep the task bounded and
observable; if p95 latency regresses, split the worker. The inline path is not a second
execution pipeline — it uses the same service and ledger.

---

## ADR-0022 — Deleting a strategy also deletes its decision log

**Date:** 2026-10-22 · **Status:** Accepted for P0

**Context:** a strategy owns an append-only `strategy_runs` log: inputs, decision, reason
and the resulting order id. The first delete implementation removed the strategy row but
left its runs, which fails on the `strategy_runs.strategy_id → strategies.id` foreign key
in Postgres. Keeping the runs would require nullable attribution or an archival store; P0
has neither. CONTRACT I9 says any rule that gates money must be replayable from persisted
inputs, so removing a decision log is a real loss, not a cosmetic delete.

**Decision:** `DELETE /strategies/{id}` removes the strategy and all of its
`strategy_runs` rows in one transaction. `paper_orders.strategy_id` is not a foreign key:
it stays behind as an opaque historical string, so the execution trail (order, fill,
ledger) survives. Deletion intentionally removes the decision/reasoning log. Pause is the
retention path. The scheduler and manual run tolerate a strategy disappearing mid-run: the
run insert is dropped with a warning rather than failing the pass.

**Consequences:** the order and ledger audit trail remains complete, and a deleted id
cannot be confused with a live strategy. The deleted strategy's decisions **cannot be
replayed or audited** after deletion — the inputs and reason codes are gone. Anyone who
needs to investigate a strategy's behaviour must pause it instead of deleting it. A future
archive-before-delete feature needs its own ADR and storage decision.


---

## ADR-0023 — Visual graph strategies are declarative typed documents

**Date:** 2026-10-22 · **Status:** Accepted (maintainer instruction: "Unreal Engine
Blueprint-level strategy builder", 2026-10-22)

**Context:** P0 ships four fixed rule kinds (momentum, dip, DCA, threshold). Traders need
to compose their own logic — indicators, crosses, exits, cooldowns — without writing code
and without the platform shipping alpha. A node editor is the established way to do this
(Unreal Blueprints, Kryll, Composer), but a graph must not become a back door for code
execution, look-ahead bias or a second execution path.

**Decision:** add strategy kind `graph`. A graph is a typed JSON document
(`ezchain.graph/1`, [STRATEGY-GRAPH.md](STRATEGY-GRAPH.md)) validated against one node
catalogue in `ezchain_core.graph` and evaluated by one pure, deterministic evaluator that
the paper engine and the backtester share. Data is three-valued (unknown is never zero;
an unknown branch condition does nothing). Candles reach the graph only after they close.
Orders produced by a graph are intents that still pass risk → quote → order → fill →
journal. The UI renders the palette from the catalogue the API serves; it never redefines
node semantics.

**Consequences:** traders build and backtest their own strategies; the repository still
contains no strategy. Every graph run persists its trace so wires can show the real last
values and runs can be replayed. Adding a node type is a core change with tests, not a UI
change. Arbitrary code strategies stay in the plugin SDK, isolated per
[STRATEGY-SDK.md](STRATEGY-SDK.md).

---

## ADR-0024 — OHLCV history comes from GeckoTerminal, stored as closed bars

**Date:** 2026-10-22 · **Status:** Accepted

**Context:** indicators and backtests need bar history; the stack stores only the current
DexScreener snapshot per token. Recording our own ticks would take days before a
strategy could warm up and would have gaps whenever the API is down.

**Decision:** fetch per-pool OHLCV from the keyless GeckoTerminal public API (the most
liquid DexScreener pair, priced for our token), rate-limited and cached in a
`market_candles` table keyed by chain, token, timeframe and open time. Only closed bars are
served. Bars carry their source and fetch time; a stale or missing series is unknown and
fails closed. Chains GeckoTerminal does not index report `CANDLES_UNSUPPORTED_CHAIN`.

**Consequences:** strategies warm up immediately and backtests have months of history.
The candle source is a third-party index, not the chain; backtests name it in their
result. A future `services/ingest` (M2) can replace or cross-check the source behind the
same table.

---

## ADR-0025 — Every chain with a keyless quote source fills at a real executable quote

**Date:** 2026-10-22 · **Status:** Accepted (supersedes the blanket `paper_mark` of ADR-0020)

**Context:** ADR-0020 gave Solana a real Jupiter quote and every other chain a DexScreener
mark. A mark fill has no price impact, no route and no gas, so a paper PnL on Base or
Arbitrum was optimistic in a way no live order could reproduce. The 2026-10-22 probe found
keyless, keyless-forever quote APIs for 19 of the 25 registered chains; the remaining six
have no free quote source at all.

**Options:** (a) keep the mark everywhere except Solana, (b) implement an on-chain
`Quoter.quoteExactInputSingle` eth_call for the long tail, (c) route each chain to the best
keyless HTTP quote source and keep the mark only where none exists.

**Decision:** (c). The quote ladder lives on `ChainMeta.quote_providers` in
`packages/adapters` — `kyberswap → velora → lifi` on the EVM majors, `lifi` alone on
Mantle/Scroll/zkSync/Cronos/Celo/Metis, `cetus` on Sui, `stonfi` on TON, `osmosis_sqs` on
Osmosis, `jupiter` on Solana. Chains with no source (Blast, Fantom, Moonbeam, Aptos, Tron,
NEAR, Cosmos Hub) keep the capped `paper_mark` exactly as ADR-0020 defined it. The API asks
a `QuoteRouter`; no chain-name literal exists outside `packages/adapters`.

Rules that ride with it:

- A sell is quoted in the sell direction (`token → stable`, sized by quantity); a buy quote
  is never inverted.
- A buy is only valid when the received size also has a full-size sell quote from the same
  venue — an entry with no executable exit is not an entry (ADR-0015).
- Provider fees and gas are declared in `Quote.fee_usd`. The fill pays
  `max(paper_fee_bps, fee_usd)`, never their sum.
- A buy with a quoted impact above `EZCHAIN_MAX_PRICE_IMPACT_PCT` (default 5%) is rejected
  with `PRICE_IMPACT_TOO_HIGH`. A sell is never blocked by impact.
- When every rung fails, the order fails closed with `NO_ROUTE`. A chain with an executable
  ladder never silently degrades to a mark.
- `live_execute` stays `false` on every chain: these are quotes, not a signing path.

**Consequences:** paper fills now carry direction-specific size, impact, route and cost on
19 chains. The platform inherits five third-party availability surfaces; each provider has
its own token bucket, 10 s response cache and 60 s circuit breaker, and the ladder absorbs a
dead rung. LI.FI's free tier (75 requests / 2 h) cannot serve a hot loop, so it is only a
primary on the six chains with no alternative. Osmosis decimals come from a table because
GeckoTerminal does not index the network; an unlisted denom fails closed. Chains without a
free source stay marked, and the UI badge reads `paper_mark` for them — truthful, not
hopeful.

---

## ADR-0026 — Live execution through a separate executor with a local vault

**Date:** 2026-10-22 · **Status:** Accepted (maintainer instruction: "makes paper and real
trades flawlessly", 2026-10-22)

**Context:** the paper stack is production-ready; traders need the same pipeline to move
real funds. Keys are the highest-value asset (SECURITY.md §1) and four previous bots lost
trust by mixing keys, strategy code and accounting in one process.

**Decision:** implement [LIVE-EXECUTION.md](LIVE-EXECUTION.md): a separate
`services/executor` process is the only key holder (Argon2id + AES-256-GCM vault, keys
generated or CLI-imported, never exported, never typed into the browser); the API sends it
HMAC-authenticated intents; venue adapters build transactions (Jupiter on Solana,
KyberSwap on major EVM chains), every transaction is simulated before signing, fills come
from receipts and post to a separate live ledger. LIVE is armed by the owner with an L4
gesture and TOTP, expires, and is capped by conservative live limits; strategies need a
passed paper gate and an explicit promotion.

**Consequences:** new runtime dependencies with binary components (`solders`,
`eth-account`, `cryptography`, `argon2-cffi`, `pyotp`) are confined to the executor image
(and `pyotp`/`argon2-cffi` to the API for auth). Live chains are a capability subset; the
rest stay paper-only and say so. Real-fund testing beyond simulation and forked chains is
an owner action with a small funded wallet; automated tests never move real funds.
