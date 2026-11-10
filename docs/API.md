# API — ezCHAIN PAPER

The HTTP surface is **PAPER only**. It browses live markets, serves paper accounting
reads and accepts paper order, strategy, backtest and protection mutations. It holds no
key material, signs nothing and has no live-order route. `EZCHAIN_EXECUTION_MODE=live`
is refused at startup.

The OpenAPI document and interactive UI are served at `/openapi.json` and `/docs` on a
local API process. The Compose edge does not publish them.

## Base, formats and errors

- Base path: `/api/v1`. Health and metrics live at the site root.
- JSON, UTF-8, snake_case field names and UTC ISO-8601 timestamps.
- Money is a decimal USD string. Token quantities are decimal strings. No float crosses
  a money path.
- An unknown value is `null`, never `0`.
- History endpoints accept `limit`; market lists also accept cursor or offset pagination.

Errors use one of two shapes. Auth, adapter and domain errors carry a code object:

```json
{
  "detail": {
    "code": "CHAIN_NOT_FOUND",
    "message": "unknown chain ...",
    "retryable": false
  }
}
```

A request that fails schema validation returns FastAPI's `422` list under `detail`.
Status codes:

| Status | Meaning |
|---|---|
| 200 | Read or evaluation result |
| 201 | Order, strategy, backtest, arm, profile or key created |
| 204 | Delete or revoke succeeded |
| 401 | Missing or invalid bearer token; `UNAUTHORIZED` |
| 403 | Principal lacks the route's scope; `FORBIDDEN` |
| 404 | Unknown chain, token, strategy, plugin, backtest, arm or profile |
| 409 | A route refused the current state, or no executable sell route |
| 422 | Invalid body, parameter, graph, strategy parameters or protection rules |
| 429 | An upstream provider rate-limited the request |
| 502 | An upstream provider failed or was unreachable |
| 503 | The paper order executor is not wired |

## Authentication and scopes

Reads (`GET`, `HEAD`, `OPTIONS`) under `/api/` are open by default for a self-hosted
workspace. A hosted deployment sets `EZCHAIN_REQUIRE_AUTH_FOR_READS=true` so reads also
require a principal with the `read` scope.

Every other request under `/api/` requires `Authorization: Bearer <token>`. The
deployment token in `EZCHAIN_API_TOKEN` is an admin principal with every scope. A scoped
key minted through `POST /api/v1/agent/keys` carries only the scopes it was given.
An unset token is tolerated in development with one warning; production refuses to boot
without a token of at least 32 characters.

| Scope | Routes |
|---|---|
| public | `/health`, `/healthz`, `/readyz`, `/metrics` (outside `/api/`) |
| `read` | Every `GET` under `/api/v1`; `POST /graph/validate`; `POST /graph/dry-run`; `POST /plugins/{id}/validate` |
| `trade` | Paper orders, strategy create/update/delete/run, backtests, protection arms and profiles |
| `admin` | Every `/api/v1/agent/keys` request; `POST /api/v1/plugins/reload`; `POST /api/v1/system/kill-switch` |

`GET /api/v1/system/kill-switch` follows the read rule, not the `/system/` prefix: a
self-hosted workspace serves it without a credential, and a hosted deployment with
`EZCHAIN_REQUIRE_AUTH_FOR_READS=true` serves it to a principal holding the `read` scope.
Only the `POST` is `admin`. The Next.js server injects the deployment token in its
server-side `/api/[...path]` proxy; the browser never receives it.

## Read-only POSTs

`POST /api/v1/graph/validate` and `POST /api/v1/graph/dry-run` evaluate a graph without
placing an order. `POST /api/v1/plugins/{id}/validate` checks a plugin configuration.
Those three routes require only the `read` scope. Every other mutation is a paper or
operator action.

## Endpoints

### System and chains

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/healthz` | public | Process liveness; does not touch the database |
| GET | `/readyz` | public | Database reachability and Alembic head |
| GET | `/health` | public | Database, data mode and scheduler flags |
| GET | `/metrics` | public | Prometheus text; keep it on loopback |
| GET | `/api/v1/chains` | read | Registry, capabilities and paper/live flags |
| GET | `/api/v1/system/kill-switch` | read | Persisted strategy-scoped operator switch |
| POST | `/api/v1/system/kill-switch` | admin | `{active, reason}`: arm or release the switch |

### Markets, tokens and quotes

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/markets` | read | Live discovery rows plus per-chain coverage |
| GET | `/api/v1/markets/overview` | read | Per-chain observation status, age and errors |
| GET | `/api/v1/markets/stored` | read | Latest persisted observations |
| GET | `/api/v1/tokens/{chain}/{address}` | read | Token header, pairs, safety and quote preview |
| GET | `/api/v1/tokens/{chain}/{address}/candles` | read | Closed OHLCV bars, oldest first |
| GET | `/api/v1/quotes` | read | Quote or `paper_mark` envelope for a side and size |

`/markets` accepts `chain`, `q`, `sort`, `limit`, `cursor`/`after`/`offset`, and the
optional filters `min_liquidity_usd`, `min_volume_24h_usd`, `min_change_h24`,
`max_change_h24`, `min_pair_age_minutes`, `max_pair_age_minutes` and `dex`. Rows carry
`source`, `observed_at` and decimal-string money. Unknown liquidity, volume or change is
`null`, never zero. A `chain=all` query is a per-chain round robin, not one global
ranking; `sort_semantics` and `ordered_by` say which ordering was used.

`/quotes` requires `chain`, `token`, `side` (`buy` or `sell`) and `amount_usd`, with an
optional `quantity`. It returns the quote kind, route, price, fee and the same cost sheet
the journal records. A non-executable `paper_mark` is labelled as such.

### Candles

`GET /api/v1/tokens/{chain}/{address}/candles?timeframe=5m&limit=300` serves closed bars
from `market_candles`, oldest first. Supported timeframes are `1m`, `5m`, `15m`, `1h`,
`4h` and `1d`; `limit` is 1 to 1000 and defaults to 300. Prices and volumes are decimal
strings; unknown volume is `null`. The forming bar is never returned.

A `200` with an empty `candles` list carries `reason: "NO_POOL"` when the token has no
indexed pool or `reason: "CANDLES_EMPTY"` when the provider has no history. A chain
without a candle provider is `404 CANDLES_UNSUPPORTED_CHAIN`; a provider that reports no
series for an indexed pool is `404 CANDLES_NOT_FOUND`; an exhausted provider is `502
UPSTREAM_UNREACHABLE` with `retryable: true`. A chain outside the registry is `404
CHAIN_NOT_FOUND`.

The token page and the per-position detail page both render this series. The chart names
its source, age, stale state and reason; it never draws a fabricated line.

### Paper account and orders

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/paper/account` | read | Cash, equity, realized/unrealized PnL and composition |
| GET | `/api/v1/paper/positions` | read | One server-computed `PositionView` per open position |
| GET | `/api/v1/paper/orders?limit=` | read | Order attempts, including rejected orders and reasons |
| POST | `/api/v1/paper/orders` | trade | Submit one paper order |
| GET | `/api/v1/paper/fills?limit=` | read | Fills with quantity, price, notional and fee |
| GET | `/api/v1/paper/ledger?limit=&account=&kind=` | read | Append-only journal entries |
| GET | `/api/v1/paper/performance` | read | Paper performance summary and daily realized series |
| GET | `/api/v1/paper/attribution` | read | Per-token paper attribution |
| GET | `/api/v1/paper/limits` | read | Server limits in force for this account |
| GET | `/api/v1/paper/alerts` | read | Reconciliation, mark, rejection and kill-switch alerts |
| GET | `/api/v1/paper/reconciliation` | read | Journal, fill and order agreement checks |
| GET | `/api/v1/paper/export/orders.csv` | read | CSV export |
| GET | `/api/v1/paper/export/fills.csv` | read | CSV export |
| GET | `/api/v1/paper/export/ledger.csv` | read | CSV export |

`POST /paper/orders` takes:

```json
{
  "chain": "base",
  "token_address": "0x...",
  "side": "buy",
  "amount_usd": "100.00",
  "idempotency_key": "a-unique-key"
}
```

It returns `201` with the stored order. A risk refusal is also a stored order, with
`status: "rejected"` and a comma-joined `reason`; it is not an HTTP error. The order row
is the record of the attempt. Example reasons: `ORDER_LIMIT`, `PAPER_MARK_LIMIT`,
`POSITION_LIMIT`, `MAX_OPEN_POSITIONS`, `DAILY_LOSS_LIMIT`, `INSUFFICIENT_CASH`,
`INSUFFICIENT_POSITION`, `PRICE_UNKNOWN`, `QUOTE_STALE`, `LIQUIDITY_UNKNOWN`,
`LIQUIDITY_TOO_LOW`, `PAIR_AGE_UNKNOWN`, `PAIR_TOO_YOUNG`, `NO_ROUTE`,
`NO_SELL_ROUTE`, `PRICE_IMPACT_TOO_HIGH` and `KILL_SWITCH_ACTIVE`.

A fill journals a simulated fee: 30 bps of the notional by default on a `paper_mark`, or
the quote's own venue cost when that is larger. No money reaches a venue or network.

The idempotency key is unique. A retry with the same key returns the stored order; send a
new key for a new intent. What could not be computed is `null`: `equity_usd` and
`unrealized_usd` are withheld when any open mark is unknown, and the account payload
lists the unpriced positions instead of reporting a partial total as complete.

### Strategies

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/strategies` | read | User strategies and last run |
| POST | `/api/v1/strategies` | trade | Create a declarative, graph or plugin strategy |
| GET | `/api/v1/strategies/{strategy_id}` | read | Strategy detail |
| PATCH | `/api/v1/strategies/{strategy_id}` | trade | Pause, resume or change parameters |
| DELETE | `/api/v1/strategies/{strategy_id}` | trade | Delete the strategy and its run log |
| POST | `/api/v1/strategies/{strategy_id}/run` | trade | Evaluate now and return the decision |
| GET | `/api/v1/strategies/{strategy_id}/runs` | read | Decision log |
| GET | `/api/v1/strategies/{strategy_id}/performance` | read | Per-strategy paper performance |
| GET | `/api/v1/strategies/{strategy_id}/live-readiness` | read | Computed paper-gate evidence |

Strategy create and patch validate parameters against the strategy kind. A bad parameter
is `422` with `STRATEGY_PARAM_TYPE`, `STRATEGY_PARAM_RANGE`, `STRATEGY_PARAM_MISSING` or
`STRATEGY_PARAM_UNKNOWN`. An unknown strategy is `404 STRATEGY_NOT_FOUND`; a paused
strategy refuses a manual run with `409 STRATEGY_PAUSED`.

### Graph and backtests

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/graph/catalogue` | read | Node catalogue and pin types |
| POST | `/api/v1/graph/validate` | read | Validation report; never places an order |
| POST | `/api/v1/graph/dry-run` | read | Evaluate against live context; never places an order |
| POST | `/api/v1/backtests` | trade | Run one graph or graph-strategy backtest |
| GET | `/api/v1/backtests?strategy_id=` | read | Stored backtest list, newest first |
| GET | `/api/v1/backtests/{backtest_id}` | read | One stored backtest |

A JSON graph object always returns `200` from `/graph/validate`, with `ok: false` and
`errors` when invalid. A missing or non-object `graph` is `422 STRATEGY_GRAPH_INVALID`.
The dry run returns the decision, trace and context with `mode: "DRY_RUN"`; an invalid
graph there is `422 STRATEGY_GRAPH_INVALID` with the report, and an unknown chain is `404
CHAIN_NOT_FOUND`.

A backtest stores its request, data source, fill model, fee and slippage assumptions and
metrics. An unknown id is `404 BACKTEST_NOT_FOUND`. Invalid input is `422
BACKTEST_INVALID`; a graph with no candles is `422 BACKTEST_NO_CANDLES`; more than 1000
bars is `422 BACKTEST_BARS_LIMIT`.

### Plugins and agents

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/plugins` | read | Installed plugin catalogue and validation issues |
| POST | `/api/v1/plugins/reload` | admin | Rescan plugin paths and entry points |
| GET | `/api/v1/plugins/{plugin_id}` | read | One manifest and installation metadata |
| POST | `/api/v1/plugins/{plugin_id}/validate` | read | Validate a parameter document |
| GET | `/api/v1/plugins/{plugin_id}/health` | read | Last worker/quarantine state |
| GET | `/api/v1/agent/capabilities` | read | Agent-facing capabilities and paper mode |
| GET | `/api/v1/agent/state` | read | One state document for agents |
| GET | `/api/v1/agent/keys` | admin | Key metadata; never a hash or token |
| POST | `/api/v1/agent/keys` | admin | Mint a scoped key; the token is shown once |
| DELETE | `/api/v1/agent/keys/{key_id}` | admin | Revoke a key immediately |

Plugin errors are `404 PLUGIN_NOT_FOUND` and `422 PLUGIN_VERSION_MISMATCH`,
`PLUGIN_NOT_CONFIGURED` or `PLUGIN_CHAIN_UNSUPPORTED`.
Minting a key with an unknown scope or no scopes is `422`; production without
`EZCHAIN_API_KEY_PEPPER` is `409 API_KEY_PEPPER_REQUIRED`.

### Position protection

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/protection/arms?status=&chain=&token_address=` | read | Arms for the account |
| POST | `/api/v1/protection/arms` | trade | Arm a stop-loss, trailing stop or take-profit |
| GET | `/api/v1/protection/arms/{arm_id}` | read | One arm and its state |
| GET | `/api/v1/protection/arms/{arm_id}/events?limit=` | read | Newest-first append-only arm event trail |
| PATCH | `/api/v1/protection/arms/{arm_id}` | trade | Edit an armed rule |
| POST | `/api/v1/protection/arms/{arm_id}/cancel` | trade | Cancel an arm |
| POST | `/api/v1/protection/arms/{arm_id}/evaluate` | trade | Run one evaluation tick |
| GET | `/api/v1/protection/profiles?chain=` | read | Manual and smart profiles |
| POST | `/api/v1/protection/profiles` | trade | Create a profile |
| GET | `/api/v1/protection/profiles/effective?chain=` | read | Effective profile for a chain |

Protection arms are PAPER only and close through the same paper order path as a manual
sell. An unknown arm or profile is `404 ARM_NOT_FOUND` or `PROFILE_NOT_FOUND`; a rule
that cannot be edited or cancelled in its current state is `409 ARM_NOT_EDITABLE` or
`ARM_NOT_CANCELLABLE`; invalid parameters are `422 PROTECTION_PARAM_*` or
`PROFILE_PARAM_*`. An evaluation without a wired executor is `503
ORDER_EXECUTOR_UNAVAILABLE`.

## What the API does not expose

There is no route that accepts a private key, signs a transaction, broadcasts to a venue
or places a live order. There is no fiat on-ramp, withdrawal or copy-trading route. The
agent surface has no live order tool; its mutation tools are paper-only and hidden unless
the MCP server is started with writes enabled.
