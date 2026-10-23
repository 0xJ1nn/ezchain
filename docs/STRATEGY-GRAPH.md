# STRATEGY GRAPH — the visual strategy contract (v1)

Status: **implemented for PAPER.** The `graph` strategy kind, the catalogue/validate/
dry-run/backtest HTTP contract, the backtest artifact and the scheduler runtime are
built. A graph strategy is a user-drawn, Blueprint-style node graph. It is still a
*declarative rule set* — typed JSON, validated and evaluated by platform code. There is
no `eval`, no user code and no plugin loading here (plugins are
[STRATEGY-SDK.md](STRATEGY-SDK.md)). ADR-0023 records the decision.

The same graph is what the validator checks, what the backtester replays and what the
paper engine runs. There is exactly one evaluator: `ezchain_core.graph.evaluate_graph`.

**Not implemented:** live signing, the executor, and real-money execution. A graph can
place PAPER orders only, through the existing risk → quote → order → fill → journal path.

## 1. Document shape

```json
{
  "schema": "ezchain.graph/1",
  "nodes": [
    {"id": "tick", "type": "event.tick", "params": {}, "ui": {"x": 0, "y": 0}},
    {"id": "c1", "type": "data.candles", "params": {"timeframe": "5m"}, "ui": {"x": 0, "y": 160}},
    {"id": "fast", "type": "ind.ema", "params": {"period": 9}, "ui": {}},
    {"id": "slow", "type": "ind.ema", "params": {"period": 21}, "ui": {}},
    {"id": "x", "type": "logic.cross_above", "params": {}, "ui": {}},
    {"id": "br", "type": "flow.branch", "params": {}, "ui": {}},
    {"id": "buy", "type": "act.buy", "params": {"amount_usd": "100"}, "ui": {}}
  ],
  "edges": [
    {"from": {"node": "tick", "port": "out"}, "to": {"node": "br", "port": "in"}},
    {"from": {"node": "c1", "port": "close"}, "to": {"node": "fast", "port": "series"}},
    {"from": {"node": "c1", "port": "close"}, "to": {"node": "slow", "port": "series"}},
    {"from": {"node": "fast", "port": "series"}, "to": {"node": "x", "port": "a"}},
    {"from": {"node": "slow", "port": "series"}, "to": {"node": "x", "port": "b"}},
    {"from": {"node": "x", "port": "out"}, "to": {"node": "br", "port": "cond"}},
    {"from": {"node": "br", "port": "true"}, "to": {"node": "buy", "port": "in"}}
  ],
  "comments": [{"id": "k1", "text": "Entry", "ui": {"x": 0, "y": 0, "w": 400, "h": 300}}]
}
```

- `ui` and `comments` are presentation only. The evaluator ignores them; they never
  change a decision. Everything else is semantic.
- Numbers in `params` are JSON strings or numbers and are parsed to `Decimal`.
- Node ids match `^[A-Za-z0-9_-]{1,40}$` and are unique. Limits: ≤ 200 nodes, ≤ 400
  edges, ≤ 50 comments, document ≤ 256 KiB.

## 2. Pin types

| Type | Meaning | Wire colour token |
|---|---|---|
| `exec` | control flow: *what happens next, in order* | `--graph-exec` (white) |
| `bool` | true / false / **unknown** | `--graph-bool` (red) |
| `number` | one Decimal or **unknown** | `--graph-number` (green) |
| `series` | oldest→newest Decimals of closed bars; may be shorter than requested | `--graph-series` (blue) |

Rules:

- An input accepts exactly one wire; an output may fan out. `exec` outputs accept one
  wire (order is explicit; use `flow.sequence` to fan out).
- Type must match exactly, except a `series` output may feed a `number` input: it is
  read as the **last** value (unknown if empty). This is the only implicit cast.
- Data inputs may be left unconnected only if the node declares a default param for
  them (e.g. `math.compare.b` defaults to `params.b`).

## 3. Unknown is not zero (three-valued logic)

Every data value may be **unknown** (`None`): warmup not met, missing snapshot field,
stale data, division by zero. Unknown propagates:

- arithmetic / indicators with any unknown input → unknown;
- `compare` with unknown → unknown;
- `and`: false if any false, else unknown if any unknown, else true (Kleene);
  `or`: true if any true, else unknown if any unknown, else false; `not unknown` → unknown;
- `flow.branch` with an unknown condition takes **neither** branch and records
  `UNKNOWN_CONDITION` with the node id in the trace. Fail closed.

## 4. Evaluation semantics

One **tick** = one evaluation of one graph for one token, at time `now`, with a frozen
`EvalContext`:

```
EvalContext(now, snapshot, candles: {timeframe: [Candle]}, position, account,
            last_fill_at, entry_at, peak_since_entry, vars: {name: value})
```

1. Validation has already passed (section 6); the evaluator re-validates cheaply and
   returns `INVALID_GRAPH` instead of raising.
2. Data nodes are pure and evaluated lazily with memoisation (each node at most once
   per tick).
3. Execution starts at each `event.tick` node and follows `exec` wires depth-first.
   A step budget of 1 000 exec steps per tick prevents runaway graphs (there are no
   loops in v1; the budget is a second fence).
4. Action nodes append **effects** in exec order: `order` (buy/sell), `notify`,
   `set_var`. At most **one order effect** is kept per tick: the first; later ones are
   recorded as `SUPPRESSED_ONE_ORDER_PER_TICK`. Orders are *intents*; the platform still
   applies risk, quote and sizing.
5. The result is `GraphDecision(action, amount_usd, sell_fraction, reason, effects,
   trace)`. `trace` maps node id → last value (stringified Decimal / bool / null) plus
   the ordered list of exec node ids visited, so the UI can light wires and show value
   chips. The trace is persisted in the run log and is what "replay" shows.
6. Determinism: same `GraphDocument` + same `EvalContext` ⇒ byte-identical decision
   and trace. No wall clock (only `ctx.now`), no randomness, no I/O.

### Candles and look-ahead

- `ctx.candles[tf]` contains **closed** bars only, oldest first, each with
  `open_time, open, high, low, close, volume` (volume may be unknown).
- The context builder (paper engine *and* backtester) only includes bars whose close
  time ≤ `now`. A node can never see the forming bar. This is enforced in the builder,
  not left to the user.

## 5. Node catalogue (v1)

`series→` means output series; inputs listed `name:type`. Every indicator also exposes
`value:number` = last element of its series.

### Events
| type | inputs | outputs | params |
|---|---|---|---|
| `event.tick` | — | `out:exec` | — |

### Market data
| type | outputs | params |
|---|---|---|
| `data.price` | `value:number` (snapshot USD price) | — |
| `data.change` | `value:number` (% change) | `window`: `5m`\|`1h`\|`6h`\|`24h` |
| `data.liquidity` | `value:number` USD | — |
| `data.volume24h` | `value:number` USD | — |
| `data.pair_age` | `value:number` minutes | — |
| `data.candles` | `open,high,low,close,volume:series` | `timeframe`: `1m`\|`5m`\|`15m`\|`1h`\|`4h`\|`1d` |

### Indicators (over a series)
| type | inputs | outputs | params |
|---|---|---|---|
| `ind.sma` | `series` | `series`, `value` | `period` 1–500 |
| `ind.ema` | `series` | `series`, `value` | `period` 1–500 (seed = SMA of first `period`) |
| `ind.rsi` | `series` | `series`, `value` | `period` 2–200 (Wilder) |
| `ind.bbands` | `series` | `upper,middle,lower:series`, `upper_value,middle_value,lower_value` | `period`, `stddev` (population) |
| `ind.atr` | `high,low,close` | `series`, `value` | `period` (Wilder) |
| `ind.macd` | `series` | `macd,signal,hist:series` + `_value`s | `fast` 12, `slow` 26, `signal` 9 |
| `ind.highest` / `ind.lowest` | `series` | `value` | `period` |
| `ind.roc` | `series` | `series`, `value` (% over `period` bars) | `period` |
| `ind.vwap` | `high,low,close,volume` | `series`, `value` (rolling) | `period` |

Warmup: an indicator series is shorter than its input by its warmup; if it is empty
the `value` is unknown.

### Position and account (from the ledger, never self-reported)
| type | outputs |
|---|---|
| `pos.state` | `open:bool`, `quantity`, `avg_cost_usd`, `value_usd`, `unrealized_pct`, `peak_since_entry_usd`, `drawdown_from_peak_pct`, `minutes_in_position`, `minutes_since_last_fill` (numbers; unknown when flat where meaningless) |
| `acct.state` | `cash_usd`, `equity_usd`, `daily_realized_pnl_usd`, `open_positions` |
| `time.now` | `minute_of_day` (UTC 0–1439), `weekday` (0=Mon) |
| `var.get` | `value:number` — params `name`, `default` (number or unknown) |

### Math and logic
| type | inputs | outputs | params |
|---|---|---|---|
| `math.const` | — | `value:number` | `value` |
| `math.add` `math.sub` `math.mul` `math.div` | `a,b:number` | `value` | — (`div` by 0 → unknown) |
| `math.min` `math.max` | `a,b` | `value` | — |
| `math.abs` | `a` | `value` | — |
| `math.compare` | `a,b:number` (b may default) | `out:bool` | `op`: `>` `>=` `<` `<=` `==` `!=`, `b` |
| `math.between` | `x,lo,hi` | `out:bool` | inclusive |
| `logic.and` `logic.or` | `a,b:bool` (+ optional `c,d`) | `out:bool` | — |
| `logic.not` | `a:bool` | `out:bool` | — |
| `logic.cross_above` `logic.cross_below` | `a,b:series` | `out:bool` | crossed on the last closed bar |

### Flow
| type | inputs | outputs |
|---|---|---|
| `flow.branch` | `in:exec`, `cond:bool` | `true:exec`, `false:exec` |
| `flow.sequence` | `in:exec` | `then0..then3:exec` (in order) |
| `flow.gate_cooldown` | `in:exec` | `out:exec` passes only if `minutes_since_last_fill ≥ minutes` (unknown last fill = pass) — params `minutes` |

### Actions (exec in; `out:exec` continues the chain)
| type | params | effect |
|---|---|---|
| `act.buy` | exactly one of `amount_usd` / `pct_cash` (0–100]; optional `max_position_usd` | order buy; amount clipped to `max_position_usd − value_usd`; ≤ 0 → `MAX_POSITION` hold |
| `act.sell` | `pct_position` (0–100], default 100 | order sell of that fraction; flat → `NO_POSITION` hold |
| `act.notify` | `message` ≤ 200 chars, `level` info\|warn | notify effect (persisted in the run log) |
| `act.set_var` | `name`; input `value:number` | set_var effect, applied **after** the tick is recorded |

### Composite guards (data nodes that encode common exits without spaghetti)
| type | outputs | params |
|---|---|---|
| `guard.take_profit` | `hit:bool` | `pct` |
| `guard.stop_loss` | `hit:bool` | `pct` |
| `guard.trailing_stop` | `armed:bool`, `hit:bool` | `activate_pct`, `giveback_pct` — armed once peak ≥ entry·(1+activate); hit when price ≤ peak·(1−giveback) while armed |

## 6. Validation (the "compile" step)

`validate(document) -> ValidationReport{ok, errors[], warnings[], warmup_bars{tf:n}}`.
Each issue has `code`, `message`, `node` (and `port` when relevant). Errors block save
and run; warnings do not.

Errors: `GRAPH_SCHEMA` (shape, limits, ids), `NODE_TYPE_UNKNOWN`, `PARAM_INVALID`
(range/type/unknown key — typos never become defaults), `PORT_UNKNOWN`,
`PIN_TYPE_MISMATCH`, `INPUT_MULTIPLE`, `EXEC_FANOUT`, `INPUT_REQUIRED`
(unconnected required input), `CYCLE` (any cycle, data or exec), `NO_EVENT`
(no `event.tick`), `NO_ACTION` (no reachable order action), `ACTION_UNREACHABLE`
(an action not reachable from an event, error because it silently never fires).

Warnings: `NODE_ORPHAN` (data node feeding nothing), `WARMUP_LONG` (warmup > 500
bars), `NO_EXIT` (buys reachable but no sell reachable), `DUPLICATE_ORDER_PATH` (two
order actions reachable on the same exec path — only the first fires).

`warmup_bars` is the max bars each timeframe needs; the engine requests that many and the
UI shows it.

## 7. Persistence and runtime

- Strategy row: `kind = "graph"`, `params = {"graph": <document>, "interval_minutes":
  n, "max_position_usd": "…"}`. Validation runs on create/patch; an invalid graph is
  `422 STRATEGY_GRAPH_INVALID` with the report.
- The API composition root wires a `GraphContextBuilder` into the engine. It builds the
  `EvalContext` from the live snapshot, closed candles
  (`apps/api/src/ezchain_api/services/candles.py`), the ledger-replayed position and the
  paper account. The scheduler then calls the one evaluator and maps the decision onto
  the existing `OrderRequest` path (risk → quote → order → fill → journal). Nothing about
  the single execution path changes.
- A buy carries `amount_usd`. A sell carries `sell_fraction × current position value`
  (a fraction of 100 is a full exit) and is capped at that value, so it can never
  oversell. A hold, an unknown value or a rejected order is logged with its own reason;
  it is never recorded as a filled trade.
- Run log `inputs` stores `{"kind": "graph", "graph_trace": …, "effects": …,
  "context_digest": …, "graph_vars": …}` so a run names the exact context it was made
  from and the editor can show the last real values on wires.
- Variables are carried in the append-only run log (`inputs.graph_vars`) and read back
  from the strategy's latest run. This slice has no separate mutable `strategy_state`
  table: the row that records the tick also records the state it produced.
- If the context builder is absent or fails (no runtime, no candles, ledger failure),
  the run is a logged hold (`GRAPH_RUNTIME_UNAVAILABLE` / `GRAPH_CONTEXT_UNAVAILABLE`)
  and the decision window is not consumed. No order is placed.

## 8. Backtest

`run_backtest(document, candles_by_tf, *, primary_timeframe, fee_bps, slippage_bps,
starting_cash_usd, snapshot_template, max_position_usd)` replays the same evaluator bar
by bar with an `EvalContext` built from bars ≤ t. Fills happen at the **next** bar's open
(no same-bar fill), fee and slippage applied, and the result is immutable: equity curve,
drawdown, trades, metrics (net PnL, return %, max drawdown %, win rate, profit factor,
trades, closed trades, exposure %, Sharpe on bar returns, fees) and per-node hit counts.
The harness declares data source and fill model in the result. Only the per-bar close is
known during a replay: current liquidity, volume, change and pair-age fields are not
injected into historical ticks, so those nodes stay unknown rather than leak look-ahead.
Backtests are labelled `BACKTEST`, never `PAPER` or `LIVE`.

## 9. HTTP contract (API ↔ web)

All bodies are JSON; Decimals are strings; unknown is `null`. Reads are open, mutations
need the bearer token (the Next proxy injects it).

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/v1/graph/catalogue` | → the core `catalogue_json()`: `{schema: "ezchain.graph/1", pin_types: ["exec", "bool", "number", "series"], categories: [{id, title}], node_types: [{type, category, title, description, inputs: [{name, type, required, default_param}], outputs: [{name, type}], params: [{name, kind, required, default, min, max, exclusive_min, choices, max_length}]}]}` |
| POST | `/api/v1/graph/validate` | `{graph}` → `ValidationReport {ok, errors: [{code, message, node, port}], warnings: [...], warmup_bars: {tf: n}}`. A JSON object always returns 200, even when the graph is invalid. A missing/non-object `graph` is `422 {code: "STRATEGY_GRAPH_INVALID", message}`. |
| POST | `/api/v1/graph/dry-run` | `{graph, chain, token_address, max_position_usd?}` → `{decision: {action, amount_usd, sell_fraction, reason, effects}, trace: {values, exec, unknown}, context: {now, price_usd, candles: {tf: {bars, last_close_time, stale}}, position, account}, mode: "DRY_RUN"}` — evaluates against live data, **never** places an order. An invalid graph is `422 STRATEGY_GRAPH_INVALID` with the report. |
| POST | `/api/v1/backtests` | `{graph \| strategy_id, chain, token_address, timeframe, bars (≤ 1000), fee_bps, slippage_bps, starting_cash_usd}` → `201 Backtest {id, created_at, label: "BACKTEST", request, data_source, fill_model, metrics, equity: [{t, equity_usd, drawdown_pct}], trades: [{entry_time, exit_time, side, qty, entry_price, exit_price, pnl_usd, pnl_pct, fees_usd, reason}], node_hits, final_cash_usd, final_quantity, final_equity_usd, warmup_bars}`. `trades` are closed round trips; an open position is carried by `final_quantity`, not invented into a trade. |
| GET | `/api/v1/backtests?strategy_id=` | → list of `{id, created_at, request, metrics}` (newest first, ≤ 100) |
| GET | `/api/v1/backtests/{id}` | → full `Backtest` (`404 BACKTEST_NOT_FOUND`) |
| GET | `/api/v1/strategies/{id}/performance` | → `{strategy_id, label: "PAPER", realized_pnl_usd, unrealized_pnl_usd, fees_usd, trades, wins, losses, win_rate_pct, open_quantity, avg_cost_usd, mark_usd, equity_curve: [{t, pnl_usd}]}` — `trades` counts realized sell fills, `equity_curve` is cumulative realized PnL after fees at each fill, all computed only from orders whose `strategy_id` matches. The latest snapshot marks the open position; unknown mark → `mark_usd` and `unrealized_pnl_usd` are `null`. |

Strategy create/patch with `kind: "graph"` uses the existing `/api/v1/strategies`
endpoints; `params.graph` carries the document and is validated on create **and** patch.
Runs (`/strategies/{id}/runs`) carry `inputs.graph_trace`, `inputs.effects`,
`inputs.context_digest` and `inputs.graph_vars` for graph strategies.

The catalogue is the platform's single node table (no duplicated list in the API or in
the evaluator). The web builder's live adapter is a separate UI change; in fixture mode
it still renders its own labelled fixture catalogue.
