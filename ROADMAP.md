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

## What is next

- Re-drill the Compose stack at the current migration head and record the result.
- Add dashboard equity-snapshot history and the remaining position-tile states.
- Add per-user authentication and role-based access; today one deployment token serves one
  paper workspace.
- Close out the UI follow-ups: a generic automation builder, signal scoring and routing,
  alert delivery channels and gallery polish.
- Build a real, replayable ingest path with provider health and a stable reason-code
  taxonomy before any execution work.

## Later

- A single replayable execution pipeline against a simulated venue, with a venue adapter
  that quotes a full-size sell and simulates before broadcasting.
- An executor with an encrypted vault, a paper gate that cannot be forged from the client,
  server-derived mode and a kill switch the executor honours.
- Signed plugin and agent packages with reproducible provenance, plus a security review of
  the MCP tool surface and the key lifecycle.
- Multi-tenant ledger partitioning, or one workspace per hosted user.
- Official agent listings only after real-mode readiness is measured, not planned.

## Experimental — pump.fun firehose

Near-native pump.fun market data is under investigation. Discovery events are hints only.
Live pump.fun trading is not planned until a venue adapter, safety evidence and a recorded
architecture decision are in place.

## Explicitly out of scope for v1

Derivatives execution, fiat on-ramp, lending, copy-trading marketplace, native mobile
apps, managed hosting and subscription/payment billing. See `docs/PRODUCT.md` §5.
