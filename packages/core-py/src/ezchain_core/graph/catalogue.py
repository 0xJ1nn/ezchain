"""The single node catalogue for ``ezchain.graph/1``.

One table, one source of truth: the palette the web UI renders, the validator's idea of
which ports exist and the evaluator's parameter defaults all come from ``NODE_SPECS``.
Adding a node type means adding one entry here (and its evaluation in ``evaluate.py``).

Parameter kinds:

``decimal``  parsed to ``Decimal``; never a float on the money path
``int``      parsed to ``int`` (integral decimals accepted, ``3.5`` is not)
``enum``     one of ``choices``
``str``      text, optionally bounded by ``max_length``

Ranges are inclusive; ``exclusive_min`` makes the lower bound strict (``> 0``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

PinType = Literal["exec", "bool", "number", "series"]
ParamKind = Literal["decimal", "int", "enum", "str"]

WINDOWS: tuple[str, ...] = ("5m", "1h", "6h", "24h")
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")

#: Minutes per bar for every supported timeframe. The context builder and the backtester
#: use this to decide which bars are closed at ``now``.
TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

COMPARE_OPS: tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=")
NOTIFY_LEVELS: tuple[str, ...] = ("info", "warn")

CATEGORY_ORDER: tuple[str, ...] = (
    "event",
    "data",
    "indicator",
    "state",
    "logic",
    "flow",
    "action",
    "guard",
)
CATEGORY_TITLES: dict[str, str] = {
    "event": "Events",
    "data": "Market data",
    "indicator": "Indicators",
    "state": "Position & account",
    "logic": "Math & logic",
    "flow": "Flow",
    "action": "Actions",
    "guard": "Guards",
}

#: Node types whose effect is a buy/sell order. Validation and evaluation both need this.
ORDER_ACTIONS: frozenset[str] = frozenset({"act.buy", "act.sell"})


@dataclass(frozen=True)
class PortSpec:
    name: str
    type: PinType
    required: bool = True
    #: Name of the node param that supplies this input when nothing is wired to it.
    default_param: str | None = None


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: ParamKind
    required: bool = False
    default: Decimal | int | str | None = None
    min: Decimal | int | None = None
    max: Decimal | int | None = None
    exclusive_min: bool = False
    choices: tuple[str, ...] = ()
    max_length: int | None = None


@dataclass(frozen=True)
class NodeSpec:
    type: str
    category: str
    title: str
    description: str
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)

    def input(self, name: str) -> PortSpec | None:
        for spec in self.inputs:
            if spec.name == name:
                return spec
        return None

    def output(self, name: str) -> PortSpec | None:
        for spec in self.outputs:
            if spec.name == name:
                return spec
        return None

    def param(self, name: str) -> ParamSpec | None:
        for spec in self.params:
            if spec.name == name:
                return spec
        return None


# --------------------------------------------------------------------------------------
# Table shorthands
# --------------------------------------------------------------------------------------


def _exec_in(name: str = "in") -> PortSpec:
    return PortSpec(name, "exec")


def _exec_out(name: str = "out") -> PortSpec:
    return PortSpec(name, "exec")


def _num_out(name: str = "value") -> PortSpec:
    return PortSpec(name, "number")


def _series_in(name: str = "series", *, required: bool = True) -> PortSpec:
    return PortSpec(name, "series", required=required)


def _number_in(name: str, *, required: bool = True, default_param: str | None = None) -> PortSpec:
    return PortSpec(name, "number", required, default_param)


def _bool_in(name: str, *, required: bool = True) -> PortSpec:
    return PortSpec(name, "bool", required)


def _period(max_value: int = 500, *, minimum: int = 1) -> ParamSpec:
    return ParamSpec("period", "int", required=True, min=minimum, max=max_value)


def _pct(name: str, *, maximum: Decimal | None = Decimal(100)) -> ParamSpec:
    return ParamSpec(
        name, "decimal", required=True, min=Decimal(0), max=maximum, exclusive_min=True
    )


def _series_value_outputs() -> tuple[PortSpec, ...]:
    return (PortSpec("series", "series"), _num_out())


_NODE_SPECS: tuple[NodeSpec, ...] = (
    # -- events -------------------------------------------------------------------------
    NodeSpec(
        "event.tick",
        "event",
        "Every tick",
        "Starts the graph on each scheduler tick for this token.",
        outputs=(_exec_out(),),
    ),
    # -- market data --------------------------------------------------------------------
    NodeSpec(
        "data.price",
        "data",
        "Price",
        "Last USD price from the token snapshot. Unknown while the mark is stale.",
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "data.change",
        "data",
        "Change %",
        "Percent price change over a lookback window.",
        outputs=(_num_out(),),
        params=(ParamSpec("window", "enum", required=True, choices=WINDOWS),),
    ),
    NodeSpec(
        "data.liquidity",
        "data",
        "Liquidity",
        "Pool liquidity in USD.",
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "data.volume24h",
        "data",
        "24h volume",
        "Trailing 24h volume in USD.",
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "data.pair_age",
        "data",
        "Pair age",
        "Minutes since the deepest pair was created. Unknown when the source omits it.",
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "data.candles",
        "data",
        "Candles",
        "Closed bars for one timeframe, oldest first. The forming bar is never visible.",
        outputs=(
            PortSpec("open", "series"),
            PortSpec("high", "series"),
            PortSpec("low", "series"),
            PortSpec("close", "series"),
            PortSpec("volume", "series"),
        ),
        params=(ParamSpec("timeframe", "enum", required=True, choices=TIMEFRAMES),),
    ),
    # -- indicators ---------------------------------------------------------------------
    NodeSpec(
        "ind.sma",
        "indicator",
        "SMA",
        "Simple moving average over the last `period` bars.",
        inputs=(_series_in(),),
        outputs=_series_value_outputs(),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.ema",
        "indicator",
        "EMA",
        "Exponential moving average, seeded with the SMA of the first `period` bars.",
        inputs=(_series_in(),),
        outputs=_series_value_outputs(),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.rsi",
        "indicator",
        "RSI",
        "Wilder relative strength index (0-100).",
        inputs=(_series_in(),),
        outputs=_series_value_outputs(),
        params=(_period(200, minimum=2),),
    ),
    NodeSpec(
        "ind.bbands",
        "indicator",
        "Bollinger bands",
        "SMA middle band with population standard deviation bands.",
        inputs=(_series_in(),),
        outputs=(
            PortSpec("upper", "series"),
            PortSpec("middle", "series"),
            PortSpec("lower", "series"),
            _num_out("upper_value"),
            _num_out("middle_value"),
            _num_out("lower_value"),
        ),
        params=(
            _period(),
            ParamSpec(
                "stddev",
                "decimal",
                required=True,
                min=Decimal(0),
                max=Decimal(10),
            ),
        ),
    ),
    NodeSpec(
        "ind.atr",
        "indicator",
        "ATR",
        "Wilder average true range from high, low and close.",
        inputs=(_series_in("high"), _series_in("low"), _series_in("close")),
        outputs=_series_value_outputs(),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.macd",
        "indicator",
        "MACD",
        "MACD line, signal line and histogram over the same close series.",
        inputs=(_series_in(),),
        outputs=(
            PortSpec("macd", "series"),
            PortSpec("signal", "series"),
            PortSpec("hist", "series"),
            _num_out("macd_value"),
            _num_out("signal_value"),
            _num_out("hist_value"),
        ),
        params=(
            ParamSpec("fast", "int", default=12, min=1, max=200),
            ParamSpec("slow", "int", default=26, min=2, max=500),
            ParamSpec("signal", "int", default=9, min=1, max=200),
        ),
    ),
    NodeSpec(
        "ind.highest",
        "indicator",
        "Highest",
        "Highest close over the last `period` bars.",
        inputs=(_series_in(),),
        outputs=(_num_out(),),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.lowest",
        "indicator",
        "Lowest",
        "Lowest close over the last `period` bars.",
        inputs=(_series_in(),),
        outputs=(_num_out(),),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.roc",
        "indicator",
        "Rate of change",
        "Percent change over `period` bars.",
        inputs=(_series_in(),),
        outputs=_series_value_outputs(),
        params=(_period(),),
    ),
    NodeSpec(
        "ind.vwap",
        "indicator",
        "Rolling VWAP",
        "Volume-weighted average price over the last `period` bars.",
        inputs=(
            _series_in("high"),
            _series_in("low"),
            _series_in("close"),
            _series_in("volume"),
        ),
        outputs=_series_value_outputs(),
        params=(_period(),),
    ),
    # -- position and account -----------------------------------------------------------
    NodeSpec(
        "pos.state",
        "state",
        "Position",
        "Position state derived from the ledger. Flat position -> unknown costs.",
        outputs=(
            PortSpec("open", "bool"),
            _num_out("quantity"),
            _num_out("avg_cost_usd"),
            _num_out("value_usd"),
            _num_out("unrealized_pct"),
            _num_out("peak_since_entry_usd"),
            _num_out("drawdown_from_peak_pct"),
            _num_out("minutes_in_position"),
            _num_out("minutes_since_last_fill"),
        ),
    ),
    NodeSpec(
        "acct.state",
        "state",
        "Account",
        "Paper account totals from the ledger.",
        outputs=(
            _num_out("cash_usd"),
            _num_out("equity_usd"),
            _num_out("daily_realized_pnl_usd"),
            _num_out("open_positions"),
        ),
    ),
    NodeSpec(
        "time.now",
        "state",
        "Clock",
        "UTC clock of the tick.",
        outputs=(_num_out("minute_of_day"), _num_out("weekday")),
    ),
    NodeSpec(
        "var.get",
        "state",
        "Read variable",
        "Reads a strategy variable; unknown until it has been set.",
        outputs=(_num_out(),),
        params=(
            ParamSpec("name", "str", required=True, max_length=40),
            ParamSpec("default", "decimal"),
        ),
    ),
    # -- math and logic -----------------------------------------------------------------
    NodeSpec(
        "math.const",
        "logic",
        "Constant",
        "A fixed number.",
        outputs=(_num_out(),),
        params=(ParamSpec("value", "decimal", required=True),),
    ),
    NodeSpec(
        "math.add",
        "logic",
        "Add",
        "a + b.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.sub",
        "logic",
        "Subtract",
        "a - b.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.mul",
        "logic",
        "Multiply",
        "a * b.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.div",
        "logic",
        "Divide",
        "a / b; division by zero is unknown.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.min",
        "logic",
        "Minimum",
        "The smaller of a and b.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.max",
        "logic",
        "Maximum",
        "The larger of a and b.",
        inputs=(_number_in("a"), _number_in("b")),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.abs",
        "logic",
        "Absolute value",
        "|a|.",
        inputs=(_number_in("a"),),
        outputs=(_num_out(),),
    ),
    NodeSpec(
        "math.compare",
        "logic",
        "Compare",
        "a <op> b, with b falling back to the `b` param when unwired.",
        inputs=(_number_in("a"), _number_in("b", required=False, default_param="b")),
        outputs=(PortSpec("out", "bool"),),
        params=(
            ParamSpec("op", "enum", required=True, choices=COMPARE_OPS),
            ParamSpec("b", "decimal"),
        ),
    ),
    NodeSpec(
        "math.between",
        "logic",
        "Between",
        "Inclusive range test: lo <= x <= hi.",
        inputs=(_number_in("x"), _number_in("lo"), _number_in("hi")),
        outputs=(PortSpec("out", "bool"),),
    ),
    NodeSpec(
        "logic.and",
        "logic",
        "And",
        "Kleene AND: false if any false, else unknown if any unknown, else true.",
        inputs=(
            _bool_in("a"),
            _bool_in("b"),
            _bool_in("c", required=False),
            _bool_in("d", required=False),
        ),
        outputs=(PortSpec("out", "bool"),),
    ),
    NodeSpec(
        "logic.or",
        "logic",
        "Or",
        "Kleene OR: true if any true, else unknown if any unknown, else false.",
        inputs=(
            _bool_in("a"),
            _bool_in("b"),
            _bool_in("c", required=False),
            _bool_in("d", required=False),
        ),
        outputs=(PortSpec("out", "bool"),),
    ),
    NodeSpec(
        "logic.not",
        "logic",
        "Not",
        "Negation; unknown stays unknown.",
        inputs=(_bool_in("a"),),
        outputs=(PortSpec("out", "bool"),),
    ),
    NodeSpec(
        "logic.cross_above",
        "logic",
        "Crosses above",
        "True on the last closed bar when a moved from <= b to > b.",
        inputs=(_series_in("a"), _series_in("b")),
        outputs=(PortSpec("out", "bool"),),
    ),
    NodeSpec(
        "logic.cross_below",
        "logic",
        "Crosses below",
        "True on the last closed bar when a moved from >= b to < b.",
        inputs=(_series_in("a"), _series_in("b")),
        outputs=(PortSpec("out", "bool"),),
    ),
    # -- flow ---------------------------------------------------------------------------
    NodeSpec(
        "flow.branch",
        "flow",
        "Branch",
        "Takes exactly one exec branch. An unknown condition takes neither (fail closed).",
        inputs=(_exec_in(), _bool_in("cond")),
        outputs=(_exec_out("true"), _exec_out("false")),
    ),
    NodeSpec(
        "flow.sequence",
        "flow",
        "Sequence",
        "Runs the connected outputs in order: then0, then1, then2, then3.",
        inputs=(_exec_in(),),
        outputs=(
            _exec_out("then0"),
            _exec_out("then1"),
            _exec_out("then2"),
            _exec_out("then3"),
        ),
    ),
    NodeSpec(
        "flow.gate_cooldown",
        "flow",
        "Cooldown gate",
        "Passes exec only when the last fill is at least `minutes` old; unknown = pass.",
        inputs=(_exec_in(),),
        outputs=(_exec_out(),),
        params=(ParamSpec("minutes", "int", required=True, min=0, max=10_080),),
    ),
    # -- actions ------------------------------------------------------------------------
    NodeSpec(
        "act.buy",
        "action",
        "Buy",
        "Buys a USD amount or a percent of cash, clipped to the max position headroom.",
        inputs=(_exec_in(),),
        outputs=(_exec_out(),),
        params=(
            ParamSpec("amount_usd", "decimal", min=Decimal(0), exclusive_min=True),
            ParamSpec("pct_cash", "decimal", min=Decimal(0), max=Decimal(100), exclusive_min=True),
            ParamSpec("max_position_usd", "decimal", min=Decimal(0), exclusive_min=True),
        ),
    ),
    NodeSpec(
        "act.sell",
        "action",
        "Sell",
        "Sells a fraction of the open position (default: all of it).",
        inputs=(_exec_in(),),
        outputs=(_exec_out(),),
        params=(
            ParamSpec(
                "pct_position",
                "decimal",
                default=Decimal(100),
                min=Decimal(0),
                max=Decimal(100),
                exclusive_min=True,
            ),
        ),
    ),
    NodeSpec(
        "act.notify",
        "action",
        "Notify",
        "Records a message in the run log. Never places an order.",
        inputs=(_exec_in(),),
        outputs=(_exec_out(),),
        params=(
            ParamSpec("message", "str", required=True, max_length=200),
            ParamSpec("level", "enum", default="info", choices=NOTIFY_LEVELS),
        ),
    ),
    NodeSpec(
        "act.set_var",
        "action",
        "Set variable",
        "Stores a number in strategy state; applied after the tick is recorded.",
        inputs=(_exec_in(), _number_in("value")),
        outputs=(_exec_out(),),
        params=(ParamSpec("name", "str", required=True, max_length=40),),
    ),
    # -- guards -------------------------------------------------------------------------
    NodeSpec(
        "guard.take_profit",
        "guard",
        "Take profit",
        "True when unrealized PnL is at least `pct` percent.",
        outputs=(PortSpec("hit", "bool"),),
        params=(_pct("pct", maximum=None),),
    ),
    NodeSpec(
        "guard.stop_loss",
        "guard",
        "Stop loss",
        "True when unrealized PnL is at or below -`pct` percent.",
        outputs=(PortSpec("hit", "bool"),),
        params=(_pct("pct"),),
    ),
    NodeSpec(
        "guard.trailing_stop",
        "guard",
        "Trailing stop",
        "Arms once the peak is `activate_pct` above entry; hits on a `giveback_pct` drop "
        "from that peak.",
        outputs=(PortSpec("armed", "bool"), PortSpec("hit", "bool")),
        params=(_pct("activate_pct", maximum=None), _pct("giveback_pct")),
    ),
)

NODE_SPECS: tuple[NodeSpec, ...] = _NODE_SPECS
NODE_TYPES: tuple[str, ...] = tuple(spec.type for spec in _NODE_SPECS)
NODE_BY_TYPE: dict[str, NodeSpec] = {spec.type: spec for spec in _NODE_SPECS}


def get_node_spec(node_type: str) -> NodeSpec | None:
    return NODE_BY_TYPE.get(node_type)


# --------------------------------------------------------------------------------------
# Parameter coercion (shared by validate and evaluate)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamError:
    name: str
    message: str


def _decimal_value(raw: object) -> Decimal | None:
    """Exact Decimal from a JSON number or numeric string. ``None`` when unusable."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, int | float | str):
        try:
            value = Decimal(str(raw).strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    if not value.is_finite():
        return None
    return value


def coerce_param(spec: ParamSpec, raw: object) -> tuple[Decimal | int | str | None, str | None]:
    """Return ``(value, error)`` for one param value. Never guesses on a bad value."""
    if spec.kind == "decimal":
        value = _decimal_value(raw)
        if value is None:
            return None, f"{spec.name} must be a finite number"
        if spec.min is not None:
            minimum = Decimal(str(spec.min))
            if value < minimum or (spec.exclusive_min and value == minimum):
                comparator = ">" if spec.exclusive_min else ">="
                return None, f"{spec.name} must be {comparator} {minimum}"
        if spec.max is not None:
            maximum = Decimal(str(spec.max))
            if value > maximum:
                return None, f"{spec.name} must be <= {maximum}"
        return value, None

    if spec.kind == "int":
        value = _decimal_value(raw)
        if value is None:
            return None, f"{spec.name} must be a whole number"
        if value != value.to_integral_value():
            return None, f"{spec.name} must be a whole number"
        number = int(value)
        if spec.min is not None and number < int(spec.min):
            return None, f"{spec.name} must be >= {spec.min}"
        if spec.max is not None and number > int(spec.max):
            return None, f"{spec.name} must be <= {spec.max}"
        return number, None

    if spec.kind == "enum":
        if not isinstance(raw, str) or raw not in spec.choices:
            choices = ", ".join(spec.choices)
            return None, f"{spec.name} must be one of: {choices}"
        return raw, None

    if not isinstance(raw, str):
        return None, f"{spec.name} must be a string"
    if spec.max_length is not None and len(raw) > spec.max_length:
        return None, f"{spec.name} must be at most {spec.max_length} characters"
    if not raw:
        return None, f"{spec.name} must not be empty"
    return raw, None


ParsedParams = dict[str, Decimal | int | str | None]


def parse_params(
    node_type: str, raw_params: Mapping[str, object] | None
) -> tuple[ParsedParams, tuple[ParamError, ...]]:
    """Coerce every declared param, apply defaults and reject unknown keys.

    A typo is an error, never a silent default. A param whose value is invalid is left
    out of the result (the evaluator then treats it as unknown, which fails closed).
    """
    spec = get_node_spec(node_type)
    values: ParsedParams = {}
    errors: list[ParamError] = []
    if spec is None:
        return values, (ParamError("", f"unknown node type: {node_type}"),)

    raw: Mapping[str, object] = raw_params or {}
    for entry in spec.params:
        if entry.name in raw:
            value, error = coerce_param(entry, raw[entry.name])
            if error is not None:
                errors.append(ParamError(entry.name, error))
                continue
            values[entry.name] = value
        elif entry.default is not None:
            values[entry.name] = entry.default
        elif entry.required:
            errors.append(ParamError(entry.name, f"{entry.name} is required"))

    known = {entry.name for entry in spec.params}
    for key in sorted(set(raw) - known):
        errors.append(ParamError(str(key), f"unknown parameter: {key}"))

    return values, tuple(errors)


def _param_json(entry: ParamSpec) -> dict[str, Any]:
    return {
        "name": entry.name,
        "kind": entry.kind,
        "required": entry.required,
        "default": None
        if entry.default is None
        else str(entry.default)
        if isinstance(entry.default, Decimal)
        else entry.default,
        "min": None
        if entry.min is None
        else str(entry.min)
        if isinstance(entry.min, Decimal)
        else entry.min,
        "max": None
        if entry.max is None
        else str(entry.max)
        if isinstance(entry.max, Decimal)
        else entry.max,
        "exclusive_min": entry.exclusive_min,
        "choices": list(entry.choices),
        "max_length": entry.max_length,
    }


def node_spec_json(spec: NodeSpec) -> dict[str, Any]:
    return {
        "type": spec.type,
        "category": spec.category,
        "title": spec.title,
        "description": spec.description,
        "inputs": [
            {
                "name": port.name,
                "type": port.type,
                "required": port.required,
                "default_param": port.default_param,
            }
            for port in spec.inputs
        ],
        "outputs": [{"name": port.name, "type": port.type} for port in spec.outputs],
        "params": [_param_json(entry) for entry in spec.params],
    }


def catalogue_json() -> dict[str, Any]:
    """The palette, as JSON. Stable: same catalogue -> same object -> same bytes."""
    from .schema import GRAPH_SCHEMA

    return {
        "schema": GRAPH_SCHEMA,
        "pin_types": ["exec", "bool", "number", "series"],
        "categories": [
            {"id": category, "title": CATEGORY_TITLES[category]} for category in CATEGORY_ORDER
        ],
        "node_types": [node_spec_json(spec) for spec in NODE_SPECS],
    }


__all__ = [
    "CATEGORY_ORDER",
    "CATEGORY_TITLES",
    "COMPARE_OPS",
    "NOTIFY_LEVELS",
    "NODE_BY_TYPE",
    "NODE_SPECS",
    "NODE_TYPES",
    "ORDER_ACTIONS",
    "TIMEFRAMES",
    "TIMEFRAME_MINUTES",
    "WINDOWS",
    "NodeSpec",
    "ParamError",
    "ParamSpec",
    "ParsedParams",
    "PinType",
    "PortSpec",
    "catalogue_json",
    "coerce_param",
    "get_node_spec",
    "node_spec_json",
    "parse_params",
]
