# ROADMAP — ezCHAIN

> **ALPHA — PAPER IN THIS BUILD.** `0.1.0` targets real-money trading, but no build
> enables live mode until the readiness gate in
> [docs/LIVE-EXECUTION.md](docs/LIVE-EXECUTION.md) is satisfied and the maintainer enables
> it. Official agent listings and any live-mode claim wait for that readiness measurement;
> today's Hermes and OpenClaw packs are local integrations, not official marketplace
> listings.

## What exists today

The current code is the PAPER stack with production operations and strategy SDK v1:
multi-chain market observation, per-chain quote ladders, an append-only paper ledger,
declarative and visual graph strategies, external Python strategy plugins in a supervised
worker, an MCP server for agents, a backtester, migrations, authentication, observability,
a complete operator surface and a full-code stress harness. Live execution, keys, wallet
custody and a live UI are not built today; they are in scope for `0.1.0`, behind the
readiness gate, and the unmerged executor foundation is not a claim.

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
order may change, the product ships with no strategies, and no build enables real-money
trading until the readiness gate in [docs/LIVE-EXECUTION.md](docs/LIVE-EXECUTION.md) passes
and the maintainer enables live mode. `0.1.0` is the only launch lane and carries the
complete product; `0.2.0` and later are post-launch version names kept as empty placeholders.
The per-version change record is [CHANGELOG.md](CHANGELOG.md); the release workflow and how
a download is verified are in [docs/RELEASES.md](docs/RELEASES.md).

### 0.1.0 — the complete product, including live

The first public release and the only launch lane. It carries the whole product: the paper
surface and the remaining screens and onboarding, data ingest, authentication and roles,
execution groundwork, the Rust and native work, and live execution with real money. The
version identifier stays `0.1.0`. Live mode is targeted, not enabled: no build turns it on
until the readiness gate in [docs/LIVE-EXECUTION.md](docs/LIVE-EXECUTION.md) passes and the
maintainer enables it. The release-defining gates are a clean-clone install, a real paper
trade in the default gate, version agreement and a publication audit, with the live
readiness rollup, custody and key runbooks and a venue rehearsal recorded before live mode
is enabled. No dates are promised.

### 0.2.0 — post-launch placeholder

Empty. Work is added after `0.1.0` ships only if the owner adds it.

### 0.3.0 — post-launch placeholder

Empty. Work is added after `0.1.0` ships only if the owner adds it.

### 0.4.0 — post-launch placeholder

Empty. Work is added after `0.1.0` ships only if the owner adds it.

### 1.0.0 — post-launch placeholder

Empty. Work is added after `0.1.0` ships only if the owner adds it.

Signed plugin and agent packages with reproducible provenance, a security review of the MCP
tool surface and the key lifecycle, multi-tenant ledger partitioning, and official agent
listings are not part of `0.1.0`; they belong to post-launch versions if added.

## Experimental — pump.fun firehose

Near-native pump.fun market data is under investigation. Discovery events are hints only.
A pump.fun live route needs a venue adapter, safety evidence and a recorded architecture
decision before any live mode is enabled.

## Explicitly out of scope for v1

Derivatives execution, fiat on-ramp, lending, copy-trading marketplace, native mobile
apps, managed hosting and subscription/payment billing. See `docs/PRODUCT.md` §5.
