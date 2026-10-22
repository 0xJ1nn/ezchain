# ezCHAIN

**One open-source crypto trading app. Every chain. Your strategies. Your keys.**

ezCHAIN is a self-hostable trading workstation: multi-chain wallets, a real ledger, live
positions, automations, signals, backtests and a strategy plugin SDK. It ships with **no
strategies** — the platform, not the alpha, is the product.

> Status: **P0 — live paper slice**. Live multi-chain market browsing (DexScreener),
> Solana paper quotes (Jupiter), an append-only paper ledger with positions/PnL, and
> user-defined declarative strategies that run in paper mode. There are **no keys, no
> signing, no live execution and no fiat rail**. Wallet, automation and system pages
> remain wireframe/fixture surfaces; routes without an implementation say so.

## Why this exists

Four previous bots (solbot v1/v2/v3, pumpbot) proved the pieces but never the product:
monoliths that grew to 300k LOC, three position-tile implementations, ledgers that
disagreed with themselves, strategies hard-wired into the engine, and one chain only.
ezCHAIN inverts that: a small, boring, modular platform with hard invariants, where a
strategy is a removable rule set and a chain is an adapter.

Read [docs/LEARNINGS.md](docs/LEARNINGS.md) for the evidence, the
[ADR log](docs/DECISIONS.md) for the decisions that follow from it,
[ROADMAP.md](ROADMAP.md) for what is current, and
[docs/PLAN-LIVE-PAPER.md](docs/PLAN-LIVE-PAPER.md) for the P0 contract.

## What is in this repo

| Path | What |
|---|---|
| `project.md` | Canonical engineering rules (governance, scope, tests, releases) |
| `apps/web/` | Next.js UI: shell, dashboard, positions, markets, token page, strategies |
| `apps/api/` | FastAPI edge: live markets, quotes, paper orders, strategy CRUD/run |
| `services/engine/` | Declarative strategy evaluator and scheduler (inline in P0) |
| `packages/core-py/` | Domain, append-only ledger, paper accounting, risk, strategy rules |
| `packages/adapters/` | DexScreener market data, Jupiter quotes, 25-chain registry |
| `packages/ui/` | Generated design tokens shared by the web app |
| `docs/` | Product, architecture, domain, chains, plan, infra, security, workflow |
| `design/wireframe/` | 31-page clickable low-fi wireframe (open `index.html`) |
| `infra/` | Docker Compose + Caddy + Prometheus blueprint (not runnable yet) |
| `tools/` | Local gates, setup and the combined dev runner |

## Principles

1. **The ledger is the only accountant.** Cash is `SUM(ledger_entries.usd_value)`; every
   fill writes its journal entries in the same transaction as the position update.
2. **One execution path.** Paper, testnet and live share the same pipeline; only the venue
   adapter and custodian differ. P0 implements paper only.
3. **Strategies are user-defined rules, never code.** P0 ships declarative
   momentum/dip/DCA/threshold rules; arbitrary strategy code is not loaded.
4. **Every number has one home.** Config is schema-validated; the UI reads it; nothing
   re-declares a default.
5. **Chains are adapters.** A capability matrix, not `if chain == "solana"` branches.
6. **Measured, not vibes.** Backtests and paper gates are mandatory before live; claims
   name their data population and harness.

## Quick start — live paper slice

Requires Node 22 + pnpm 9, Python 3.11 and Postgres 16.

```bash
createdb ezchain                 # once
cp .env.example .env             # DB URL, API URL, paper limits
PYTHON=python3.11 bash tools/setup-python.sh
.venv/bin/alembic -c apps/api/alembic.ini upgrade head   # once, forward-only
bash tools/dev.sh                # API on :8000, web on :3000
open http://localhost:3000
```

- API docs: <http://127.0.0.1:8000/docs>
- Liveness/readiness: `/healthz` and `/readyz` (the latter includes the migration head)
- Live markets: `/markets` · token/ticket: `/tokens/{chain}/{address}`
- Paper positions: `/positions` · user strategies: `/strategies`

### Mutations need a bearer token

Reads (`GET`/`HEAD`/`OPTIONS`) are open. Every other request under `/api/` requires
`Authorization: Bearer <EZCHAIN_API_TOKEN>`. The web server injects the token in its
`/api/[...path]` proxy; the browser never receives it. In local dev an unset token still
allows mutations and logs one warning. Production **refuses to start** without a token of
at least 32 characters, `EZCHAIN_AUTO_CREATE_SCHEMA=false`, a non-default database URL
with a password, `EZCHAIN_DATA_MODE=live`, and no `*` CORS origin.

```bash
openssl rand -hex 32             # put this in EZCHAIN_API_TOKEN for the API and web
```

Existing local databases created by `create_all()` before Alembic are unstamped:
`/readyz` reports `migration: missing`. Verify the schema matches the models, then run
`alembic -c apps/api/alembic.ini stamp 0001_baseline` once, then run
`alembic -c apps/api/alembic.ini upgrade head` so the forward-only 0002 index is
applied. Never stamp a production database.

For deterministic UI work and Playwright, set `EZCHAIN_WEB_MODE=fixture`; every fixture is
labelled `FIXTURE` and never presented as live.

## Wireframe

```bash
npx serve design/wireframe      # or any static file server
```

Docker Compose remains the production deployment contract. It is **not runnable yet**:
`infra/compose.yaml` references images and Dockerfiles that do not exist in this
repository. The local quick start above uses the Python API and Next dev server directly.

```bash
# production blueprint — not runnable yet
cp infra/.env.example infra/.env
docker compose -f infra/compose.yaml up -d
```

## License

AGPL-3.0-or-later. Strategies authored by users are their own; the plugin SDK is
Apache-2.0 so strategies can be distributed under any license.
