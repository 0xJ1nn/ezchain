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
