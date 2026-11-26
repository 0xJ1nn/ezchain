# ROADMAP — ezCHAIN

> **ALPHA — PAPER ONLY.** Not ready for real-money trading. Official agent listings and
> any live-mode claim wait for real-mode readiness to be measured; today's Hermes and
> OpenClaw packs are local integrations, not official marketplace listings.

## What exists today

The current code is the PAPER stack with production operations and strategy SDK v1:
multi-chain market observation, per-chain quote ladders, an append-only paper ledger,
declarative and visual graph strategies, external Python strategy plugins in a supervised
worker, an MCP server for agents, a backtester, migrations, authentication, observability,
a complete operator surface and a full-code stress harness. Live execution, keys, wallet
custody and a live UI are not built, and the unmerged executor foundation is not a claim.

## Done — live paper slice

Multi-chain browsing, a real paper ledger and user-defined paper strategies work. The
foundation covers the API, Postgres, adapters, core packages, engine and the local gate.
Live market data comes from DexScreener with Jupiter quotes. Orders are idempotent, fills
and positions are journaled together, and risk refuses positions it cannot price. The web
app covers live markets, token pages, the paper ticket and the strategy builder. The
integration gate, live smoke test and restart drills are recorded as evidence.

## Done — strategy SDK v1 and agent surface

The plugin SDK is a standalone Apache-2.0 package with a manifest, context and intent
contract. Plugins run in a supervised worker with a scrubbed environment, resource limits,
a per-callback timeout and crash quarantine. Discovery works through entry points and a
configured plugin path, with a catalogue, validation and reload API. The MCP server offers
read tools and resources, hides paper-only mutations by default and preserves bearer
authentication. The API derives execution mode on the server and refuses live mode until an
executor exists. Agent packs, scoped keys and a live-readiness endpoint are in place.

## Done — production operations (paper)

Market observation covers 22 of 25 chains, with source and age on every row. The engine,
reconciliation, kill switch, honest order rejections and UTC timestamps are in place.
Forward-only migrations, bearer authentication and a CSRF-aware proxy, health and metrics
endpoints, JSON logs and a runnable PAPER Compose stack are shipped. The verified
deployment is recorded against an earlier migration head, so a fresh drill at the current
head remains open. The stress harness and labelled web fixtures are in place.

## Versions

This roadmap sets out the scope of each version, not a schedule: no dates are promised, the
order may change, nothing in a `0.x` release enables real-money trading, and the product
ships with no strategies. The per-version change record is [CHANGELOG.md](CHANGELOG.md); the
release workflow and how a download is verified are in [docs/RELEASES.md](docs/RELEASES.md).

### 0.1.0 — release blockers only

The first public release, in preparation. Scope is correctness and honest public
documentation of the shipped paper surface: a clean-clone install, a real paper trade inside
the default gate, a publication audit, version agreement and a licence scan. No new
features.

### 0.2.0 — paper-product completeness

Finish the paper product: the remaining partial screens, sign-in and onboarding, data
ingest, per-user authentication and role-based access, and documentation polish. Still
paper-only.

### 0.3.0 — paper-only execution groundwork

Prepare the execution path without moving real money: a venue adapter that quotes a
full-size sell and simulates before broadcasting, and persisted, idempotent order intents.
Paper-only.

### 0.4.0 — native and Rust completion

Complete the Rust core migration, make the native host primary, and work on performance and
peer-to-peer networking. Still paper-only.

### 1.0.0 — live execution, gated

Live execution only, and only with the maintainer's written approval: an executor with an
encrypted vault, a paper gate that cannot be forged from the client, server-derived mode, a
kill switch the executor honours, live venues and the live book. There is no date.

Signed plugin and agent packages with reproducible provenance, a security review of the MCP
tool surface and the key lifecycle, multi-tenant ledger partitioning, and official agent
listings belong here or later, never in a `0.x` release.

## Experimental — pump.fun firehose

Near-native pump.fun market data is under investigation. Discovery events are hints only.
Live pump.fun trading is not planned until a venue adapter, safety evidence and a recorded
architecture decision are in place.

## Explicitly out of scope for v1

Derivatives execution, fiat on-ramp, lending, copy-trading marketplace, native mobile
apps, managed hosting and subscription/payment billing. See `docs/PRODUCT.md` §5.
