# Evidence — live PAPER UI flow (2026-10-23)

Scope: a disposable Postgres 16 database, the real API (auth enabled, inline engine and
market scan disabled for the drill), and the real Next.js app in `EZCHAIN_WEB_MODE=live`.
No keys, no signing, no live money.

## Environment

| Item | Value |
|---|---|
| API | `http://127.0.0.1:8001` |
| Database | PostgreSQL 16.15, disposable `ezchain_dev_verify` |
| Migration head | `0006_backtests` (`/readyz` reported `migration: head`) |
| Web | `EZCHAIN_WEB_MODE=live`, server-side bearer token, port 3301 |
| Market data | DexScreener + GeckoTerminal + the per-chain quote ladder |

## Commands and observed results

```bash
bash tools/check-migrations.sh
# migrations: ok (head + no pending autogenerate diff)

tools/stress/run.sh --mode smoke --duration 5 \
  --base-url http://127.0.0.1:8001 \
  --chain base --token 0x4200000000000000000000000000000000000006 --token-symbol WETH
# stress: PASS — 0 unique finding(s)

pnpm --filter @ezchain/web exec playwright test \
  --config playwright.live.config.ts --grep "live markets"
# 1 passed — live markets and token page render real data

pnpm --filter @ezchain/web exec playwright test \
  --config playwright.live.config.ts --grep "paper buy and sell"
# 1 passed — paper buy and sell complete through the UI
```

The UI test performed, through the rendered app:

1. opened the live Base WETH token page;
2. entered `$1`, previewed the server quote, confirmed the paper order;
3. saw `PAPER FILL` and the order id;
4. opened `/positions` and saw the server position;
5. requested a sell for more USD than the position value (the server clamps the sell to
   the held quantity), confirmed, and saw the second `PAPER FILL`;
6. read `/api/v1/paper/reconciliation` and asserted `ok: true`.

## What this proves

- The browser → Next proxy → API → quote → risk → paper fill → ledger path works live.
- A sell cannot oversell: the server sizes it against the held quantity.
- The position and reconciliation read models agree after a buy and a full exit.
- The app does not fall back to fixtures in live mode; the same test asserts the live
  token page has no `FIXTURE` badge.

## Release gate

The same flow is part of `bash tools/verify.sh --live`. On candidate `744f32d` the full
gate passed: `ci: ok` (1045 Python + 94 Playwright), `stress: PASS` (0 findings) and the
live UI spec `4 passed`.

## Not covered by this drill

- All-chain matrix, soak, fuzz, race and kill switch (run `tools/verify.sh --live`).
- Restart/restore and Docker edge (separate drills in `docker-up.md` and `RUNBOOK.md`).
- Live execution: not built. `live_execute` is `false` everywhere; there are no keys.
