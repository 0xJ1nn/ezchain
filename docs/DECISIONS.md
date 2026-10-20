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
