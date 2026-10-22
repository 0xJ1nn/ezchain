# Evidence — PAPER production operations (2026-10-22)

Environment: macOS, local Postgres 16, API on `127.0.0.1:8000`, Next web on
`localhost:3000`, `EZCHAIN_AUTH_ENABLED=true`, `EZCHAIN_AUTO_CREATE_SCHEMA=false`,
Alembic head `0003_paper_fill_order`. All trading is **PAPER**.

Commands below are the ones actually run; numbers are observed, not inferred.

## 1. Live market coverage and monitor

```bash
curl -s http://127.0.0.1:8000/api/v1/markets/overview
curl -s 'http://127.0.0.1:8000/api/v1/markets?chain=all&limit=100'
```

Observed after the inline scan:

- `totals`: `chains=25, covered=22, stale=0, no_data=3, error=0, snapshots=93`
- `no_data`: `osmosis`, `cosmos`, `moonbeam` (no DexScreener pairs; never zero)
- 100 rows across 22 chains, 4–5 rows per data-bearing chain, `ordered_by=chain_round_robin`
- every row carried `source=dexscreener` and an `observed_at`

## 2. Token, quote and paper flow

```bash
curl -s 'http://127.0.0.1:8000/api/v1/tokens/base/0x4200...0006'
curl -s 'http://127.0.0.1:8000/api/v1/quotes?chain=base&token=0x4200...0006&side=buy&amount_usd=10'
# through the Next proxy, which injects the bearer token
curl -X POST http://localhost:3000/api/v1/paper/orders \
  -H 'content-type: application/json' -H 'origin: http://localhost:3000' \
  -d '{"chain":"base","token_address":"0x4200...0006","side":"buy","amount_usd":"1.00","idempotency_key":"..."}'
```

Observed:

- token/quote returned a `paper_mark` with source and age
- paper buy filled; a duplicate `idempotency_key` returned the **same order id** with one fill
- paper sell filled; reconciliation `ok: true`
- account cash `9798.11751100`, 3 positions; `cash.fills_match` independent identity passed

## 3. Strategy lifecycle and honest rejection

```bash
curl -X POST http://localhost:3000/api/v1/strategies ...
curl -X POST .../strategies/{id}/run
curl -X PATCH .../strategies/{id} -d '{"status":"paused"}'
curl -X DELETE .../strategies/{id}
```

Observed:

- create → run → pause → resume → delete returned 204; the run log was readable before
  deletion
- timestamps are UTC (`+00:00`), not the DB session offset
- a strategy order above the risk cap logs `hold` / `ORDER_REJECTED` with the attempted
  action and rejected order id; the stress invariant `strategy.run_order_filled` passes
- `GET /api/v1/paper/reconciliation` reports `ok: true`

## 4. Auth and proxy

```bash
# direct API, no token
curl -X POST http://127.0.0.1:8000/api/v1/system/kill-switch ...   # 401 UNAUTHORIZED
# same-origin browser request through Next
curl -X POST http://localhost:3000/api/v1/system/kill-switch ...   # 200, token injected
# cross-site
curl -X POST http://localhost:3000/api/v1/system/kill-switch ... \
  -H 'origin: http://evil.example' -H 'sec-fetch-site: cross-site'  # 403 CROSS_ORIGIN_DENIED
```

Observed 401/200/403 exactly as above. The browser never receives the token.

## 5. API-down and restart drill

With the API stopped:

```bash
curl -s http://localhost:3000/markets | grep -E 'UPSTREAM_UNREACHABLE|Markets unavailable'
```

Observed: HTTP 200 page containing `Markets unavailable` and `UPSTREAM_UNREACHABLE`; no
fixture rows were substituted. After restarting the API:

- cash `9798.11751100`, realized `-0.91288107`, 3 positions — **identical** before/after
- `GET /readyz` returned 200 with `revision=0003_paper_fill_order`

## 6. Stress suite (auth enabled)

```bash
EZCHAIN_STRESS_API_TOKEN=... .venv/bin/python -m tools.stress \
  --base-url http://127.0.0.1:8000 --mode smoke ...
```

Observed:

| Mode | Result |
|---|---|
| smoke (10s, 2 workers) | PASS, 0 findings |
| idempotency (10s, 2 workers) | PASS, 0 findings |
| chaos (10s, 2 workers) | PASS, 0 findings |
| soak (60s, 4 workers) | PASS, 0 findings |
| rejection (`--strategy-order-usd 1500`, `--file-issues`) | PASS, `no issues filed` |

Earlier the rejection scenario failed, filed **#71**, and PR #72 fixed the
`ORDER_REJECTED` logging. Re-running with the same fingerprint filed nothing.

## 7. Gate

```bash
bash tools/ci.sh
.venv/bin/pytest -q
EZCHAIN_TEST_DATABASE_URL=postgresql+psycopg://.../ezchain_test bash tools/check-migrations.sh
```

Observed: `ci: ok`; 398 Python + 36 Playwright tests passed; migration check
`No new upgrade operations detected`.

## Not shown here

- Docker image build/`compose up` — now verified; see
  [docs/evidence/p0-production/docker-up.md](docker-up.md)
- per-user auth/RBAC, live execution/keys, EVM venue, Timescale retention
