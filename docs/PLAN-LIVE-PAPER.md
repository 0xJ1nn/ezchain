# PLAN — Live paper slice (P0)

Status: **P0.0–P0.5 implemented; P0.6 in progress.** This plan is the contract for the
first working vertical slice. It supersedes the M1 fixture-only assumption for the
surfaces it touches; the rest of M1 (wallets, automations, system pages) stays where it is.

## Goal

A user can open ezCHAIN, browse **live markets on every chain DexScreener covers**, open a
token, place a **paper** buy or sell at a live quote/mark, see the fill in an append-only
ledger, and create a **user-defined strategy** that runs automatically in paper mode.

No private keys, no signing, no real funds, no fiat rail, no copy-trading.

## Success criteria

1. `pnpm dev` + the API + local Postgres boot from a documented two-command flow.
2. `/markets` shows live pairs across at least 20 chains, with source and age on every row.
3. `/tokens/{chain}/{address}` shows live pairs, price, liquidity and a quote.
4. A paper buy and a paper sell produce balanced ledger entries, a position, cash and PnL;
   both survive an API restart.
5. A user can create, pause, resume, run-now and delete a strategy; the scheduler places
   paper orders through the same order service the UI uses.
6. Unknown, stale or route-less data fails closed with a reason code; no fixture is
   presented as live.
7. `pytest`, `pnpm lint/typecheck/build/test` and `bash tools/ci.sh` are green.
8. `docs/`, `CHANGELOG.md`, `.state/` and the ADR log match the code.

## Non-goals

- Live order signing, key custody, hardware wallets, live funds.
- Fiat on-ramp, billing or subscriptions.
- Copy-trading.
- pump.fun firehose (spike #35; live trading later).
- A full strategy SDK sandbox; P0 ships a declarative user-defined rule engine, not
  arbitrary code execution.
- Every DEX on every chain; browsing is broad, execution is capability-gated.

## Architecture for P0

```
Next.js web  --/api rewrite-->  FastAPI apps/api  -->  Postgres 16
                                      |
                                      +--> packages/adapters (DexScreener, Jupiter)
                                      +--> packages/core-py (ledger, paper broker, risk)
                                      +--> services/engine (inline scheduler in P0)
```

- **One process in P0:** the strategy scheduler runs as an asyncio background task inside
  the API when `ENGINE_INLINE=true`. It uses the same order service as the HTTP route.
  A separate `services/engine` process can replace it without changing the domain.
- **Postgres is the source of truth.** No paper state in JSON, process memory or fixtures.
- **No keys exist in P0.** The executor boundary is untouched; `live_execute` is `false`
  for every chain.
- **Fixtures remain for tests.** The API supports `DATA_MODE=fixture` for deterministic
  Playwright and pytest runs; default is `live`.

## Data sources and honesty

| Need | P0 source | Label |
|---|---|---|
| Multi-chain discovery, pairs, price, liquidity, volume | DexScreener public API | `dexscreener` + `observed_at` |
| Solana executable quote (paper) | Jupiter Lite Swap API | `jupiter` + `observed_at`, `paper` |
| Non-Solana paper price | DexScreener best pair | `paper_mark` — **not an executable route** |
| Token safety | Not implemented in P0 | `unknown`; no trade if unknown where a gate requires it |

Rules:

- Every number carries `source`, `observed_at` and `age_seconds`.
- `age > 60s` is `stale`; a stale quote cannot open a position.
- A buy on Solana requires a full-size sell quote; no route → `NO_SELL_ROUTE`.
- Non-Solana paper **buys** are capped at **$250** and labelled `PAPER · MARK` in the UI.
  A paper-mark sell is reduce-only and is not capped, so an exit is always possible.
  This is a deliberate v1 limitation recorded in ADR-0020.
- No client-side PnL or quote math; the API returns computed view models.

## API contract (v1)

Base: `/api/v1`. JSON, UTC timestamps, snake_case. Errors: `{code, message, retryable,
detail}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + DB + scheduler state |
| GET | `/chains` | registry, capabilities, browse/paper/live flags |
| GET | `/markets?chain=all&q=&sort=trending&limit=50` | live discovery rows |
| GET | `/tokens/{chain}/{address}` | token header, pairs, safety unknowns, quote preview |
| GET | `/quotes?chain=&token=&side=&amount_usd=` | quote or `paper_mark` envelope |
| GET | `/paper/account` | cash, equity, realized/unrealized, positions count |
| GET | `/paper/positions` | one `PositionView` shape |
| GET | `/paper/orders?limit=` | order history |
| POST | `/paper/orders` | `{chain, token_address, side, amount_usd, idempotency_key}` |
| GET | `/paper/fills?limit=` | fill history |
| GET | `/paper/reconciliation` | read-only ledger/fill/order reconciliation |
| GET | `/strategies` | user strategies + last run |
| POST | `/strategies` | create declarative strategy |
| GET | `/strategies/{id}` | detail + runs |
| PATCH | `/strategies/{id}` | pause/resume/params |
| DELETE | `/strategies/{id}` | delete strategy and its run log (orders keep attribution) |
| POST | `/strategies/{id}/run` | evaluate now, return decision |
| GET | `/strategies/{id}/runs` | decision log |
| GET | `/system/kill-switch` | persisted strategy-scoped operator switch |
| POST | `/system/kill-switch` | `{active, reason}`: arm/release and return state |

Money fields are integer USD cents in the API and decimal strings at the boundary where
token quantities need precision.

## Paper accounting

- Genesis: `$10,000.00` paper cash.
- Append-only `ledger_entries(id, group_id, account, asset, amount, usd_value, kind,
  created_at)` with `SUM(usd_value) = 0` per group.
- Accounts: `cash:USD`, `position:{chain}:{asset}`, `fees:USD`, `pnl:realized:USD`,
  `equity:genesis`.
- Buy: position +cost, cash −cost, fees +fee, cash −fee. Sell: cash +proceeds,
  position −cost basis, fees +fee, pnl −(proceeds − cost − fee).
- Position quantity and cost basis are derived by replaying that position's entries.
- `PositionView`: chain, token, symbol, quantity, avg_cost, mark, mark_source, mark_age,
  market_value, unrealized, realized, mode=`paper`.
- Idempotency: unique `idempotency_key`; retry returns the original order.

## Risk policy (P0)

- Max order notional `$1,000`; max position `$2,500`; max 10 open positions.
- Max daily realized loss `$500`, enforced as a new-entry block (sells stay available);
  max strategy budget `$2,500`.
- Quote age `<= 60s`; liquidity `>= $10,000`; pair age `>= 10 minutes`.
- Solana buy requires a sell route; non-Solana buy requires `paper_mark` and the $250 cap.
- Order-size, paper-mark-size, position-size, open-position and daily-loss limits gate
  **new risk only (buys)**. A sell is always allowed to reduce exposure; the fail-closed
  data gates (price, liquidity, route, freshness) still apply.
- Any unknown/missing field → `DENY` with a reason code; unknown is never zero.

## Execution hardening (P0.6)

- **Reconciliation is a read model.** `GET /paper/reconciliation` derives everything
  from persisted `ledger_entries`, `paper_fills` and `paper_orders`: per-group
  `SUM(usd_value)` within `0.000001`, cash account sum vs replayed cash, open positions,
  fill/order counts, orphan fills, filled orders without exactly one fill, negative cash
  and negative position quantities, and fill counts by `quote_kind`. It never writes or
  repairs. `ok: false` means pause and investigate, not "retry until clean".
- **Strategy-scoped kill switch.** The flag is persisted in `system_flags`
  (`engine_kill_switch`) so it survives a restart and is visible to every process. This is
  the only new table in this slice; ops generates the Alembic baseline after merge. The
  engine wraps the order executor; a request with `strategy_id` is refused with
  `ExecutionBlocked` (`KILL_SWITCH_ACTIVE`) and the run log records a `hold` with the
  attempted action and amount. The strategy window is not consumed, so releasing the
  switch takes effect on the next run. Manual paper orders (`strategy_id = null`) stay
  allowed. There is no authentication yet; the endpoint is for a trusted network until
  ops adds auth.
- **Strategy deletion is a cascade with an audit trade-off.** Deleting a strategy removes
  its `strategy_runs` decision log and the strategy in one transaction; historical
  `paper_orders.strategy_id` stays as an opaque string. The execution trail is preserved,
  but the reasoning that led to those orders is not. Operators who need the decision log
  for an investigation should **pause** the strategy instead of deleting it.
- **Concurrency is resolved by the idempotency key.** A manual run and the scheduler can
  evaluate the same strategy at once. Both may decide to buy, but the unique
  `paper_orders.idempotency_key` admits one order and one fill per window; the loser
  returns the winning order. No in-process lock is used, so multiple API/engine
  processes stay safe.

## User-defined strategies (declarative, no code execution)

```json
{
  "name": "BONK momentum",
  "chain": "solana",
  "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
  "token_symbol": "BONK",
  "kind": "momentum",
  "params": {
    "lookback": "1h",
    "entry_change_pct": 5,
    "exit_change_pct": -3,
    "take_profit_pct": 20,
    "stop_loss_pct": 8,
    "order_usd": 100,
    "interval_minutes": 60,
    "max_position_usd": 500
  },
  "status": "active"
}
```

Supported kinds in P0:

- `momentum` — buy when the lookback change ≥ entry; exit on change ≤ exit, take-profit or
  stop-loss.
- `dip` — buy when the lookback change ≤ −entry; exit on take-profit or stop-loss.
- `dca` — buy `order_usd` every `interval_minutes` until `max_position_usd`.
- `threshold` — buy at/below `buy_below_usd`, sell at/above `sell_above_usd`.

Rules: one decision per strategy per interval; every run is logged with inputs, decision,
reason and order id; paused strategies never run; all orders go through the paper order
service and the same risk policy.

## Workstreams and file ownership

| WS | Owner | Owns | Must not touch |
|---|---|---|---|
| P0.0 | lead | `pyproject.toml`, `.env.example`, `apps/api` app/config/db bootstrap, `apps/web/next.config.ts`, docs, CI | feature modules |
| P0.1 | swarm A | `packages/adapters/**`, adapter tests | API routes, DB models, web |
| P0.2 | swarm B | `packages/core-py/**`, paper service/repositories, ledger tests | adapters, engine, web |
| P0.3 | swarm C | `services/engine/**`, strategy evaluator tests | API routes, adapters, web |
| P0.4 | swarm D | live markets/token web surfaces, `lib/api.ts`, e2e | paper/strategy UI, shell |
| P0.5 | swarm E | paper ticket/positions/strategies web surfaces, e2e | markets/token pages, shell |

Shared files are lead-owned: `apps/web/components/ui.tsx`, `shell.tsx`, `app/ui.css`,
`lib/format.ts`, `docs/**`, `pyproject.toml`, `.state/**`.

## Phases

### P0.0 — Foundation (lead)

- [ ] `pyproject.toml`, Python 3.11 venv, dependency lock note, `.env.example`.
- [ ] Postgres schema bootstrap + Alembic baseline.
- [ ] FastAPI app, settings, error envelope, CORS/proxy, `/health`, `/chains`.
- [ ] Adapter/core/engine protocols and package skeletons.
- [ ] API fixture mode and live mode switch.
- [ ] Next.js `/api` rewrite.
- [ ] CI additions: `ruff`, `mypy`, `pytest` when the Python tree exists.
- [ ] ADR-0020 (paper mark for non-Solana), ADR-0021 (inline engine in P0).
- [ ] Epic issue + workstream issues + this plan.

### P0.1 — Live multi-chain data (swarm A)

- [ ] DexScreener client: profiles, boosts, takeovers, token batch, search, pairs.
- [ ] 25-chain registry with DexScreener ids; unknown chain → 404.
- [ ] Normalizer: pair → market row; source/age; duplicate pair collapse.
- [ ] Jupiter quote client: exact-in, sell quote, route/fee/impact.
- [ ] Cache + rate limit (token bucket, 429 backoff, request dedupe).
- [ ] Fixture recordings + `respx` tests.
- [ ] Adapter contract tests.

### P0.2 — Paper ledger + order service (swarm B)

- [ ] SQLAlchemy models: ledger entries, paper orders, fills, positions view.
- [ ] Genesis account and balance/position replay.
- [ ] Buy/sell order service with idempotency and risk checks.
- [ ] Quote → fill math (Jupiter outAmount / paper mark, fee bps).
- [ ] Account/positions/orders/fills read models.
- [ ] Property tests: ledger sums to zero, no negative cash/position, replay stable.
- [ ] Restart test.

### P0.3 — Strategy engine (swarm C)

- [ ] Strategy model + CRUD service (API routes owned by lead or B).
- [ ] Evaluator for momentum/dip/DCA/threshold.
- [ ] Scheduler: interval, pause, one-decision-per-window, run log.
- [ ] Risk integration through the order service.
- [ ] Manual run-now endpoint/service.
- [ ] Tests: each kind, cooldown, stop-loss, take-profit, paused, repeated run.

### P0.4 — Web live browsing (swarm D)

- [ ] `lib/api.ts` typed client, no silent fallback.
- [ ] Markets page: live rows, chain filter, search, sort, source/age, error/empty/stale.
- [ ] Token page: live header/pairs/quote, fail-closed reason codes.
- [ ] Loading/refresh; no client PnL/quote math.
- [ ] Playwright against `DATA_MODE=fixture`; screenshots at 1440/390.
- [ ] Update fixture banner logic: live mode says `LIVE`; fixture mode says `FIXTURE`.

### P0.5 — Web paper + strategies (swarm E)

- [ ] Paper account card on dashboard and positions.
- [ ] Buy/sell ticket on token page (amount USD, quote preview, confirm, idempotency key).
- [ ] Positions page from `/paper/positions`.
- [ ] Strategies page: list, create form, pause/resume, run-now, runs log.
- [ ] Error/reason rendering; `PAPER` badge on every number.
- [ ] Playwright e2e for buy → position → sell; strategy create → run.
- [ ] Screenshots at 1440/390.

### P0.6 — Integration and hardening (lead + reviewers)

- [ ] Merge stacked branches, resolve contracts, run end-to-end against live APIs.
- [ ] Manual smoke: browse 5 chains, buy/sell, restart, strategy fires.
- [ ] Failure drills: API down, DexScreener 429, stale quote, no route, DB down.
- [ ] Security: no secrets, no key material, no arbitrary code execution.
- [ ] Update README, ROADMAP, TODO, CHANGELOG, ADRs, state.
- [ ] Open PR(s) with evidence; squash-merge; close issues.

## Acceptance tests

```bash
# backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy --strict packages apps/api services

# web
pnpm -r lint
pnpm -r typecheck
pnpm --filter @ezchain/web build
pnpm -r test
bash tools/ci.sh
```

Live smoke (manual, recorded in the PR): markets over 5 chains, token quote, paper buy,
paper sell, position/PnL, strategy run, restart persistence, 429/stale/no-route states.

## Risks

- **DexScreener is not an execution venue.** Non-Solana fills are paper marks; label them
  honestly and cap size. Live EVM execution needs a venue adapter later.
- **Rate limits** can make the UI look empty. Cache, backoff and show `rate_limited`; never
  fabricate rows.
- **Jupiter Lite** may throttle. Cache quotes for 5s, fail closed on 429.
- **Paper PnL is not a backtest.** Label it PAPER; no profitability claim.
- **Inline engine** can affect API latency. It runs in a bounded task with its own
  session; split it if p95 latency regresses.
- **Scope creep.** Wallet/automation/system pages stay out of P0.
