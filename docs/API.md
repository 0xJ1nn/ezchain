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

A request that fails schema validation returns FastAPI's `422` list under `detail`. The
order body, the kill-switch body and the `/quotes` query refuse fields the route does not
declare (`type: "extra_forbidden"`), and each entry's `loc` names the offending field, so
a misspelt limit is an error rather than a silent drop. Response models stay permissive:
a new field never breaks an existing client.
Status codes:

| Status | Meaning |
|---|---|
| 200 | Read or evaluation result |
| 201 | Order, strategy, backtest, arm, profile or key created |
| 204 | Delete or revoke succeeded |
| 400 | An unreadable cursor or a malformed `If-Match` precondition |
| 401 | Missing or invalid bearer token; `UNAUTHORIZED` |
| 403 | Principal lacks the route's scope; `FORBIDDEN` |
| 404 | Unknown chain, token, strategy, plugin, backtest, arm or profile |
| 409 | A route refused the current state, or no executable sell route |
| 422 | Invalid body, parameter, graph, strategy parameters or protection rules |
| 429 | An upstream provider rate-limited the request |
| 500 | The strategy run completed but its log row could not be written |
| 502 | An upstream provider failed or was unreachable |
| 503 | The paper order executor is not wired |

Every code in this table, and every code that only appears as a stored `reason`, is
enumerated with its meaning, source module, HTTP status, retryability and severity in
[REASON-CODES.md](REASON-CODES.md). That document is generated from one module,
`ezchain_core.reason_codes`, and a contract test fails the build when a code is emitted
without a row there. Compare on `code`, never on `message`.

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
| GET | `/healthz` | public | Process liveness and the server-derived execution mode; does not touch the database |
| GET | `/readyz` | public | Database reachability and Alembic head |
| GET | `/health` | public | Database, data mode and scheduler flags |
| GET | `/metrics` | public | Prometheus text; keep it on loopback |
| GET | `/api/v1/chains` | read | Registry, capabilities and paper/live flags |
| GET | `/api/v1/system/kill-switch` | read | Persisted operator switch, global and strategy levels |
| POST | `/api/v1/system/kill-switch` | admin | `{active, reason?}`: arm or release the switch in one step |

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
ranking; a feed-wide search is a global sort of the matching rows, and a single-chain read
is `per_chain_sort`. `sort_semantics` and `ordered_by` say which ordering was used.
Coverage `reason` is `pairs`, `no_pairs`, `no_seeds` (nothing was requested, so the chain's
population is unknown), `error` or `timeout`.

### Market freshness

Every market read model — `/markets` rows, `/markets/stored` rows and the token header —
reports provenance from the one `MarketEvent` contract, never from the adapter that
happened to answer:

- `source` is the observation's `SourceRef` (`name`, `url`, `observed_at`).
- `age_seconds` is that source timestamp measured against the read clock, computed at read
  time and floored to whole seconds. It is `null` when the read has no usable source.
- `stale` is the contract's verdict on the same clock (`DEFAULT_STALENESS_SECONDS`, 60s),
  derived from the same floored age, so the number shown and the verdict always agree.

A row whose persisted source is blank or the writer's `unknown` sentinel has no
observation behind it: `source` is `null` and `age_seconds` and `stale` are `null` too, so
an unprovenanced row is never presented as fresh. A source dated further ahead than the
tolerated clock skew is likewise an unusable unknown (ADR-0036): its `age_seconds` and
`stale` are `null` rather than a fresh-looking zero. On `/markets/stored` this makes
`stale` nullable; `/markets` always has a discovery source. The token header keeps its
boolean `stale` field and adds `age_seconds`; a token with no source reports `stale: false`
and a null age, while a token dated beyond the tolerated skew reports `stale: true` and a
null age so it never reads as fresh.

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
| GET | `/api/v1/paper/wallets` | read | Paper wallets and the unallocated cash bucket |
| GET | `/api/v1/paper/positions` | read | One server-computed `PositionView` per open position |
| GET | `/api/v1/paper/orders?limit=&offset=` | read | Order attempts, including rejected orders and reasons, newest first |
| POST | `/api/v1/paper/orders` | trade | Submit one paper order |
| GET | `/api/v1/paper/fills?limit=&offset=` | read | Fills with quantity, price, notional and fee, newest first |
| GET | `/api/v1/paper/ledger?limit=&offset=&account=&kind=` | read | Append-only journal entries, newest first |
| GET | `/api/v1/paper/performance` | read | Paper performance summary and daily realized series |
| GET | `/api/v1/paper/attribution` | read | Per-token paper attribution |
| GET | `/api/v1/paper/limits` | read | Server limits in force for this account |
| GET | `/api/v1/paper/alerts` | read | Reconciliation, mark, rejection and kill-switch alerts |
| GET | `/api/v1/paper/reconciliation` | read | Journal, fill and order agreement checks |
| GET | `/api/v1/paper/approvals?status=&limit=` | read | Agent orders waiting for a person |
| GET | `/api/v1/paper/approvals/{approval_id}` | read | One approval and its decision |
| POST | `/api/v1/paper/approvals/{approval_id}/approve` | trade | Approve the stored order and execute it |
| POST | `/api/v1/paper/approvals/{approval_id}/reject` | trade | Reject the stored order with a reason |
| GET | `/api/v1/paper/automation` | read | This user's automation tier and disclosure |
| PUT | `/api/v1/paper/automation` | trade | Switch the tier; `full_auto` needs the acknowledgement |
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
`INSUFFICIENT_POSITION`, `PRICE_UNKNOWN`, `QUOTE_STALE`, `SNAPSHOT_STALE`,
`MARK_TIMESTAMP_FUTURE`,
`LIQUIDITY_UNKNOWN`, `LIQUIDITY_TOO_LOW`, `PAIR_AGE_UNKNOWN`, `PAIR_TOO_YOUNG`, `NO_ROUTE`,
`NO_SELL_ROUTE`, `PRICE_IMPACT_TOO_HIGH` and `KILL_SWITCH_ARMED`.

An order may name a risk profile (`risk_profile`, also spelled `trade_risk_profile` or
`profile`): any of `low`/`mid`/`high` is accepted, the deployment profile is only the
default, and an unknown name is a stored `PROFILE_UNKNOWN` rejection. The profile owns its
own order ceiling — `high` is $250 — and the profile's percent-of-equity, per-token and
slippage policy applies with it. `GET /paper/limits` reports that same effective ceiling in
`max_order_usd`, so the displayed limit is the one the gate enforces.

The history reads (`/paper/orders`, `/paper/fills`, `/paper/ledger`) accept `offset`
alongside `limit` so history older than the newest page stays reachable. `offset` is
bounded to `10000`; a larger value is `422`, so a paging client cannot ask the database to
skip an unbounded number of rows.

`QUOTE_STALE` and `SNAPSHOT_STALE` mean a price was too old to trust: the venue quote and
the market mark respectively. `MARK_TIMESTAMP_FUTURE` means a timestamp ran further ahead
of the clock than the tolerated skew, which is a clock fault rather than fresh evidence.
None is ever zero-filled or carried forward — a stale source refuses to price, and the
order row is the record of the refusal. A stale mark refuses an *entry* with a persisted
`SNAPSHOT_STALE` rejection row (a deterministic data-quality refusal, so a retry does not
replay it); a transient `NO_ROUTE`/timeout still raises without claiming the key, so the
same idempotency key can retry.

A reduce-only exit is never trapped by a mark. It sizes from the held quantity, never from
a stale mark. On a chain with an executable venue the venue's fresh sell quote is the
exit's price. On a `paper_mark`-only chain there is no other price, so the exit settles at
the last observed mark rather than remain unsellable; that mark never values the account
(see `mark_status` below).

Every order, including a rejected one, carries an `actor` object recording who placed it.
The server sets it from the authenticated caller and never from the request body: `actor`
is a refused field on `POST /paper/orders` (`422`), so a client cannot claim an identity.

```json
{"actor": {"kind": "agent", "id": "3f6c…"}}
```

`kind` is `human` for the deployment token (addressed by its subject), `agent` for a
scoped agent key (addressed by the key id, so revoking and reissuing a key leaves past
orders attributed), or `strategy` for an order the engine placed (addressed by the
strategy id). `id` is `null` exactly when `kind` is `unknown`, which is what a row written
before the actor was recorded reports — it is never derived from another column after the
fact. A development process with no token has no caller, so its orders are `unknown`.

The same actor is copied onto every leg of the order's journal group, so
`GET /paper/ledger` and the ledger CSV report `actor_kind` and `actor_id` per entry
without joining back to the order. The genesis journal is `unknown`. A replay of an
idempotency key returns the original order and its original actor; a later caller cannot
re-attribute it.

### Agent orders wait for a person

Automation is a per-user setting with two tiers:

| Tier | Agent order |
|---|---|
| `manual` (default) | Stored and queued for a person; nothing fills until a person approves |
| `full_auto` | Fills through the normal path with no approval |

`GET /api/v1/paper/automation` returns the tier and the risk disclosure; a user with no
setting is `manual`. `PUT /api/v1/paper/automation` switches it, and **only a person may**
(the deployment token, recorded as `human`). The setting is bound to the authenticated
principal's owner (the deployment subject for a person, the key's subject for an agent),
never to the workspace: one user's `full_auto` does not authorize another user's orders.
Switching to `full_auto` requires
`{"tier": "full_auto", "acknowledge_risk": true}`: without the acknowledgement the request
is `422`, and with it the server appends a consent row naming the user, the disclosure
version and the moment. Returning to `manual` leaves that history in place
(`{"tier": "manual"}`). A `full_auto` setting whose consent row is missing (or is for an
older disclosure version) refuses an automated order closed as
`AUTOMATION_CONSENT_REQUIRED`; it is never silently read as `manual`.

The same gate covers automation that did not come from an agent key directly. A strategy
records the principal that created it: an order the engine places for an agent-created
strategy waits exactly like an agent order, while a strategy a person created is not
gated. A strategy whose creating principal predates this record is treated as automation
and fails closed; it never runs unattended by default.

Under `manual`, `POST /paper/orders` from an agent key answers `201` with the stored order
and `status: "pending_approval"`, `reason: "AWAITING_APPROVAL"`, plus `approval_id` and
`approval_status`. Nothing is journaled. The order is claimed by its idempotency key at
proposal time, so a retry from the same agent returns the same pending order.

```json
{
  "id": "…", "status": "pending_approval", "reason": "AWAITING_APPROVAL",
  "approval_id": "…", "approval_status": "pending",
  "actor": {"kind": "agent", "id": "3f6c…"}
}
```

An approval has one lifecycle, and every state names its next action:

| Status | Next action |
|---|---|
| `pending` | `approve_or_reject` |
| `approved` | `execute` (the single attempt a decision takes) |
| `rejected`, `expired`, `superseded`, `executed`, `failed` | `none` |

A pending approval expires after `EZCHAIN_APPROVAL_TTL_SECONDS` (300 by default); a read
reports it as `expired` without writing, and a decision on it transitions it and is
refused with `APPROVAL_EXPIRED`. A newer proposal from the same agent for the same chain,
token and side supersedes an older pending one. An unrecognised stored status is reported
as `failed`, never as pending.

The approver sees the server's own evidence, never agent-written text: `risk_decision`
carries the stage, the verdict, the structured reason codes, and the quote with its cost
sheet, captured when the order was gated. The stored `intent` is what executes.

`POST /paper/approvals/{id}/approve` (body `{"note": "…"}` optional) runs the stored intent
through the same `Intent → RiskDecision → Quote → Order → Fill → Journal` path. It cannot be
widened into a different trade, and the order keeps its original id and idempotency key. If
the gate refuses the order at execution, the approval ends `failed` with the order's reason
code. `POST /paper/approvals/{id}/reject` requires a `note`; the order is refused with
`APPROVAL_REJECTED` and the note is kept on the approval and in the order's reasons.
Decisions are attributed: `decided_by` names the person.

An agent key may read the queue; only a person may decide. Any other principal gets
`403 APPROVAL_REQUIRES_PERSON`, which is how an agent approving its own proposal is made
impossible. An expired approval and an already-decided one are `409 APPROVAL_EXPIRED` and
`APPROVAL_NOT_PENDING`; a missing one is `404 APPROVAL_NOT_FOUND`. If the queue cannot be
read, an agent's order is refused closed as `APPROVAL_STATE_UNREADABLE` instead of
filling.

An armed operator switch refuses every buy here, whoever places it, before the market read
and the quote; the rejected order row is the record of the attempt. Sells are not refused
by the switch: an exit can only reduce risk, so reduce-only sells still settle.

**The one gate order for a paper order.** Every gate is a gate *on* `place_order`, in this
order, and nothing is asked twice:

1. Idempotency replay and integrity (side, amount) — no network, no second fill.
2. The kill switch, **entry-only**, before the market read: `KILL_SWITCH_ARMED` /
   `KILL_SWITCH_UNAVAILABLE` for a buy; a sell is never refused.
3. The settlement wallet, entry-only: `WALLET_UNKNOWN` / `SETTLEMENT_ASSET_UNKNOWN`.
4. The market read and the quote. A stale mark refuses an entry with `SNAPSHOT_STALE`; a
   reduce-only exit sizes from the held quantity and still settles.
5. Inside the fill window: the kill switch is re-read (again entry-only), then the position
   replay and the quote-size checks.
6. The risk decision.
7. The **automation tier / approval queue**: an agent order, and a strategy order whose
   strategy an agent created (or whose provenance is unknown), is stored as
   `pending_approval` and queued under `manual` — nothing is journaled; an unreadable
   queue refuses `APPROVAL_STATE_UNREADABLE`, and an unconsented `full_auto` refuses
   `AUTOMATION_CONSENT_REQUIRED`. A strategy a person created is not gated.
8. Fill and journal.

Approving re-enters the same function with the stored intent, so steps 2–6 and 8 run again
(the trade keeps its original id and key) and step 7 is skipped because a person already
decided. Consent is therefore asked in exactly one place — the `PUT /paper/automation`
acknowledgement — and is never re-asked at placement. The executor's live-path
`AUTOMATION_CONSENT_REQUIRED` validates the same `AutomationSetting` for an unattended run,
so a `manual`-tier intent cannot reach a venue by the executor path either.


A fill journals a simulated fee: 30 bps of the notional by default on a `paper_mark`, or
the quote's own venue cost when that is larger. No money reaches a venue or network.

The idempotency key is unique. A retry with the same key returns the stored order; send a
new key for a new intent. What could not be computed is `null`: `equity_usd` and
`unrealized_usd` are withheld when any open mark is unknown, and the account payload
lists the unpriced positions instead of reporting a partial total as complete.

A position's `mark_status` is the server's verdict (`fresh`, `stale`, `unknown` or
`future`). An observation only modestly ahead of the clock is tolerated as `future`; one
further ahead than the skew bound reports `unknown` with no age, so a skewed or
malicious future observation cannot freeze a mark indefinitely. The age is `null`
whenever the status is `unknown`. Both `unknown` and `stale` fail closed: `mark_usd`,
`market_value_usd` and `unrealized_usd` are `null`, and `age_seconds` is withheld for
`unknown`, so no money number is ever derived from a mark the server will not vouch for,
and the account reports the position as unpriced rather than pricing it from an old or
frozen number. `source` is *not* withheld: it is the observation's provenance, not a money
number, and `GET /paper/alerts` needs it to name the source an alert is about. A position
whose row never had an observation reports `source: null`; a stale or unknown mark that
does have one keeps it.

`GET /paper/performance` and `GET /paper/attribution` apply that same policy. A mark the
account read refuses prices nothing on these surfaces either: `mark_usd`,
`market_value_usd`, `unrealized_usd`/`unrealized_pnl_usd` and `equity_usd` are `null`, the
row is reported as incomplete, and the equity series carries a `null` point for the gap
rather than interpolating or carrying the last known value forward. A mark is never
fresh on one read and stale on another.

`GET /paper/alerts` carries one code per derived condition, including
`POSITION_MARK_UNKNOWN`, `POSITION_MARK_STALE`, `POSITION_MARK_FUTURE` (a source clock
ahead of ours; `warning` beyond the tolerated skew, `info` within it),
`RECONCILIATION_FAILED`, `ORDERS_REJECTED`, `KILL_SWITCH_ACTIVE` and `PROVIDER_STALE` (one
per fresh→stale transition, keyed `(chain, token, kind)`). The transition state is
process-local: a restart re-raises once for a source that is still stale.

`GET /paper/account` and `GET /paper/wallets` are read-only. They never create genesis or
re-declare a missing wallet: bootstrap happens at boot and on `POST /paper/orders` only, so
a GET reports a missing wallet row as missing. A sell is an exit, not an entry: on a chain with no declared wallet, or a wallet that
declares a settlement asset other than USD, the fill still happens and its proceeds
settle to the settlement wallet. An identical buy is refused with
`WALLET_UNKNOWN` or `SETTLEMENT_ASSET_UNKNOWN`, naming the chain and, for an asset
mismatch, the asset the wallet declares.

`GET /paper/wallets` lists the declared wallets, settlement first, with the cash the
ledger settled to each. A final `unallocated` row (`id: null`, `chain: "unallocated"`,
`asset: "USD"`) holds cash whose attributed wallet id no longer has a row, so
`sum(balance_usd)` is a complete partition of `cash_usd` rather than a partial answer.

Every ledger entry reports `wallet_id`, the paper wallet that received its cash leg. It is
`null` on legs that are not cash (positions, the `fees:USD` expense, `pnl:realized:USD`
and `equity:genesis`). The no-risk funding top-up is cash: its `cash:USD` leg carries the
settlement wallet id because the shortfall is booked as settlement capital, while its
`funding:USD` contra leg carries none. A `null` is not backfilled: on a legacy cash row it means the row predates wallet
attribution, and the replay reports that cash in the settlement wallet.

Every ledger entry also reports `actor_kind` and `actor_id`, the principal that placed the
order its group belongs to. They are copied onto the legs when written and are `unknown`
with a `null` id for the genesis journal and for rows written before the actor was
recorded; a null is never attributed after the fact.

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
`STRATEGY_PARAM_UNKNOWN`. An unknown strategy is `404 STRATEGY_NOT_FOUND`. `POST
/strategies/{strategy_id}/run` is an explicit manual request, so it evaluates exactly one
cycle even for a paused strategy (and even inside the strategy's interval) and leaves the
strategy paused: the pause gates automatic scheduling only. Resuming separately is what
re-enables the scheduler.

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
