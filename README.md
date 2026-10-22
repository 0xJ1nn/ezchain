# ezCHAIN

**One open-source crypto trading app. Every chain. Your strategies. Your keys.**

ezCHAIN is a self-hostable trading workstation: multi-chain wallets, a real ledger, live
positions, automations, signals, backtests and a strategy plugin SDK. It ships with **no
strategies** — the platform, not the alpha, is the product.

> Status: **M1 — web UI scaffold**. The shell and dashboard are implemented from the
> wireframe. The API, engine, executor and ingest services are not built yet; routes
> without an implementation say so instead of pretending. The product spec, architecture,
> infra contract and 31-page wireframe remain the source of truth.

## Why this exists

Four previous bots (solbot v1/v2/v3, pumpbot) proved the pieces but never the product:
monoliths that grew to 300k LOC, three position-tile implementations, ledgers that
disagreed with themselves, strategies hard-wired into the engine, and one chain only.
ezCHAIN inverts that: a small, boring, modular platform with hard invariants, where a
strategy is a removable plugin and a chain is an adapter.

Read [docs/LEARNINGS.md](docs/LEARNINGS.md) for the evidence and the
[ADR log](docs/DECISIONS.md) for the decisions that follow from it.

## What is in this repo

| Path | What |
|---|---|
| `project.md` | Canonical engineering rules (governance, scope, tests, releases) |
| `apps/web/` | Next.js UI: shell + dashboard (M1), fixture-driven |
| `packages/ui/` | Generated design tokens shared by the web app |
| `docs/` | Product, architecture, domain model, chains, SDK, infra, security, workflow |
| `design/wireframe/` | 31-page clickable low-fi wireframe (open `index.html`) |
| `design/BRIEF.md` | Design brief for the visual pass (Claude Design input) |
| `infra/` | Docker Compose + Caddy + Prometheus blueprint (deployment contract, not runnable yet) |
| `.github/` | PR/issue templates, CODEOWNERS, release notes template |
| `tools/` | Local gates (`tools/ci.*`) |

## Principles

1. **The ledger is the only accountant.** Cash is `SUM(ledger_entries.amount)`; every
   fill writes its journal entries in the same transaction as the position update.
2. **One execution path.** Paper and live share the same pipeline; only the venue
   adapter differs. No second "paper engine".
3. **Strategies are plugins, never features.** No strategy code in this repo.
4. **Every number has one home.** Config is schema-validated; the UI reads it; nothing
   re-declares a default.
5. **Chains are adapters.** A capability matrix, not `if chain == "solana"` branches.
6. **Measured, not vibes.** Backtests and paper gates are mandatory before live; claims
   name their data population and harness.

## Quick start — web UI (M1)

```bash
pnpm install
pnpm dev
open http://localhost:3000
```

The UI is fixture-driven: no API, no keys, no chain calls. Every route without an
implementation renders an explicit "not implemented yet" page.

## Wireframe

```bash
npx serve design/wireframe      # or any static file server
```

The full stack quick start (compose, API, executor) lands when those services exist:

```bash
cp infra/.env.example infra/.env
docker compose -f infra/compose.yaml up -d
open http://localhost:8080
```

## License

AGPL-3.0-or-later. Strategies authored by users are their own; the plugin SDK is
Apache-2.0 so strategies can be distributed under any license.
