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

**Context:** the paper stack runs the full paper pipeline end to end; traders need the
same pipeline to move real funds. Keys are the highest-value asset (SECURITY.md §1) and
four previous bots lost trust by mixing keys, strategy code and accounting in one process.

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

---

## ADR-0027 — Paper fills serialize per account; stale sell sizes are rejected

**Date:** 2026-10-23 · **Status:** Accepted (concurrency hardening; no invariant change)

**Context:** the paper order service replayed the ledger, sized a sell and committed the
fill without a cross-request lock. Four concurrent full-size sells could each see the same
held quantity and fill it; two buys on different tokens could each pass the same cash check.
The stress harness reproduced both (`race.no_oversell`, negative cash), and a negative
position also made later reads fail. The quote itself is network I/O and must not hold a
database transaction open.

**Decision:** the quote happens outside a transaction. The replay, order claim, risk check,
fill and journal then run inside one **account-scoped** critical section: an in-process
lock plus, on PostgreSQL, a transaction-scoped `pg_advisory_xact_lock` on `paper:account`.
This is deliberately coarser than a per-token lock because cash, the open-position count and
daily realized PnL belong to the account, not the token. A sell whose quoted quantity is
larger than the quantity still held is rejected `POSITION_CHANGED`; it is never resized
against a stale quote. The rejected order persists the quote (`quote_kind`, `quote_json`) so
the refusal is replayable. The database must run at READ COMMITTED (the default): after the
advisory lock is granted, each statement sees the previous winner's committed rows.

The quote ladder also gets a total deadline of 8 s and a 5 s per-provider/exit attempt.
Exhausting the budget fails closed with `NO_ROUTE`/`TIMEOUT`; it never degrades to a mark or
returns an open connection. The paper-mark path is inside the same budget.

**Consequences:** paper orders are serialized across the whole account, so throughput is
bounded but every fill is consistent. A trader who races a changing position gets an
explicit `POSITION_CHANGED` and retries with a fresh quote. The in-process lock covers the
single-worker API and SQLite tests; the advisory lock covers multiple API processes. A
future per-account sharding scheme needs its own ADR and must keep cash and open positions
in the same serialization domain.

---

## ADR-0028 — Strategy plugins run in a supervised worker; agents use MCP

**Date:** 2026-10-23 · **Status:** Accepted (maintainer instruction: "strategies state of
the art, plug and play, Hermes/OpenClaw, ML packages", 2026-10-23)

**Context:** ADR-0004 and `docs/STRATEGY-SDK.md` promised external intent-only strategies,
but the code shipped only five declarative kinds. The platform already had the hard
parts — append-only ledger, one paper execution path, risk policy, reconciliation, paper
gate — and lacked the extension and delegation surface. Competitors now expose Python
strategy packages with ML dependencies (Freqtrade FreqAI, Jesse), and agent harnesses
(Hermes, OpenClaw) converge on MCP. Running arbitrary strategy code in the API process
would put keys, the ledger and every tenant in the same failure domain; not running it at
all would leave the SDK a document.

**Options:** (a) import plugins in-process; (b) run each plugin strategy in a supervised
subprocess; (c) a separate plugin service/container; (d) no code plugins, declarative
only.

**Decision:** (b), with a stable standalone SDK and an MCP edge.

- `packages/sdk-py` is Apache-2.0, imports no platform code, and defines the manifest,
  JSON-Schema subset, context, intent and lifecycle. A plugin declares
  `ezchain.plugin.json`, pins `ezchain-sdk>=1,<2`, and is discovered through the
  `ezchain.strategies` entry point or `EZCHAIN_PLUGIN_PATHS`.
- The engine runs one worker process per plugin strategy. The worker gets a scrubbed
  environment, memory/CPU rlimits, a per-callback wall timeout, stdout redirected away
  from the protocol, and crash quarantine (3 failures in 10 minutes). This is
  containment, not a sandbox; installs are trusted operator actions.
- A plugin returns at most one intent per tick. `ctx.state` is JSON, persisted with the
  run and replayed on the next tick. Reserved execution fields are refused, not ignored.
- The API exposes the catalogue, validation and plugin-strategy CRUD. `plugin_version` is
  pinned from the installed manifest at creation; a manifest upgrade that invalidates a
  persisted config fails closed.
- `apps/mcp` implements MCP 2025-11-25 over stdio. Reads are always available; mutation
  tools are absent unless `EZCHAIN_MCP_ALLOW_WRITES=1`, and every mutation still requires
  the API bearer token. There is no mode argument and no live order tool.
- Execution mode is server-derived. `EZCHAIN_EXECUTION_MODE=live` is refused at boot
  until the executor ships. Paper-to-live readiness is a computed, read-only report.

**Consequences:** plugins can use any package in their own environment (numpy,
scikit-learn, torch), while the host owns risk and accounting. Spawning a worker costs
tens of milliseconds per strategy start and the worker is restarted on a config change;
that is acceptable for minute-scale strategies and deliberately not a high-frequency
path. In-memory plugin state is not durable by design — only `ctx.state` is. Plugin
backtesting, signed packages, a bwrap/seccomp profile and per-user scoped tokens remain
open and are not claimed by this ADR. A future real sandbox or a separate plugin service
would need its own ADR and must keep the intent-only contract.

---

## ADR-0029 — Scoped agent keys; one deployment is one paper workspace

**Date:** 2026-10-23 · **Status:** Accepted (first-class Hermes/OpenClaw integration)

**Context:** The first MCP surface used the single deployment bearer token for every
agent. Hermes and OpenClaw can connect many skills/sessions to one server, and a hosted
operator may run an agent on behalf of several users. Handing every agent the admin token
violates least privilege; adding multi-tenant ledger partitioning at the same time would
rewrite the accounting model and delay a production integration.

**Options:** (a) keep one token; (b) scoped keys inside one workspace plus one workspace
per user for hosted multi-user; (c) full multi-tenant rows in every ledger/order table.

**Decision:** (b). `api_keys` stores a peppered SHA-256 token hash, a subject, scopes
(`read`, `trade`, `admin`), optional label/expiry, last-used and revocation timestamps.
The deployment token remains the admin/bootstrap credential. Middleware resolves a
bearer token to a principal and enforces the least scope per route; `read` covers reads
and dry-run/validate, `trade` covers paper orders/strategies/backtests, `admin` covers
key management, plugin reload and the kill switch. `EZCHAIN_REQUIRE_AUTH_FOR_READS=true`
closes the read surface for hosted deployments. A key names the actor inside one paper
workspace; it does not partition the ledger. Hosted multi-user means one workspace (or
one reverse-proxied route) per user, with one key per agent.

**Consequences:** agents can be provisioned and revoked without rotating the deployment
token, and a trade key cannot administer the platform. The admin token remains a shared
secret for the operator; production must keep it out of agent configs. Multi-tenant
isolation, per-key rate limits, IP allowlists and key rotation remain follow-ups. Any
change to ledger partitioning needs its own ADR and migration; this ADR deliberately
does not claim it.

---

## ADR-0030 — Position protection arms share the single paper execution path

**Date:** 2026-11-07 · **Status:** Accepted

**Context:** users want to protect an open position without writing a strategy:
stop-loss, trailing stop (price or percent) and take-profit (single or laddered),
plus reusable manual/smart profiles. The strategy surface already proves the shape —
declarative config, engine evaluation, one order path — but couples exits to strategy
kinds and offers no standalone arms, no trailing fairness and no per-chain presets.

**Options:** (a) new execution pipeline and dual ledger math for arm exits; (b) encode
protection as strategies; (c) small vertical slice — pure core evaluation plus an
engine runner that evaluates armed rows on scheduler ticks and fires the same
`OrderExecutor.place_order` a manual sell uses.

**Decision:** (c).

- `ezchain_core.protection` is pure: `validate_arm`, `evaluate_arm`,
  `resolve_profile`. Trailing peaks track only above-water prices, so a trailing stop
  that never went above water never triggers, however far the price then falls.
  Unknown or stale marks never trigger; expiry, cancel and missing positions are
  explicit holds, never zero.
- `services/engine/protection.py` owns the side effects: one market read and one
  ledger replay per arm per tick, then one sell intent with a stable idempotency key
  (`protection:<arm>:single|level-N`). A strategy close wins the position race first;
  an arm then sees `NO_POSITION`. Ladder levels stay armed until their last level
  fires. The scheduler runs the pass after strategies on the same tick, so a
  kill-switch outage skips both.
- `protection_arms` / `protection_profiles` hold only arm state (params, peak, ladder
  progress, expiry, ordering). Money still journals only through
  `paper.place_order` groups; profiles carry defaults (one home each in core) and,
  for smart profiles, the verbatim rule list. A per-chain profile overrides the global
  one by whole-profile selection (newest wins within a scope).
- Every transition appends to `protection_events` in the same transaction as the
  state change it describes: the rule snapshot, mark, `observed_at`, action and
  resulting order id. The trail is append-only at the ORM seam and has no HTTP
  mutation route; `seq` is monotonic within an arm, so newest-first reads do not
  depend on clock resolution. A hold that stays on the same reason is one row, not
  one per tick. Arms carry a `version`; an update or cancel that supplies an
  expected version is a conditional update and returns `409 PROTECTION_CONFLICT`
  when another writer won the race.

**Consequences:** arm closes reconcile with manual fills and the existing
reconciliation checks; no second bus, no dual math, no strategy code in-tree, and no
live-anything surface. Follow-ups: partial-ladder fraction caps assume the position
does not change size between levels (a resize updates the remaining value at the next
tick); expiry is checked at evaluation time, not by a sweep loop; replay reads the arm
row plus the append-only event trail, not an unbounded per-tick log.

---

## ADR-0031 — Reduce-only exits bypass entry-only risk gates

**Date:** 2026-11-10 · **Status:** Accepted

**Context:** the risk policy grew one gate at a time. Size, position, open-position,
daily-loss and price-impact limits were already sell-exempt, but quote staleness,
unknown price, unknown or thin liquidity and a missing pair still denied every sell.
A protection arm therefore inherited gates whose purpose is to size new risk: a token
DexScreener could not mark could not be exited through a venue that could sell it, so
the position was trapped by the very checks meant to protect it.

**Options:** (a) keep one policy and let every sell skip every data gate; (b) treat a
sell as an exit only when it is explicitly flagged or sized to the whole position, and
keep ordinary partial sells under the entry gates; (c) add a second execution path for
protection exits.

**Decision:** (b), inside the one `Intent → RiskDecision → Quote → Order → Fill →
Journal` path.

- `OrderRequest` carries `reduce_only` and an optional exact `quantity`. A reduce-only
  sell skips the entry gates: unknown or stale mark, unknown or thin liquidity, absent
  pair, order/position/open-position/daily-loss limits and price impact. A sell whose
  requested size clamps to the whole position is an exit by construction and gets the
  same treatment. An ordinary partial sell keeps the entry gates.
- The exit still fails closed on its own integrity: an open position, a quote whose
  source is fresh, a positive executable price with a positive amount, a route that
  returns a quantity, and a filled quantity no larger than the position. A stale quote
  is never waived.
- Sell sizing is quantity first. A caller that knows the position sends `quantity`; when
  it does not, a reduce-only sell with no usable mark asks the router for the whole
  held quantity instead of dividing by an absent price. A normal sell without a mark
  still fails closed with `PRICE_UNKNOWN`.
- The intent record on the order row carries `reduce_only`, and a sell with no route is
  persisted as `EXIT_NO_ROUTE` with the router's error code, message and detail. No
  fill happens without a route.

**Consequences:** a protection exit and a manual full close can always reach a venue
that can price them, and their refusals are replayable from the order row. Partial
ladder exits are exact because the arm passes the token quantity it already holds.
`reduce_only` on a buy is rejected at the API edge and ignored by the service, which
treats every buy as new risk. The policy still cannot verify the position bound itself;
that remains the order service's job, as it was before this decision.

---

## ADR-0032 — Paper wallet attribution lives on the ledger entry

**Date:** 2026-11-11 · **Status:** Accepted

**Context:** paper trading reported one cash figure for every chain, so a trader could not
see where settlement happened and no order was attributable to a chain's wallet. A wallet
that stored its own balance would become a second accountant next to the journal and
could drift from it. The first cut also read wallet cash with a SQL aggregate over the
cash account; SQLite stores money as TEXT, so `SUM()` coerced every row to REAL and the
wallet figures stopped adding up to the account cash around the 13th decimal place.

**Options:** (a) a `balance` column per wallet, updated on every fill; (b) a separate
wallet-cash projection table rebuilt by a background job; (c) attribute each cash ledger
entry to a wallet and derive every balance in the same Decimal replay that computes the
cash account, refusing an order whose chain cannot be attributed; (d) infer the wallet
from the order or the position instead of storing attribution on the journal.

**Decision:** (c), inside the existing ledger model.

- `ledger_entries.wallet_id` is nullable attribution on the cash leg only, added by
  migration `0015_paper_wallets` without rewriting existing rows. `NULL` means the row
  predates wallets or belongs to the genesis journal, and it settles in the genesis
  wallet (`paper-wallet:settlement`). The ledger account is still `cash:USD`; nothing
  about double entry changes.
- `paper_wallets` declares chain, settlement asset and label, unique per
  `(chain, asset)`. It stores identity, never money. Wallets are seeded at boot and on
  order placement; `GET /paper/wallets` only reads, so a GET cannot write.
- Settlement routing is an entry gate. A buy on a chain with no declared wallet is
  refused `WALLET_UNKNOWN` before any quote, market read, fill or journal row; a wallet
  that does not settle in the ledger asset is refused `SETTLEMENT_ASSET_UNKNOWN`. Both
  are structured risk reasons with the chain (and asset) in the detail, so the refusal
  is replayable from the order row. A sell is never refused for either reason: an exit
  is bounded by the held quantity, so its proceeds settle to the chain wallet when that
  wallet is valid and to the settlement wallet otherwise. Cash attributed to a wallet
  row that no longer exists is reported in a labelled `unallocated` read-model row with
  a null id, so the wallet list stays a complete partition of the cash account.
- Cash per wallet is `Replay.wallet_cash`, accumulated in the same single pass over the
  same loaded rows as `Replay.cash`, at an exact decimal precision. No SQL aggregate
  computes money read models. The buckets are therefore an exact partition of the cash
  account: `sum(wallet.balance_usd) == replay.cash`, and a property test asserts that
  after every step of a seeded random walk.
- `by_wallet` decomposes equity into each wallet's settled cash plus its chain's marked
  positions. A chain with positions but no wallet row, and cash attributed to a wallet id
  with no row, appear in an explicit `unallocated` row instead of vanishing from the
  total; an unpriced chain withholds `positions_usd` and `market_value_usd` rather than
  valuing them at zero.

**Consequences:** the wallets always reconcile with the journal, and deleting a wallet
row cannot orphan money — attribution lives on the append-only entry. Adding a paper
chain needs no migration, only its registry capability flag; the wallet appears on the
next boot or order. Per-wallet cash is attribution, not a spendable limit: risk still
checks the single paper cash account, so a negative chain wallet (fees and notional
settled before proceeds) is expected. Coverage of attribution is checked at read time by
the `unallocated` row rather than assumed. `WALLET_UNKNOWN` is a new rejection code
clients may observe on `POST /paper/orders`, alongside `SETTLEMENT_ASSET_UNKNOWN`.

## ADR-0033 — Provider credentials are operator-supplied and encrypted outside the ledger

**Status:** accepted 2026-11-10.

**Context:** the adapters are paced by a shared provider budget with no operator control, and a
deployment cannot carry its own provider credentials. An operator with a paid provider key has no
way to give it to the deployment except through configuration, which means a restart and a secret
in an environment file. The alternative — the platform shipping its own keys — puts the platform
between the operator's money and the venue, and makes one leaked key every deployment's problem.

**Decision:** credentials belong to the operator and the platform stores them encrypted.

- `POST /api/v1/provider-keys` accepts a key, stores it through the configured secret store (an
  `age` recipient or the OS keyring; there is no plaintext fallback), and returns only a
  fingerprint. No route returns the stored key, and no log line or error body carries it. If the
  store is unavailable, creation is refused rather than degraded to plaintext.
- Rows are workspace-scoped: every key carries the deployment's workspace id, so a database shared
  between two workspaces cannot mix their credentials.
- One active key per workspace and provider, enforced by a partial unique index on the active
  rows. Creating a second key requires revoking the first, so a revoked key can never silently
  become the active one again.
- Revocation is reference-preserving. A failed external delete keeps the stored reference and a
  pending-purge state; replaying the same revoke completes the purge of that reference instead of
  orphaning a secret behind a 404. A revoked key resolves for no new call; a call already holding
  a decrypted copy can still be in flight, which is stated in the contract rather than papered
  over.
- The provider budget bounds each caller's cumulative wait by the quote attempt's absolute
  deadline, so an exhausted budget returns the typed refusal before the route's own timeout can
  turn it into a route failure.
- A permanent decryption failure (a rotated key) is not retryable; a store outage is.

**Consequences:** the platform can carry an operator's keys without holding a plaintext copy, and
a revoked key cannot be resurrected by creating another. The deployment now depends on the secret
store being reachable at creation time, and on the operator revoking a key before adding its
replacement. Losing the encryption secret loses the stored keys — they are re-entered, not
recovered, which is the intended failure direction for credentials.

## ADR-0034 — The kill switch's global level refuses every new entry

**Status:** accepted 2026-11-11.

**Context:** the persisted operator switch was enforced only on orders that claimed
strategy attribution. The REST order route, the MCP server and the web ticket carry no
strategy id, so an armed switch left them free to open new risk; a drill filled two paper
buys while it was armed. The wireframe for the risk page already promised "rejects every
new entry · exits, stops and reduce-only sells still work", and the runbook's scope table
puts global above strategy.

**Options:** (a) make the strategy check unconditional in the order service, (b) add a
second flag for the global level, (c) treat the existing `active` flag as the global level
and keep the engine's strategy wrapper as the narrower second level.

**Decision:** (c). One persisted flag, two levels. The order service — the single path
every caller shares — refuses any **buy** while the flag is armed, before the market read
and the quote, with the new reason code `KILL_SWITCH_ARMED`. Risk-increasing means a buy:
a sell is bounded by the held quantity and cannot open a short, so ordinary exits,
reduce-only exits and stops still settle while the switch is armed. The engine's
`KillSwitchExecutor` stays as the strategy level and keeps its own `KILL_SWITCH_ACTIVE`
hold in the run log. `GET` and `POST /system/kill-switch` report `scope: "global"` (the
widest level in force) and `scopes: ["global", "strategy"]` (both levels the flag governs).

**Consequences:** a switch armed by an operator from now on stops entries from every
client, which is the behaviour the UI already described; clients that read the old
`scope: "strategy"` value must treat the widest level as armed. Two reason codes reach
observability, and they are distinct on purpose: `KILL_SWITCH_ARMED` is the refusal code on
the rejected order row, while `KILL_SWITCH_ACTIVE` stays on the engine's hold and the
`account_read` alert (`account_read.py:461`) because the run log and the stress readers
already key on that exact code; collapsing both into one string would break those readers.
A refused order still persists its rejected order row, so the attempt stays
replayable, but it writes no fill and no journal entry and never reaches a venue. The
stored `value.scope` of an existing row is ignored on read, so no migration is needed and
the next write records the new scope. The switch still never sells, closes or marks a
position.
## ADR-0035 — The execution mode is server-derived; live requires a signed gate record

**Status:** accepted 2026-11-13.

**Context:** the mode existed as a configuration literal, three read surfaces repeated their own
`paper` value, and `EZCHAIN_EXECUTION_MODE=live` failed with one sentence that never mentioned the
record an owner must produce. A client could not force a mode, but nothing proved it, and nothing
tied a live start to a specific build.

**Decision:** one function, `ezchain_api.mode.derive_execution_mode`, decides the mode from
settings only; no request field or header participates. `/healthz`, `/api/v1/agent/capabilities`,
`/api/v1/agent/state` and the strategy readiness document all report its result, so a probe and an
agent cannot disagree.

A live start requires a gate record signed with `EZCHAIN_LIVE_GATE_KEY` (HMAC-SHA256) and bound to
the deployed `EZCHAIN_BUILD_HASH`. The record is JSON with `schema_version`, `build_hash`,
`build_fingerprint`, `checklist_version`, `approver`, `approved_at` and `signature`; the signature
covers every other field in canonical form. `build_fingerprint` is the digest of the API version and
`EZCHAIN_BUILD_HASH`, so attesting it binds the record to the exact build that will serve it. The
record path is `EZCHAIN_LIVE_GATE_RECORD`. `Settings` verifies it during construction, so an
unsigned, tampered, wrong-build, wrong-fingerprint or placeholder-build start refuses before the
process serves. Every refusal names the record path and the field at fault, never a traceback.

**Consequences:** the key-holding executor is still unshipped, so a verified record currently ends
in a refusal that says so; enabling live is one constant in `mode.py`, and the gate stays in force.
Possession of `EZCHAIN_LIVE_GATE_KEY` is equivalent to approval, so it belongs with the owner and
rotating it invalidates every outstanding record — the intended fail-closed direction. The mode is
now safe to show on a public probe because a refused live start reports paper, not the requested
value, and the public reason omits the record's path (the boot refusal and the log keep it). A
signed record must be regenerated for each deployed build hash.

## ADR-0036 — A future-dated observation is rejected, and an unknown mark prices nothing

**Status:** accepted 2026-11-13.

**Context:** A market observation dated in the future reported `future` for any negative age,
so a far-future observation (skewed or malicious) kept a position's mark looking usable
indefinitely, priced equity while its status said `unknown`, and — once stored — vetoed every
later correct observation because its bogus timestamp outranked real ones.

**Decision:** The write side rejects an observation dated beyond a bounded clock-skew window
with `MARK_TIMESTAMP_FUTURE` and stores nothing. The receiver's clock is recorded separately
as `received_at`, so a row stored beyond the skew is a clock fault that never outranks an
accepted observation and a corrected write replaces it. On the read side a mark whose status
is `unknown` prices nothing: no mark value, no market value, no unrealized PnL, and the
account reports incomplete. The four-value vocabulary (`fresh`/`stale`/`unknown`/`future`)
is unchanged and the rule is computed once on the server in `_mark_status`.

## ADR-0037 — An order records the principal that placed it, decided on the server

**Status:** accepted 2026-11-13.

**Context:** the ledger could say what was bought and at what price but not who placed the
order. A person at the order ticket, a strategy firing from the engine and an agent key
calling the MCP tool all produced identical rows, so an incident review could not separate
an operator's manual trade from automation, and `paper_orders.strategy_id` only covered the
engine's own orders.

**Decision:** `paper_orders` and `ledger_entries` gain nullable `actor_kind` and `actor_id`.
`kind` is `human` (the deployment token, addressed by its subject), `agent` (a scoped key,
addressed by its key id) or `strategy` (the engine, addressed by the strategy id); a row
with no kind reads as `unknown`. The actor is resolved once, inside the single execution
path: `place_order` takes the API edge's resolved principal and derives strategy attribution
from `strategy_id`, which only the engine sets because the manual and MCP routes refuse the
field. It is stamped on the order and copied onto every leg of its journal group, so the
journal attributes itself without a join. `POST /paper/orders` refuses an `actor` field
outright, so a client cannot claim an identity, and an idempotency replay keeps the original
actor instead of adopting the replayer's.

A caller that cannot be named — the development process with no token, a direct service call
in a test, a row written before this revision — is `unknown`, never a plausible guess.
`actor_kind` is null only for those legacy rows; the read model maps it to `unknown`, and
nothing backfills it.

**Consequences:** migration 0019 is additive and forward-only; no existing row is rewritten
and no actor is invented for one. The agent key id, not its subject, is the address because a
key is revocable and reissuable: past orders keep naming the credential that placed them even
after the key is retired. The web ledger page does not render the actor yet — the API carries
it and the UI is a separate change (tracked follow-up). Because the value comes from
`request.state.principal`, a route that places orders without the auth middleware resolving
a principal records `unknown` rather than borrowing another field's value.

---

---

## ADR-0038 — A signature is bound to the approved intent by a decode before signing

**Status:** accepted 2026-11-13.

**Context:** The executor built and signed whatever the venue returned. Nothing proved the
bytes it signed were the order risk approved, so a compromised or buggy adapter could
substitute a mint, inflate the amount, redirect the recipient, call another program or
target another cluster, and the pipeline would broadcast it.

**Decision:** `ezchain_core.signing` owns an immutable `ApprovedIntent` (chain, cluster,
mint, side, amount, minimum output, recipient, program id, idempotency key) with a SHA-256
digest over those fields. The transaction adapter receives only that object and returns
bytes; it is never handed the order or the quote. The execution pipeline itself decodes the
bytes, compares every bound field with the approval and refuses with a typed
`TRANSACTION_*` code before any signer is called, so the binding holds whichever
`TransactionSigner` the boundary carries; the shipped signer repeats the check immediately
before touching the key ring (defence in depth). The signature that comes back is checked
against the pipeline's own decode: the approval it carries must be the approved one
(`TRANSACTION_APPROVAL_MISMATCH`), the bytes and payload digest must be the decoded ones,
and the payload must still reproduce the approval. The signed payload's digest is carried
on submission and the receipt must echo it; a missing or different digest is
`TRANSACTION_DIGEST_MISMATCH` and is never journaled.

**Consequences:** a mismatch costs a refused intent, never a broadcast, because no signer
is reached until the pipeline's own decode agrees and no signature is accepted unless it
covers those bytes and that approval. A substitute or buggy signer is therefore not a way
to disarm the binding. The fake venue is the signing boundary the tests drive; a real chain
adapter must deliver a decode of its chain-native message to the same payload, which is the
remaining work for live. Paper may run without a boundary and submits unsigned; a pipeline
whose mode is not paper refuses an unsigned submit with `SIGNING_BOUNDARY_REQUIRED` before
the venue is called.

---

## ADR-0039 — A stale mark never prices an entry or a valuation, but never traps an exit

**Status:** accepted 2026-11-13.

**Context:** The stale-source change made a mark-only chain (a `paper_mark`
rung with no executable provider) has no other price. Refusing the mark outright would trap
an open position: the position could not be sold, sized, or valued. At the same time, an
old mark must not price new risk or appear as equity.

**Decision:** Freshness is one per-class policy (`ezchain_core.freshness`) shared by the
order gate, the quote router and the alert tracker, and it speaks one vocabulary per
`MarkStatus`: past the budget is `stale` with `SNAPSHOT_STALE`/`QUOTE_STALE`; beyond the
clock skew is `unknown` with `MARK_TIMESTAMP_FUTURE`; a modest future is `future`. Every
snapshot producer passes the mark budget, so `TokenSnapshot.stale` is an age, not a
constant.

- An *entry* from a stale mark is refused with a persisted `SNAPSHOT_STALE` rejection row;
  a transient route/timeout failure still raises before the key is claimed, so a retry is a
  first order, not a replay of the refusal.
- A *reduce-only exit* sizes from the held quantity (`min(requested, held)`), never from the
  mark. On a chain with an executable venue the venue's fresh sell quote is the exit's
  price. On a `paper_mark`-only chain the exit settles at the last observed mark, because
  that is the chain's venue and the alternative is an unsellable position.
- `stale` and `unknown` never price: `mark_usd`, `market_value_usd`, `unrealized_usd` and
  `source` are `null`, and the account is incomplete. A sale settling at the last mark is a
  settlement fact, never a valuation.
- The transition alert is keyed `(chain, token, kind)`, drained into `GET /paper/alerts`
  through a bounded queue. The transition state is process-local: a restart re-raises once
  for a source that is still stale. A durable sink is a separate feature.

**Consequences:** a mark-only paper position can always be closed, entries and valuations
never use an old number, and the operator sees one alert per outage per process rather than
one per request. The stale settlement price is visible on the fill and in the order's quote
evidence, so nothing is hidden.

## ADR-0040 — Custody is the user's; the vault protects keys from everyone else

**Status:** accepted 2026-11-13. Amends the custody plan that named a hardware signer as
the only path; owner direction of the same day is the source.

**Context:** The custody line had been designed as an operator-only hardware signer: one
device, one key ring, live execution behind it. The product is user-facing, and users add
their own keys or link their own wallets. A design where only the operator's device can
sign cannot serve them, and one that forced a device approval on every order would impose
friction the owner explicitly rejected: automation, including full automation, is the
user's choice. What must not move is the engineering invariant — the platform keeps keys
safe from outsiders, and money still fails closed.

**Options:** (a) operator-held hardware signer only; (b) keys in the server's process
memory, gated by a per-order confirmation; (c) user-held keys in an encrypted vault the
server enforces, with a device as one custody option, and automation as a per-user setting.

**Decision:** (c). A key is added at runtime by the user who owns it and is addressed by an
opaque `KeyHandle` (id, owner, custody tier, chain, public address, public-key SHA-256
fingerprint). `ezchain_core.vault` owns the contract: a `Vault` signs one prepared
transaction for one owner's handle and returns a `VaultSignature`; there is no operation
that returns a key, a seed or a passphrase, and no field to store one. Custody tiers are
`local_vault` (key encrypted at rest under the user's credential), `hardware` (a device
approves each signature; one option, not the only one) and `watch` (no key; never signs).
`services/executor` implements the vendor-agnostic seam `HardwareSignerVault` over a
`SignerTransport` — USB device, local daemon on a unix socket, remote signer — so no vendor
SDK enters the tree and the device family is chosen without a dependency. Every non-answer
is one stable code (`VAULT_UNREACHABLE`, `VAULT_APPROVAL_DENIED`, `VAULT_UNKNOWN_HANDLE`,
`VAULT_LOCKED`, `VAULT_WATCH_ONLY`, `VAULT_SIGN_FAILED`); another user's handle refuses
exactly like an unknown one, so a caller cannot probe existence, and a signer that answers
for a different fingerprint is refused rather than trusted. Automation is a per-user
`AutomationSetting`: `manual` is the approval queue, and `full_auto` cannot be
constructed without a `RiskDisclosure` at the automation opt-in for that same user. The
execution path enforces it: an intent that names an automation runs unattended, so the
pipeline resolves that user's setting and refuses with `AUTOMATION_CONSENT_REQUIRED` when
there is none, when it belongs to another user, or when the tier still waits for per-order
approval — before any order exists. The second disclosure point, `key_add`, records what
custody of the added key means. The product tells the user what they are doing, once, and
then does what they asked.

The principal that a signature is authorised for is not a signer setting. It travels on the
order: `Intent.actor` (the actor attribution resolved server-side, never a request field) is
copied into `Order` and into the `ApprovedIntent`, and it is covered by the approval digest.
The pipeline compares the handle's owner with that principal before any signer runs, and the
signer refuses an approval naming another principal with `CUSTODY_OWNER_MISMATCH` before the
vault is called. `VaultTransactionSigner` therefore has no `owner` constructor field: an
executor cannot assert which user it is signing for.

**Consequences:** the key belongs to its user, so the platform's guarantee is against
outsiders — encryption at rest, the handle boundary, no key in any log, metric, error or
serialised object, no third-party access — and is not a paternalistic gate on the owner.
The ledger keeps a fingerprint per fill, never a key, so a fill names the key that signed
it without exposing it. The pipeline's live gate tightens rather than loosens: a non-paper
pipeline needs not only a signing boundary but a boundary whose signer carries a signable
per-user handle, and a bare in-memory signer refuses with `VAULT_BOUNDARY_REQUIRED` before
the venue. A `handle` that is present but not a `KeyHandle` is refused on the same code
instead of being dereferenced. The principal comparison is the isolation guarantee: a cross
user signature attempt refuses inside the pipeline with `CUSTODY_OWNER_MISMATCH`, not only at
the vault. The existing `SIGNING_BOUNDARY_REQUIRED` refusal still stands for a pipeline
with no boundary at all. Everything the executor already enforced is unchanged: one
ledger, one execution path, unknown data refuses, the kill switch gates entry only, and
the signed live gate record still gates a live start.

**Open, for the next vault tasks:** the cipher and KDF for the encrypted store and its
backup/restore path; the allow-list and per-key policy shape; the master-key source (the
operator's KMS or sealed file, and how a user's credential wraps their own key); key
rotation and revocation (a tracked follow-up) and the rotation's fingerprint history; the offline
known-answer vector and cluster guard (a tracked follow-up); the signing process's socket and per-start
secret (a tracked follow-up). Each is a design against this decision and needs owner review before live.

## ADR-0041 — Agent orders wait in a durable approval queue; automation is per user

**Date:** 2026-11-13 · **Status:** Accepted

**Context:** ADR-0037 made every order attributable, so an incident review can now tell an
agent's order from a person's. It also exposed the next hole: an agent's order filled as
soon as it was issued, with no human between the proposal and the fill. Attribution tells
you who acted; it does not let anyone stop them. The owner's product requirement is a
per-user choice — some users want every agent order approved, others deliberately run
agents unattended — so the control cannot be a single deployment-wide switch, and an
"unattended" mode must be an explicit, acknowledged opt-in rather than a silent default.

**Options:** (a) approve by holding the HTTP request open until a person answers;
(b) a second execution endpoint that fires an approved order; (c) a durable queue that
gates the one existing path; (d) a deployment-wide mandatory approval for every agent.

**Decision:** (c), with automation as a **per-user** setting. `automation_settings` holds
one row per user with tier `manual` or `full_auto`; a user with no row is `manual`, and
`full_auto` is only honoured while an append-only `automation_consents` row for that user
and the current disclosure version exists. Switching tiers is a person's act — the
deployment principal, recorded as `human` — and `full_auto` requires the risk
acknowledgement in the same request, so the consent and the setting are written together.

Under `manual`, an order attributed to an `agent` principal stops inside `place_order`
after risk has allowed it and before any fill: the order row is claimed by its idempotency
key, marked `pending_approval`, and an `order_approvals` row stores the intent, the
server's own risk evidence and the quote's cost sheet. Nothing is journaled. There is no
expiry timer process and no second write path: a read computes `expired` from
`expires_at` without writing, a decision transitions it, and each new proposal sweeps its
own expired rows. The queue is a gate *on* `place_order`, never beside it — approving
re-enters `place_order` with the stored intent and `approved=True`, so the approved order
runs `Intent → RiskDecision → Quote → Order → Fill → Journal` like any other and keeps its
original id and idempotency key. The decision claim (`approved`) is committed before the
attempt, so a second approver loses the row instead of filling twice; an attempt the gate
refuses ends the approval `failed` with the order's reason code.

**Consequences:** the queue is durable — a restart resumes it — and its state is one
declared lifecycle with a next action per state. Only a person may decide, enforced on the
principal kind rather than a scope, which is what makes an agent approving its own
proposal impossible. An approver reads the server's verdict, never agent-written prose. An
unreadable queue, an unreadable stored intent and an expired approval all refuse with a
reason code instead of placing an order. Migration `0021_approvals` is additive and forward-only:
`paper_orders.status` is already free text, so `pending_approval` needs no column change,
and no existing row is rewritten. The queue is a paper control, so it does not cover the
executor process's own venue path (services/executor), which is live-only and out of this
change's scope; when live execution reaches the approval surface it must gate there too.
The web queue screen is a follow-up: the API carries the queue and the tier, and no UI is
changed here.

**Amendment (review round 1):** the gate keys on the *automation*, not the actor label.
`resolve_actor` had mapped every `strategy_id` to `ACTOR_STRATEGY`, so an agent key with
`trade` could create a strategy and run it with no approval. Strategies now carry the
principal that created them (`created_by_kind`/`created_by_id`/`created_by_owner`, migration
`0022_strategy_creator`); an agent-created strategy is gated exactly like a direct agent
order, a person-created one is not, and unknown provenance fails closed. The tier and its
consent are bound to the ordering principal's owner — the authenticated subject, or the
strategy's creator — not to `workspace_id`, so one user's `full_auto` cannot authorize
another user's agent orders. A stored `full_auto` whose consent record is missing refuses
`AUTOMATION_CONSENT_REQUIRED` instead of being read as `manual`, and an unreadable queue
rolls its transaction back before the refusal is written.

---

## ADR-0042 — The built-in agent runtime is its own service that speaks only MCP

**Status:** accepted 2026-11-14. **Implementation note:** the minimal loop exists in
`services/agent-runtime` (model seam, MCP tool surface, six budgets, lifecycle table,
sourced answers); the stdio transport, the session trace store, the panel and the headless
command remain planned, not built.

**Amendment (review round 1, budgets).** "Per day" is per process, not per loop: the loop is
built per session, so the daily counters live in a process-scoped store keyed by
`(agent, UTC date)` (`budget.PROCESS_SPEND`) and every session in one day spends from one
budget. A multi-process runtime injects a persisted mapping through `DailyLedger(spend=...)`.
A turn is paid for after the pre-turn gate, so the budget is re-checked immediately after
consumption: reaching a limit exactly still delivers that turn's answer, crossing one stops
the session with the typed reason and discards the answer — a session that crossed a limit
never reports `COMPLETED`.

**Context:** the agent surface today is the MCP server in `apps/mcp`; the process that calls
it belongs to the user. The product's own agent is the next piece to build — one loop, a
model, the platform's tools, a panel and a headless command — and an owner may instead bring
an external agent over the Agent Client Protocol, a path that is planned and not built. All
of that is untrusted code driving money-adjacent tools: a model turns market text and tool
output into the next call, so its input is attacker-influenced by construction. Hosting that
loop inside the API or the engine, or letting it open the database, would put model output
next to the ledger, the keys and the admin surface. The other side of the boundary is
already decided: custody is the user's (ADR-0040), automation is a per-user
`manual`/`full_auto` choice (ADR-0041), and the platform has one execution path (invariant
I2).

**Options:** (a) host the loop inside `apps/api` or `services/engine`, beside the order
service and the database; (b) a separate `services/agent-runtime` whose only egress is a
child `ezchain-mcp` process holding a scoped `read`/`trade` key; (c) ship no built-in
runtime and support external harnesses only; (d) a separate runtime with a direct API client,
its own database reads and its own view of the ledger.

**Decision:** (b). The built-in runtime is its own service. It owns the loop, the model
clients, the budgets (turns, tokens and cost per session and per day) and its own session
trace; it owns nothing that moves money. Its only path to ezCHAIN is the MCP server over
stdio, with a scoped agent key minted for the session's owner. It has no REST client of its
own, no database connection, no message-bus client, and no shared process or filesystem with
the API, engine, executor or ingest. The external-agent path from option (c) remains
available through the same seam: an external agent is a different process implementing the
same contract, with the same tools and the same approvals, so the boundary is not
runtime-specific.

**What the runtime is.** On the platform side its capabilities are exactly the paths a
scoped agent key may drive: the `/api/v1/paper` order and approval routes, the strategy,
run and backtest routes, the agent read models, and behind them the engine's strategy host
and the executor's single `Intent → RiskDecision → Quote → Order → Fill → Journal` path. The
runtime adds no capability; it composes those paths through MCP. A route with no MCP tool
does not exist for it, and a new write tool widens the runtime's capability and is reviewed
as such. The runtime is not the engine, not a strategy host and not a key holder.

**Where agent code runs.** In the runtime, only the loop, the model clients, the budgets and
the trace. No strategy code runs there: a strategy is an external SDK package (invariant I5)
loaded by the engine's supervised worker, and the runtime can only ask the platform to
create, change or run a strategy record the platform validates. The model receives exactly
the MCP tools for the key's scope — no shell, URL fetch, filesystem, subprocess or generic
code execution. The runtime never loads a user-supplied executable into its own process.

**What an agent may and may not do.** The key is `read` plus `trade`, never `admin` and never
the deployment token.

| May | May not |
|---|---|
| Read state, markets, account, positions, orders, fills, ledger, reconciliation, strategies, plugins, backtests and readiness | Place a live order, choose an execution mode or reach the executor |
| Propose a paper order, create, change or delete a strategy record (`trade` scope; `ezchain_delete_strategy` is destructive and keeps order history), pause or resume a strategy, run a strategy or backtest | Approve or reject its own proposal, change the automation tier or supply the consent |
| Read the approval queue and its own tier | Arm or release the kill switch, reload plugins, mint or revoke keys |
| — | Read or write key material, the vault or a key handle |

Under `manual` (the default) an agent order is stored as `pending_approval` and nothing is
journaled; only a person decides, and an agent that tries gets `APPROVAL_REQUIRES_PERSON`
(ADR-0041). Under `full_auto` the user's recorded consent authorises the fill, and the
runtime still cannot turn the tier up. An armed kill switch refuses the next entry at
`place_order` (ADR-0034), so a session stops on the refusal; it cannot route around the
switch.

**The trust boundary.** The runtime never sees a wallet key, seed, passphrase, `KeyHandle` or
the vault. A paper order needs no signature; when the executor's live path exists, the vault
signs one prepared transaction for the owner's handle after the approval digest check
(ADR-0038, ADR-0040), and the runtime receives the order, fill and ledger outcome, never the
bytes or the key. There is no tool that signs or exports anything. An order placed through
the runtime is `agent`, addressed by the key id (ADR-0037); no route accepts an actor field,
so the runtime cannot claim an identity.

Model-provider credentials are the runtime's alone: read from its own environment at start,
never stored in the platform, the vault, the MCP server, a trace, a log line or the browser,
and a missing one stops the runtime at boot. Venue, chain and market-data credentials are the
platform's: they live in the operator's encrypted provider-key store (ADR-0033) or the
executor, and the runtime holds none. The one platform credential the runtime holds is the
scoped agent key — an API bearer token, not a signing key — and it never enters a prompt, a
tool argument, the trace or the model context.

**The audit trail.** The runtime will write one append-only trace per session in its own
store, never in the platform database: session, step, prompt hash, model id, tool, arguments,
result summary, latency, tokens, cost and approval id, redacted on write and replayable
against the recorded results. That store belongs to a service that is decided, not built: the
runtime-side audit log is planned, not built, and the platform holds no trace today. The
platform keeps the money record it already had: the order row and every journal leg carry
the agent principal (ADR-0037); `order_approvals` carries the server's own risk evidence and
`decided_by`; the executor's pipeline decodes the transaction bytes and compares every bound
field with the approved intent and its digest before any signer is called (ADR-0038).
**Attribution of switch changes — not yet implemented.** The kill-switch row stores only its
active flag, reason and update time, and `set_kill_switch` takes no principal, so who armed
it is a required future capability. The two records join on approval id, order id and the
acting key id, and neither substitutes for the other. Every money number an answer states is
read from a tool result, never computed by the model.

**Consequences:** a compromised runtime costs a read/trade key, not the platform. It cannot
write the database, hold admin or sign, and under `manual` it cannot turn a proposal into a
fill — there the person's decision is the authority, and under `full_auto` the user's prior
consent is, never the runtime's. Revocation is immediate and the kill switch still refuses
entries. The runtime is not a system of record: its trace explains a session, the ledger
explains the money. The MCP tool surface becomes the runtime's security boundary, so write
tools and their annotations are reviewed with that in mind and no admin tool is ever added.
The runtime must mint one key per session owner rather than share a key, and an unreachable
MCP server fails closed — there is no fallback direct-API path. The import boundary is
documented: `services/agent-runtime` must import no `ezchain_core`, `ezchain_api`,
`ezchain_engine` or `ezchain_executor`, and nothing else under `services/` may import it. Its
mechanical enforcement — an import-linter contract and service isolation — is planned work;
neither the service nor the lint configuration exists yet, so the boundary is held by review
until both land. Provider-credential rotation is the runtime operator's job, not the
platform's. A panel and a headless command are two entry points to the same loop and the
same approval path, and the BYO-agent adapter inherits this boundary rather than defining a
second one.

## ADR-0043 — One reason-code taxonomy, one home, checked both ways

**Status:** accepted 2026-11-14. Implements the vocabulary half of GH-28.

**Context:** A failure that can block or move money had a code, but the code lived wherever
it was raised. `SNAPSHOT_STALE` existed as a private constant in `freshness.py`, a literal in
`risk.py` and a dict key in `types.py`; `KILL_SWITCH_ACTIVE` had six independent definitions
across core, the executor, the engine and the API. `MISSING_RISK_INPUTS` was emitted by the
execution path and had no message at all, so it rendered as its own name. The web app
rendered codes it could not enumerate, and nothing failed when a new code appeared: the
documentation drifted silently, which is the failure mode the codebase already fixed once
for kill-switch scopes.

**Options:** (a) document the codes by hand and review the doc; (b) make each module own an
enum and generate the doc from a merge; (c) put the whole vocabulary in one module that
every layer imports, and let one contract test police it in both directions.

**Decision:** (c). `ezchain_core.reason_codes` holds one frozen `ReasonSpec` per code —
string, category, emitting layer, source module, operator summary, display message, HTTP
status, retryable flag, severity — and `REASON_CODES` is the registry. The older registries
are derived, not copied: `RISK_REASON_MESSAGES`, `VAULT_REASONS` and `BINDING_REASONS` are
built from it, so a message or a membership test has one source. `docs/REASON-CODES.md`
enumerates every code and is checked against the registry by `tests/test_reason_codes.py`,
which scans the AST for every shape this project emits a code through and fails when a code
is emitted without a row, when a row names a code nothing emits, when a row is missing from
the document, or when the document lists a code the registry does not define. The scan is
syntactic rather than a grep, so a renamed variable cannot hide a code and a comment cannot
invent one.

**Consequences:** a code string and its meaning are permanent; a message may be corrected
within a release, and callers compare on `code`. Two codes that look like duplicates stay
distinct where they name different facts: `KILL_SWITCH_ARMED` is the order-row refusal and
`KILL_SWITCH_ACTIVE` stays on the engine hold and the account alert, because the run log and
the stress readers already key on it; `NO_ROUTE`, `NO_SELL_ROUTE` and `EXIT_NO_ROUTE` name
the request, the token and the position respectively. `ExecutionStatus` and a reason code
are deliberately not merged: the stage says how far the pipeline got, the code says why it
stopped. Adding a code is additive and cheap (one row, one table row, one test run); reusing
or renaming one is now a test failure rather than a silent divergence. The refusal
behaviour itself is unchanged: this ADR moves where the vocabulary lives, not what any
refusal does.

**Open, tracked elsewhere:** the web kill-switch e2e test asserts `KILL_SWITCH_REASON_REQUIRED`,
which no Python code emits; either the route gains that code or the test is corrected, and
the taxonomy will not list a code nothing emits. A generated TypeScript table could replace
the web's string keys once a shared types package exists; today the web renders the API
message unchanged, which the registry now makes safe.
