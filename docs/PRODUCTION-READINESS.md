# PRODUCTION READINESS — PAPER stack

Status: **paper-ready, live-execution-not-built**. This document records what was
measured on 2026-10-22, not what is planned. It is the honest answer to "can I run this
and trust the paper numbers?".

Raw commands and observed results: [docs/evidence/p0-production](evidence/p0-production/README.md).
Scope: PAPER only. There is no key custody, no signing, no live order, no fiat rail and
no copy-trading. `live_execute` is `false` for every chain.

## 1. Verified

| Area | Evidence | Result |
|---|---|---|
| Packaging / tests | `bash tools/ci.sh`, `.venv/bin/pytest -q` | 1044 Python + 94 Playwright, green |
| Market eyes | `/api/v1/markets/overview` after a scan | 22/25 chains `ok`, 3 `no_data`, 0 `error`, 90 snapshots |
| All-chain browse | `/api/v1/markets?chain=all&limit=100` | 100 rows across 22 chains, round-robin, source+age on every row |
| Paper buy/sell | `POST /api/v1/paper/orders` on Base WETH and Solana BONK | filled; ledger groups balanced |
| Idempotency | duplicate `idempotency_key` | same order id, exactly one fill |
| Reconciliation | `GET /api/v1/paper/reconciliation` | `ok: true`; independent cash-vs-fills identity |
| Strategy lifecycle | create → run → pause → resume → delete with runs | 204 delete; run log retained until delete (ADR-0022) |
| Strategy scheduler | inline engine, interval gate, run log | runs; timestamps UTC |
| Rejected orders | strategy order above risk cap | `hold` / `ORDER_REJECTED`, never a fake fill |
| Kill switch | `POST /api/v1/system/kill-switch` | persists across restart; scheduler skips while armed; manual paper orders stay available |
| Auth | direct API vs Next proxy | direct mutation without token 401; proxy mutation 200; cross-site 403 `CROSS_ORIGIN_DENIED` |
| Migrations | 0001→0006 on fresh and legacy DBs | `alembic check` clean; genesis, fill-order and backtest tables present |
| Observability | `/healthz`, `/readyz`, `/metrics`, JSON logs | 200/200/200 with DB at head; request id/path/status/duration logged |
| Stress | `--mode all` against real Postgres + live data | PASS, 0 findings: smoke, idempotency, 27 fuzz cases, 4-way sell race, kill switch, chain matrix. Evidence: [stress-all-2026-10-23.md](evidence/p0-production/stress-all-2026-10-23.md) |
| Stress → issue loop | `--strategy-order-usd 1500` | filed #71; PR #72 fixed it; same run now passes and files nothing |
| Web truth | dashboard, strategy builder | live reads carry `PAPER`/source/age; fixture mode is labelled; DCA/dip vocabularies match the validator |
| Graph strategies | `/api/v1/graph/catalogue`, `/validate`, `/dry-run`, `/backtests` | validated live against Base WETH; dry run placed no order; backtest returned `BACKTEST` with GeckoTerminal closed bars. Evidence: [graph-api-paper.md](evidence/p0-production/graph-api-paper.md) |
| Paper read models | `/paper/ledger`, `/performance`, `/attribution`, `/limits`, `/alerts`, CSV exports | server-computed from the ledger; unknown marks stay `null`; covered by the 1044-test gate |
| Live UI paper flow | Playwright live spec + 8001 API + disposable Postgres | buy $1 → position → full sell → reconciliation `ok: true`; evidence: [live-ui-paper-flow.md](evidence/p0-production/live-ui-paper-flow.md) |
| Gate determinism | Playwright with a dev server on :3000 | fails fast with instructions instead of corrupting `.next`; green without it |

## 2. Not verified

These are explicit gaps, not silent assumptions:

1. **Docker at the current migration head.** The 2026-10-22 drill on Docker 29.9.2 /
   Compose v5.6.1 passed at head `0003_paper_fill_order` (five services healthy, paper BUY
   through the edge, restart and backup drills). The stack has **not** been re-drilled at
   head `0006_backtests` or after the graph/read-model changes. Evidence for the historical
   drill: [docker-up.md](evidence/p0-production/docker-up.md). Re-run
   `docker compose -p ezchain-verify -f infra/compose.yaml up --build -d` at the release
   commit before claiming Docker parity.
2. **Per-user auth / RBAC.** The edge injects one deployment bearer token. It is not an
   identity; anyone who can reach the edge can mutate the paper account. Keep the default
   `EZCHAIN_BIND=127.0.0.1` or put a trusted authenticating proxy in front.
3. **`/metrics` is unauthenticated.** It exposes paper order/fill counts and `ezchain_up`.
   Keep it off the public internet.
4. **Live execution, keys, executor, vault.** Not built in `main`. The unmerged
   `feat/live-executor` foundation was reviewed and **BLOCKED** (venue transactions not
   bound to the intent, sell cap bypass, confirmed-sell bookkeeping gap). `services/ingest`
   and the Redis/NATS/OTel topology remain blueprint-only. `live_execute` is false.
5. **EVM execution.** Non-Solana paper fills are `paper_mark` (capped buys, reduce-only
   sells), not executable venue routes. ADR-0020.
6. **Market coverage is 22/25.** Osmosis, Cosmos Hub and Moonbeam returned no DexScreener
   pairs on 2026-10-22. They stay `no_data`, never zero.
7. **The dashboard is live** against `/paper/account`, `/paper/positions`, `/paper/limits`,
   `/paper/alerts` and the strategy/market reads. It does not draw an equity curve because
   no equity-snapshot history is stored. Wallets have no backend yet; the page says so.
8. **Timescale retention / market-event ingest / replay harness** are M2, not part of this
   stack. Snapshot storage is current-state `market_snapshots`, not a tick history.
9. **Per-token attribution exists** (`/paper/attribution`); per-strategy attribution is
   not exposed because a paper position is shared by the account. The dashboard does not
   invent it.
10. **Playwright does not run the full create→trade→delete flow against the live API**;
    the Python stress harness covers the API flow. Add an API-backed e2e run when a
    disposable Postgres is available.

## 3. Operator checklist

```bash
# 1. clean checkout and gate
bash tools/ci.sh

# 2. fresh-DB migration gate (disposable database)
createdb ezchain_test
EZCHAIN_TEST_DATABASE_URL=postgresql+psycopg://$USER@localhost:5432/ezchain_test \
  bash tools/check-migrations.sh

# 3. production-like API
cp .env.example .env
# set EZCHAIN_API_TOKEN (>=32 chars) and a non-default EZCHAIN_DATABASE_URL
EZCHAIN_AUTO_CREATE_SCHEMA=false .venv/bin/alembic -c apps/api/alembic.ini upgrade head
.venv/bin/uvicorn ezchain_api.main:app --host 127.0.0.1 --port 8000

# 4. web with server-side token
EZCHAIN_WEB_MODE=live EZCHAIN_API_URL=http://127.0.0.1:8000 \
EZCHAIN_API_TOKEN=<same token> EZCHAIN_PUBLIC_URL=http://localhost:3000 \
  pnpm --filter @ezchain/web dev

# 5. stress (auth enabled)
EZCHAIN_STRESS_API_TOKEN=<same token> \
  tools/stress/run.sh --mode smoke --duration 30 --chain base \
  --token 0x4200000000000000000000000000000000000006 --token-symbol WETH
```

Stop the `next dev` server before running `pnpm --filter @ezchain/web test`; the gate
refuses to share `apps/web/.next` with it.

## 4. Release rule

Paper results are labelled `PAPER`. Do not promote this stack to live money until:

- a venue adapter with a full-size sell quote and simulation passes its contract suite;
- the executor/vault boundary and a capped live pilot are implemented (ADR-0009/0011);
- per-user auth, audit identity and the live paper gate are enforced;
- a supervised Docker deploy, backup/restore drill and 24h soak have passed;
- the docs and `docs/CONTRACT.md` claims have been re-reviewed.

Until then, "production ready" means **safe to run as a self-hosted paper workstation**,
not "safe to trade real funds".
