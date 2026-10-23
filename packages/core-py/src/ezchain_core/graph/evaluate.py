"""The one graph evaluator: one tick, one frozen context, one decision.

Semantics come straight from ``docs/STRATEGY-GRAPH.md`` section 4:

* data nodes are pure and memoised (each node at most once per tick);
* exec runs depth-first from every ``event.tick``, in document order;
* at most one order effect survives a tick, the first one;
* unknown is not zero: Kleene logic everywhere, and a branch on an unknown condition
  takes neither path.

The result is deterministic: same document + same context -> byte-identical ``to_dict()``.
No clock, no randomness, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any, Literal

from ..types import TokenSnapshot
from . import indicators
from .catalogue import ParsedParams, get_node_spec, parse_params
from .schema import Edge, GraphDocument, GraphSchemaError, Node, parse_document
from .validate import validate

#: Exec steps allowed per tick. v1 has no loops (a cycle is invalid), so this is a fence.
MAX_EXEC_STEPS = 1000

ZERO = Decimal(0)
HUNDRED = Decimal(100)

Series = tuple[Decimal, ...]
Value = Decimal | bool | Series | None
Action = Literal["buy", "sell", "hold"]

#: Hold reasons from most to least specific. The first one recorded wins.
HOLD_PRIORITY: tuple[str, ...] = (
    "UNKNOWN_CONDITION",
    "MAX_POSITION",
    "NO_POSITION",
    "GRAPH_NO_ACTION",
)

_ORDER_ACTIONS = frozenset({"act.buy", "act.sell"})
_ACTION_NODES = frozenset({"act.buy", "act.sell", "act.notify", "act.set_var"})


@dataclass(frozen=True)
class Candle:
    """One closed bar. ``volume`` may be unknown (``None``)."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_time": self.open_time.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": None if self.volume is None else str(self.volume),
        }


@dataclass(frozen=True)
class EvalContext:
    """Everything one tick may look at. Frozen: the evaluator cannot mutate it.

    Unknown values are ``None`` — never zero. ``position_quantity`` defaults to flat, which
    is a fact, not a guess.
    """

    now: datetime
    snapshot: TokenSnapshot
    candles: Mapping[str, tuple[Candle, ...]] = field(default_factory=dict)
    position_quantity: Decimal = ZERO
    position_avg_cost_usd: Decimal | None = None
    position_value_usd: Decimal | None = None
    cash_usd: Decimal | None = None
    equity_usd: Decimal | None = None
    daily_realized_pnl_usd: Decimal | None = None
    open_positions: int | None = None
    last_fill_at: datetime | None = None
    entry_at: datetime | None = None
    peak_since_entry_usd: Decimal | None = None
    max_position_usd: Decimal | None = None
    vars: Mapping[str, Decimal | None] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphDecision:
    """The outcome of one tick. ``effects`` are intents, not executed orders.

    ``trace`` is ``{"values": {node_id: {port: value}}, "exec": [node_id, ...],
    "unknown": [node_id, ...]}`` — value chips for the wires, the exec order, and the
    branches whose condition was unknown (recorded with UNKNOWN_CONDITION).
    """

    action: Action
    amount_usd: Decimal | None
    sell_fraction: Decimal | None
    reason: str
    effects: tuple[dict[str, Any], ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "amount_usd": None if self.amount_usd is None else str(self.amount_usd),
            "sell_fraction": None if self.sell_fraction is None else str(self.sell_fraction),
            "reason": self.reason,
            "effects": [_json_safe(effect) for effect in self.effects],
            "trace": _json_safe(self.trace),
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _minutes_between(start: datetime, end: datetime) -> Decimal:
    delta = _aware(end) - _aware(start)
    total_microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(total_microseconds) / Decimal(60_000_000)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = indicators.PRECISION
        return numerator / denominator


def _trace_value(port_type: str, value: Value) -> str | bool | None:
    """Trace chips: bools stay bools, numbers are stringified Decimals, series show their
    last element (the value a ``number`` consumer would read)."""
    if value is None:
        return None
    if port_type == "bool":
        return value if isinstance(value, bool) else None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return str(value[-1]) if value else None
    return None


class _Evaluator:
    """Single-tick evaluator. Construct, call :meth:`run`, discard."""

    def __init__(self, doc: GraphDocument, ctx: EvalContext) -> None:
        self.doc = doc
        self.ctx = ctx
        self.nodes: dict[str, Node] = doc.index()
        self.incoming: dict[tuple[str, str], list[Edge]] = {}
        self.outgoing: dict[tuple[str, str], list[Edge]] = {}
        for edge in doc.edges:
            self.incoming.setdefault((edge.to.node, edge.to.port), []).append(edge)
            self.outgoing.setdefault((edge.from_.node, edge.from_.port), []).append(edge)
        self.params: dict[str, ParsedParams] = {}
        for node in doc.nodes:
            values, _errors = parse_params(node.type, node.params)
            self.params[node.id] = values
        self.memo: dict[str, dict[str, Value]] = {}
        self.computing: set[str] = set()
        self.exec_trace: list[str] = []
        self.effects: list[dict[str, Any]] = []
        self.hold_reasons: list[str] = []
        self.unknown_conditions: list[str] = []
        self.order: dict[str, Any] | None = None

    # -- context helpers ---------------------------------------------------------------
    def _price(self) -> Decimal | None:
        snapshot = self.ctx.snapshot
        if snapshot.stale:
            return None
        return snapshot.price_usd

    def _unknown(self, node: Node) -> dict[str, Value]:
        spec = get_node_spec(node.type)
        if spec is None:
            return {}
        return {port.name: None for port in spec.outputs}

    # -- input resolution --------------------------------------------------------------
    def _input(self, node: Node, port_name: str) -> Value:
        spec = get_node_spec(node.type)
        port = spec.input(port_name) if spec is not None else None
        edges = self.incoming.get((node.id, port_name))
        if not edges:
            if port is not None and port.default_param is not None:
                stored = self.params.get(node.id, {}).get(port.default_param)
                if isinstance(stored, Decimal):
                    return stored
                if isinstance(stored, int) and not isinstance(stored, bool):
                    return Decimal(stored)
            return None
        edge = edges[0]
        source = self.nodes[edge.from_.node]
        raw = self.node_values(source.id).get(edge.from_.port)
        source_spec = get_node_spec(source.type)
        source_port = source_spec.output(edge.from_.port) if source_spec is not None else None
        if (
            source_port is not None
            and source_port.type == "series"
            and port is not None
            and port.type == "number"
        ):
            # The only implicit cast: a series read as its last value.
            series = raw if isinstance(raw, tuple) else ()
            return series[-1] if series else None
        return raw

    def _number(self, node: Node, port_name: str) -> Decimal | None:
        value = self._input(node, port_name)
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return Decimal(value)
        return None

    def _flag(self, node: Node, port_name: str) -> bool | None:
        value = self._input(node, port_name)
        return value if isinstance(value, bool) else None

    def _series_input(self, node: Node, port_name: str) -> Series | None:
        value = self._input(node, port_name)
        return value if isinstance(value, tuple) else None

    # -- node evaluation ---------------------------------------------------------------
    def node_values(self, node_id: str) -> dict[str, Value]:
        cached = self.memo.get(node_id)
        if cached is not None:
            return cached
        if node_id in self.computing:
            # A cycle is invalid; if one reaches here, every output stays unknown.
            return {}
        node = self.nodes[node_id]
        self.computing.add(node_id)
        try:
            values = self._compute(node)
        finally:
            self.computing.discard(node_id)
        self.memo[node_id] = values
        return values

    def _compute(self, node: Node) -> dict[str, Value]:
        spec = get_node_spec(node.type)
        if spec is None:
            return {}
        params = self.params.get(node.id, {})
        node_type = node.type
        if node_type.startswith("data."):
            return self._data(node, params)
        if node_type.startswith("ind."):
            return self._indicator(node, params)
        if node_type == "pos.state":
            return self._position_state()
        if node_type == "acct.state":
            return self._account_state()
        if node_type == "time.now":
            now = _aware(self.ctx.now)
            return {
                "minute_of_day": Decimal(now.hour * 60 + now.minute),
                "weekday": Decimal(now.weekday()),
            }
        if node_type == "var.get":
            name = params.get("name")
            default = params.get("default")
            stored = self.ctx.vars.get(name) if isinstance(name, str) else None
            if stored is not None:
                return {"value": stored}
            return {"value": default if isinstance(default, Decimal) else None}
        if node_type.startswith("math."):
            return self._math(node, params)
        if node_type.startswith("logic."):
            return self._logic(node)
        if node_type.startswith("guard."):
            return self._guard(node, params)
        return self._unknown(node)

    # -- market data -------------------------------------------------------------------
    def _data(self, node: Node, params: ParsedParams) -> dict[str, Value]:
        snapshot = self.ctx.snapshot
        stale = snapshot.stale
        if node.type == "data.price":
            return {"value": self._price()}
        if node.type == "data.change":
            window = params.get("window")
            changes: dict[str, Decimal | None] = {
                "5m": snapshot.price_change_m5,
                "1h": snapshot.price_change_h1,
                "6h": snapshot.price_change_h6,
                "24h": snapshot.price_change_h24,
            }
            return {"value": None if stale or not isinstance(window, str) else changes.get(window)}
        if node.type == "data.liquidity":
            return {"value": None if stale else snapshot.liquidity_usd}
        if node.type == "data.volume24h":
            return {"value": None if stale else snapshot.volume_24h_usd}
        if node.type == "data.pair_age":
            created = snapshot.pair_created_at
            if stale or created is None:
                return {"value": None}
            return {"value": _minutes_between(created, self.ctx.now)}
        if node.type == "data.candles":
            timeframe = params.get("timeframe")
            bars = self.ctx.candles.get(timeframe, ()) if isinstance(timeframe, str) else ()
            return {
                "open": tuple(bar.open for bar in bars),
                "high": tuple(bar.high for bar in bars),
                "low": tuple(bar.low for bar in bars),
                "close": tuple(bar.close for bar in bars),
                # A series cannot hold unknown elements. One unknown volume makes the
                # whole volume series unknown rather than silently shifting values.
                "volume": ()
                if any(bar.volume is None for bar in bars)
                else tuple(bar.volume for bar in bars if bar.volume is not None),
            }
        return self._unknown(node)

    # -- indicators --------------------------------------------------------------------
    def _indicator(self, node: Node, params: ParsedParams) -> dict[str, Value]:
        node_type = node.type
        period = params.get("period")
        if not isinstance(period, int):
            return self._unknown(node)

        if node_type in {"ind.sma", "ind.ema", "ind.rsi", "ind.highest", "ind.lowest", "ind.roc"}:
            source = self._series_input(node, "series")
            if source is None:
                return self._unknown(node)
            series: list[Decimal]
            if node_type == "ind.sma":
                series = indicators.sma(source, period)
            elif node_type == "ind.ema":
                series = indicators.ema(source, period)
            elif node_type == "ind.rsi":
                series = indicators.rsi(source, period)
            elif node_type == "ind.highest":
                series = indicators.highest(source, period)
            elif node_type == "ind.lowest":
                series = indicators.lowest(source, period)
            else:
                series = indicators.roc(source, period)
            return {"series": tuple(series), "value": series[-1] if series else None}

        if node_type == "ind.bbands":
            source = self._series_input(node, "series")
            deviation = params.get("stddev")
            if source is None or not isinstance(deviation, Decimal):
                return self._unknown(node)
            upper, middle, lower = indicators.bbands(source, period, deviation)
            return {
                "upper": tuple(upper),
                "middle": tuple(middle),
                "lower": tuple(lower),
                "upper_value": upper[-1] if upper else None,
                "middle_value": middle[-1] if middle else None,
                "lower_value": lower[-1] if lower else None,
            }

        if node_type == "ind.atr":
            high = self._series_input(node, "high")
            low = self._series_input(node, "low")
            close = self._series_input(node, "close")
            if high is None or low is None or close is None:
                return self._unknown(node)
            series = indicators.atr(high, low, close, period)
            return {"series": tuple(series), "value": series[-1] if series else None}

        if node_type == "ind.macd":
            source = self._series_input(node, "series")
            fast = params.get("fast")
            slow = params.get("slow")
            signal = params.get("signal")
            if (
                source is None
                or not isinstance(fast, int)
                or not isinstance(slow, int)
                or not isinstance(signal, int)
            ):
                return self._unknown(node)
            macd_series, signal_series, hist = indicators.macd(source, fast, slow, signal)
            return {
                "macd": tuple(macd_series),
                "signal": tuple(signal_series),
                "hist": tuple(hist),
                "macd_value": macd_series[-1] if macd_series else None,
                "signal_value": signal_series[-1] if signal_series else None,
                "hist_value": hist[-1] if hist else None,
            }

        if node_type == "ind.vwap":
            high = self._series_input(node, "high")
            low = self._series_input(node, "low")
            close = self._series_input(node, "close")
            volume = self._series_input(node, "volume")
            if high is None or low is None or close is None or volume is None:
                return self._unknown(node)
            series = indicators.vwap(high, low, close, volume, period)
            return {"series": tuple(series), "value": series[-1] if series else None}

        return self._unknown(node)

    # -- position and account ----------------------------------------------------------
    def _position_state(self) -> dict[str, Value]:
        ctx = self.ctx
        quantity = ctx.position_quantity
        price = self._price()
        value = ctx.position_value_usd
        if value is None and price is not None:
            value = quantity * price
        average = ctx.position_avg_cost_usd
        unrealized: Decimal | None = None
        if quantity > 0 and average is not None and average > 0 and price is not None:
            unrealized = (_ratio(price, average) - 1) * HUNDRED
        peak = ctx.peak_since_entry_usd
        drawdown: Decimal | None = None
        if price is not None and peak is not None and peak > 0:
            drawdown = (_ratio(price, peak) - 1) * HUNDRED
        return {
            "open": quantity > 0,
            "quantity": quantity,
            "avg_cost_usd": average if quantity > 0 else None,
            "value_usd": value,
            "unrealized_pct": unrealized,
            "peak_since_entry_usd": peak,
            "drawdown_from_peak_pct": drawdown,
            "minutes_in_position": None
            if ctx.entry_at is None
            else _minutes_between(ctx.entry_at, ctx.now),
            "minutes_since_last_fill": None
            if ctx.last_fill_at is None
            else _minutes_between(ctx.last_fill_at, ctx.now),
        }

    def _account_state(self) -> dict[str, Value]:
        ctx = self.ctx
        return {
            "cash_usd": ctx.cash_usd,
            "equity_usd": ctx.equity_usd,
            "daily_realized_pnl_usd": ctx.daily_realized_pnl_usd,
            "open_positions": None if ctx.open_positions is None else Decimal(ctx.open_positions),
        }

    # -- math --------------------------------------------------------------------------
    def _math(self, node: Node, params: ParsedParams) -> dict[str, Value]:
        node_type = node.type
        if node_type == "math.const":
            value = params.get("value")
            return {"value": value if isinstance(value, Decimal) else None}
        if node_type == "math.abs":
            a = self._number(node, "a")
            return {"value": None if a is None else abs(a)}
        if node_type == "math.between":
            x = self._number(node, "x")
            low = self._number(node, "lo")
            high = self._number(node, "hi")
            if x is None or low is None or high is None:
                return {"out": None}
            return {"out": low <= x <= high}
        if node_type == "math.compare":
            a = self._number(node, "a")
            b = self._number(node, "b")
            op = params.get("op")
            if a is None or b is None or not isinstance(op, str):
                return {"out": None}
            result = {
                ">": a > b,
                ">=": a >= b,
                "<": a < b,
                "<=": a <= b,
                "==": a == b,
                "!=": a != b,
            }.get(op)
            return {"out": result}

        a = self._number(node, "a")
        b = self._number(node, "b")
        if a is None or b is None:
            return {"value": None}
        if node_type == "math.add":
            return {"value": a + b}
        if node_type == "math.sub":
            return {"value": a - b}
        if node_type == "math.mul":
            return {"value": a * b}
        if node_type == "math.div":
            if b == 0:
                return {"value": None}
            return {"value": _ratio(a, b)}
        if node_type == "math.min":
            return {"value": min(a, b)}
        if node_type == "math.max":
            return {"value": max(a, b)}
        return self._unknown(node)

    # -- logic -------------------------------------------------------------------------
    def _logic(self, node: Node) -> dict[str, Value]:
        node_type = node.type
        if node_type in {"logic.and", "logic.or"}:
            values: list[bool | None] = []
            spec = get_node_spec(node_type)
            assert spec is not None
            for port in spec.inputs:
                connected = bool(self.incoming.get((node.id, port.name)))
                if port.required or connected:
                    values.append(self._flag(node, port.name))
            if node_type == "logic.and":
                if any(value is False for value in values):
                    return {"out": False}
                if any(value is None for value in values):
                    return {"out": None}
                return {"out": True}
            if any(value is True for value in values):
                return {"out": True}
            if any(value is None for value in values):
                return {"out": None}
            return {"out": False}

        if node_type == "logic.not":
            value = self._flag(node, "a")
            return {"out": None if value is None else not value}

        if node_type in {"logic.cross_above", "logic.cross_below"}:
            a = self._series_input(node, "a")
            b = self._series_input(node, "b")
            if a is None or b is None or len(a) < 2 or len(b) < 2:
                return {"out": None}
            previous_a, previous_b = a[-2], b[-2]
            last_a, last_b = a[-1], b[-1]
            if node_type == "logic.cross_above":
                return {"out": previous_a <= previous_b and last_a > last_b}
            return {"out": previous_a >= previous_b and last_a < last_b}

        return self._unknown(node)

    # -- guards ------------------------------------------------------------------------
    def _guard(self, node: Node, params: ParsedParams) -> dict[str, Value]:
        ctx = self.ctx
        price = self._price()
        average = ctx.position_avg_cost_usd
        if price is None or average is None or average <= 0 or ctx.position_quantity <= 0:
            return self._unknown(node)

        if node.type == "guard.take_profit":
            pct = params.get("pct")
            if not isinstance(pct, Decimal):
                return self._unknown(node)
            return {"hit": (_ratio(price, average) - 1) * HUNDRED >= pct}

        if node.type == "guard.stop_loss":
            pct = params.get("pct")
            if not isinstance(pct, Decimal):
                return self._unknown(node)
            return {"hit": (_ratio(price, average) - 1) * HUNDRED <= -pct}

        if node.type == "guard.trailing_stop":
            activate = params.get("activate_pct")
            giveback = params.get("giveback_pct")
            peak = ctx.peak_since_entry_usd
            if (
                not isinstance(activate, Decimal)
                or not isinstance(giveback, Decimal)
                or peak is None
            ):
                return self._unknown(node)
            armed = peak >= average * (1 + activate / HUNDRED)
            if not armed:
                return {"armed": False, "hit": False}
            return {"armed": True, "hit": price <= peak * (1 - giveback / HUNDRED)}

        return self._unknown(node)

    # -- exec --------------------------------------------------------------------------
    def _run_exec(self) -> None:
        for node in self.doc.nodes:
            if node.type == "event.tick":
                self._walk(node.id)

    def _walk(self, node_id: str) -> None:
        if len(self.exec_trace) >= MAX_EXEC_STEPS:
            return
        node = self.nodes.get(node_id)
        if node is None:
            return
        spec = get_node_spec(node.type)
        if spec is None:
            return
        self.exec_trace.append(node_id)
        params = self.params.get(node_id, {})
        if node.type in _ACTION_NODES:
            self._action(node, params)
        for child in self._exec_children(node, params):
            self._walk(child)

    def _exec_children(self, node: Node, params: ParsedParams) -> list[str]:
        node_type = node.type
        if node_type == "flow.branch":
            condition = self._flag(node, "cond")
            if condition is None:
                # Fail closed: neither branch runs, and both the reason and the trace say
                # which node could not be decided (contract section 3).
                self.hold_reasons.append("UNKNOWN_CONDITION")
                if node.id not in self.unknown_conditions:
                    self.unknown_conditions.append(node.id)
                return []
            port = "true" if condition else "false"
            return [edge.to.node for edge in self.outgoing.get((node.id, port), ())]
        if node_type == "flow.sequence":
            children: list[str] = []
            for port in ("then0", "then1", "then2", "then3"):
                children.extend(edge.to.node for edge in self.outgoing.get((node.id, port), ()))
            return children
        if node_type == "flow.gate_cooldown":
            minutes = params.get("minutes")
            if isinstance(minutes, int) and self.ctx.last_fill_at is not None:
                since = _minutes_between(self.ctx.last_fill_at, self.ctx.now)
                if since < Decimal(minutes):
                    return []
            return [edge.to.node for edge in self.outgoing.get((node.id, "out"), ())]
        return [edge.to.node for edge in self.outgoing.get((node.id, "out"), ())]

    def _action(self, node: Node, params: ParsedParams) -> None:
        if node.type == "act.buy":
            self._buy(node, params)
        elif node.type == "act.sell":
            self._sell(node, params)
        elif node.type == "act.notify":
            message = params.get("message")
            level = params.get("level")
            if isinstance(message, str):
                self.effects.append(
                    {
                        "kind": "notify",
                        "level": level if isinstance(level, str) else "info",
                        "message": message,
                        "node": node.id,
                    }
                )
        elif node.type == "act.set_var":
            name = params.get("name")
            value = self._number(node, "value")
            if isinstance(name, str) and value is not None:
                self.effects.append(
                    {"kind": "set_var", "name": name, "value": value, "node": node.id}
                )

    def _position_value(self) -> Decimal | None:
        value = self.ctx.position_value_usd
        if value is not None:
            return value
        price = self._price()
        if price is None:
            return None
        return self.ctx.position_quantity * price

    def _buy(self, node: Node, params: ParsedParams) -> None:
        amount = params.get("amount_usd")
        if not isinstance(amount, Decimal):
            pct = params.get("pct_cash")
            cash = self.ctx.cash_usd
            if not isinstance(pct, Decimal) or cash is None:
                self.hold_reasons.append("GRAPH_NO_ACTION")
                return
            amount = cash * pct / HUNDRED
        if amount <= 0:
            self.hold_reasons.append("MAX_POSITION")
            return

        cap = params.get("max_position_usd")
        if not isinstance(cap, Decimal):
            cap = self.ctx.max_position_usd
        if isinstance(cap, Decimal):
            value = self._position_value()
            if value is None:
                # Cannot prove there is headroom -> do not buy.
                self.hold_reasons.append("GRAPH_NO_ACTION")
                return
            headroom = cap - value
            if headroom <= 0:
                self.hold_reasons.append("MAX_POSITION")
                return
            amount = min(amount, headroom)

        self._add_order(
            {
                "kind": "order",
                "side": "buy",
                "amount_usd": amount,
                "sell_fraction": None,
                "node": node.id,
            }
        )

    def _sell(self, node: Node, params: ParsedParams) -> None:
        quantity = self.ctx.position_quantity
        if quantity <= 0:
            self.hold_reasons.append("NO_POSITION")
            return
        pct = params.get("pct_position", HUNDRED)
        if not isinstance(pct, Decimal) or pct <= 0:
            self.hold_reasons.append("GRAPH_NO_ACTION")
            return
        fraction = pct / HUNDRED
        value = self._position_value()
        self._add_order(
            {
                "kind": "order",
                "side": "sell",
                "amount_usd": None if value is None else value * fraction,
                "sell_fraction": fraction,
                "node": node.id,
            }
        )

    def _add_order(self, effect: dict[str, Any]) -> None:
        if self.order is not None:
            self.effects.append(
                {
                    "kind": "suppressed_order",
                    "side": effect["side"],
                    "node": effect["node"],
                    "reason": "SUPPRESSED_ONE_ORDER_PER_TICK",
                }
            )
            return
        self.order = effect
        self.effects.append(effect)

    # -- result ------------------------------------------------------------------------
    def _reason(self) -> str:
        if self.order is not None:
            return "GRAPH_BUY" if self.order["side"] == "buy" else "GRAPH_SELL"
        for candidate in HOLD_PRIORITY:
            if candidate in self.hold_reasons:
                return candidate
        return "GRAPH_NO_ACTION"

    def _trace(self) -> dict[str, Any]:
        values: dict[str, dict[str, str | bool | None]] = {}
        for node in self.doc.nodes:
            cached = self.memo.get(node.id)
            if cached is None:
                continue
            spec = get_node_spec(node.type)
            if spec is None:
                continue
            ports: dict[str, str | bool | None] = {}
            for port in spec.outputs:
                if port.type == "exec":
                    continue
                ports[port.name] = _trace_value(port.type, cached.get(port.name))
            values[node.id] = ports
        return {
            "values": values,
            "exec": list(self.exec_trace),
            "unknown": list(self.unknown_conditions),
        }

    def run(self) -> GraphDecision:
        self._run_exec()
        order = self.order
        action: Action = "hold"
        if order is not None:
            action = "buy" if order["side"] == "buy" else "sell"
        return GraphDecision(
            action=action,
            amount_usd=None if order is None else order["amount_usd"],
            sell_fraction=None if order is None else order["sell_fraction"],
            reason=self._reason(),
            effects=tuple(self.effects),
            trace=self._trace(),
        )


def _invalid() -> GraphDecision:
    return GraphDecision(
        action="hold",
        amount_usd=None,
        sell_fraction=None,
        reason="INVALID_GRAPH",
        effects=(),
        trace={"values": {}, "exec": [], "unknown": []},
    )


def evaluate_graph(doc: object, ctx: EvalContext) -> GraphDecision:
    """Evaluate one tick. An invalid document yields ``INVALID_GRAPH``, never an exception."""
    try:
        document = parse_document(doc)
    except GraphSchemaError:
        return _invalid()
    if not validate(document).ok:
        return _invalid()
    return _Evaluator(document, ctx).run()


__all__ = [
    "HOLD_PRIORITY",
    "MAX_EXEC_STEPS",
    "Action",
    "Candle",
    "EvalContext",
    "GraphDecision",
    "Series",
    "Value",
    "evaluate_graph",
]
