"""The graph strategy runtime: schema, catalogue, validation, indicators, evaluator.

One evaluator serves the editor preview, the backtester and the paper engine, so a graph
cannot behave one way in a test and another way with real money.
"""

from .backtest import (
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    run_backtest,
)
from .catalogue import (
    CATEGORY_TITLES,
    NODE_SPECS,
    NODE_TYPES,
    TIMEFRAME_MINUTES,
    NodeSpec,
    ParamSpec,
    PortSpec,
    catalogue_json,
    get_node_spec,
)
from .evaluate import (
    Candle,
    EvalContext,
    GraphDecision,
    evaluate_graph,
)
from .indicators import (
    atr,
    bbands,
    ema,
    highest,
    lowest,
    macd,
    roc,
    rsi,
    sma,
    vwap,
)
from .schema import (
    GRAPH_SCHEMA,
    Comment,
    Edge,
    GraphDocument,
    GraphSchemaError,
    Node,
    PortRef,
    parse_document,
)
from .validate import (
    Issue,
    ValidationReport,
    validate,
)

__all__ = [
    "CATEGORY_TITLES",
    "GRAPH_SCHEMA",
    "NODE_SPECS",
    "NODE_TYPES",
    "TIMEFRAME_MINUTES",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "Candle",
    "Comment",
    "Edge",
    "EquityPoint",
    "EvalContext",
    "GraphDecision",
    "GraphDocument",
    "GraphSchemaError",
    "Issue",
    "Node",
    "NodeSpec",
    "ParamSpec",
    "PortRef",
    "PortSpec",
    "ValidationReport",
    "atr",
    "bbands",
    "catalogue_json",
    "ema",
    "evaluate_graph",
    "get_node_spec",
    "highest",
    "lowest",
    "macd",
    "parse_document",
    "roc",
    "rsi",
    "run_backtest",
    "sma",
    "validate",
    "vwap",
]
