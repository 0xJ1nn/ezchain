# Evidence — graph API and backtester on the PAPER stack (2026-10-23)

Scope: disposable PostgreSQL 16 database, real API with bearer auth, live DexScreener and
GeckoTerminal reads. No order was placed by the dry run; the backtest is a replay, not
paper or live execution.

## Environment

| Item | Value |
|---|---|
| API | `http://127.0.0.1:8001` |
| Database | PostgreSQL 16.15, disposable `ezchain_dev_verify` |
| Migration head | `0006_backtests` |
| Market target | Base WETH `0x4200000000000000000000000000000000000006` |
| Prepared graph | `event.tick → flow.branch`, `math.const(1) → math.compare(>)` |

## Commands and observed results

```bash
GET  /api/v1/graph/catalogue
# 200: schema, pin_types, categories and node_types from the core catalogue

POST /api/v1/graph/validate   (bearer token)
# 200: {"ok": true, "errors": [], "warnings": [{"code": "NO_EXIT", ...}]}

POST /api/v1/graph/dry-run    (bearer token)
# 200: decision hold / UNKNOWN_CONDITION; trace unknown=["br"];
#      context price_usd=2383.83, position quantity=0.000419..., mode="DRY_RUN"
#      no order was created by the dry run

POST /api/v1/backtests        (bearer token)
# 201: id 357474b8-..., label "BACKTEST", data_source/fill_model present,
#      metrics/equity/trades/node_hits present; candles came from GeckoTerminal
#      closed 5m bars for the requested range
```

The `UNKNOWN_CONDITION` result is correct for the prepared graph: its `math.compare` has
one unwired input, so the branch condition is unknown and neither branch runs. It proves
the three-valued logic fails closed rather than treating unknown as true.

## What this proves

- The catalogue, validation, dry-run and backtest endpoints exist and work against a real
  database and the real providers.
- A dry run cannot place an order; the backtest is labelled `BACKTEST` and uses closed bars.
- The graph scheduler is wired through the existing strategy/order path (unit tests:
  `tests/engine/test_graph_scheduler.py`).

## Not covered

- A real browser session of the visual builder against this API (fixture Playwright covers
  the editor; the live catalogue adapter is exercised by the live route test).
- Mainnet transaction execution: live execution is a separate, unverified path.
