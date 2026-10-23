"""Builders shared by the graph tests.

The documents here are the smallest ones that exercise one rule each; they are spelled out
rather than generated so a reader can see the graph the assertion is about.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ezchain_core.graph import Candle, EvalContext
from ezchain_core.types import TokenSnapshot

START = datetime(2026, 1, 1, tzinfo=UTC)


def node(node_id: str, node_type: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "params": params, "ui": {}}


def edge(source: str, source_port: str, target: str, target_port: str) -> dict[str, Any]:
    return {
        "from": {"node": source, "port": source_port},
        "to": {"node": target, "port": target_port},
    }


def doc(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    comments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": nodes,
        "edges": edges,
        "comments": comments or [],
    }


def make_snapshot(**overrides: Any) -> TokenSnapshot:
    values: dict[str, Any] = {
        "chain": "solana",
        "address": "So11111111111111111111111111111111111111112",
        "symbol": "TEST",
        "name": "Test Token",
        "price_usd": Decimal("1.00"),
        "liquidity_usd": Decimal("50000"),
        "volume_24h_usd": Decimal("25000"),
        "price_change_m5": Decimal("1.5"),
        "price_change_h1": Decimal("3.0"),
        "price_change_h6": Decimal("-2.0"),
        "price_change_h24": Decimal("8.0"),
        "pair_created_at": START - timedelta(days=2),
    }
    values.update(overrides)
    return TokenSnapshot(**values)


def make_candles(
    closes: list[str],
    *,
    timeframe_minutes: int = 5,
    start: datetime = START,
    opens: list[str] | None = None,
    highs: list[str] | None = None,
    lows: list[str] | None = None,
    volumes: list[str | None] | None = None,
) -> tuple[Candle, ...]:
    """Bars from a list of closes, with opens defaulting to the previous close."""
    bars: list[Candle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        open_price = opens[index] if opens is not None else previous
        high = highs[index] if highs is not None else max(open_price, close)
        low = lows[index] if lows is not None else min(open_price, close)
        volume = volumes[index] if volumes is not None else "100"
        bars.append(
            Candle(
                open_time=start + timedelta(minutes=timeframe_minutes * index),
                open=Decimal(open_price),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
                volume=None if volume is None else Decimal(volume),
            )
        )
        previous = close
    return tuple(bars)


def make_ctx(
    *,
    now: datetime | None = None,
    snapshot: TokenSnapshot | None = None,
    candles: dict[str, tuple[Candle, ...]] | None = None,
    **overrides: Any,
) -> EvalContext:
    values: dict[str, Any] = {
        "now": now or START,
        "snapshot": snapshot if snapshot is not None else make_snapshot(),
        "candles": candles or {},
        "cash_usd": Decimal("1000"),
        "equity_usd": Decimal("1000"),
        "max_position_usd": Decimal("500"),
    }
    values.update(overrides)
    return EvalContext(**values)


def price_gate_doc(*, buy_below: str = "1.00", sell_above: str = "1.10") -> dict[str, Any]:
    """Buy below a price while flat, sell above a price while long — one order per tick.

    ``tick -> sequence -> branch(buy) / branch(sell)`` so both paths are reachable and the
    "only the first order survives" rule is never what makes this graph work.
    """
    nodes = [
        node("tick", "event.tick"),
        node("seq", "flow.sequence"),
        node("pos", "pos.state"),
        node("price", "data.price"),
        node("buy_below", "math.const", value=buy_below),
        node("sell_above", "math.const", value=sell_above),
        node("cheap", "math.compare", op="<"),
        node("rich", "math.compare", op=">"),
        node("flat", "logic.not"),
        node("buy_ok", "logic.and"),
        node("sell_ok", "logic.and"),
        node("buy_branch", "flow.branch"),
        node("sell_branch", "flow.branch"),
        node("buy", "act.buy", amount_usd="100"),
        node("sell", "act.sell"),
    ]
    edges = [
        edge("tick", "out", "seq", "in"),
        edge("seq", "then0", "buy_branch", "in"),
        edge("seq", "then1", "sell_branch", "in"),
        edge("price", "value", "cheap", "a"),
        edge("buy_below", "value", "cheap", "b"),
        edge("price", "value", "rich", "a"),
        edge("sell_above", "value", "rich", "b"),
        edge("pos", "open", "flat", "a"),
        edge("cheap", "out", "buy_ok", "a"),
        edge("flat", "out", "buy_ok", "b"),
        edge("rich", "out", "sell_ok", "a"),
        edge("pos", "open", "sell_ok", "b"),
        edge("buy_ok", "out", "buy_branch", "cond"),
        edge("sell_ok", "out", "sell_branch", "cond"),
        edge("buy_branch", "true", "buy", "in"),
        edge("sell_branch", "true", "sell", "in"),
    ]
    return doc(nodes, edges)


def probe_doc(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    source: str,
    port: str,
    kind: str = "number",
) -> dict[str, Any]:
    """A valid, evaluable document that *reads* one node's output.

    Data nodes are lazy, so a node whose value nothing consumes never appears in the trace.
    The probe wires it to an action on the exec path (``act.set_var`` for numbers, a
    ``flow.branch`` for booleans) and adds the order action validation requires.
    """
    extra_nodes = list(nodes)
    extra_edges = list(edges)
    if kind == "bool":
        extra_nodes += [
            node("probe_branch", "flow.branch"),
            node("probe_buy", "act.buy", amount_usd="1"),
        ]
        extra_edges += [
            edge("tick", "out", "probe_branch", "in"),
            edge("probe_branch", "true", "probe_buy", "in"),
            edge(source, port, "probe_branch", "cond"),
        ]
    else:
        extra_nodes += [
            node("probe_set", "act.set_var", name="probe"),
            node("probe_buy", "act.buy", amount_usd="1"),
        ]
        extra_edges += [
            edge("tick", "out", "probe_set", "in"),
            edge("probe_set", "out", "probe_buy", "in"),
            edge(source, port, "probe_set", "value"),
        ]
    return doc(extra_nodes, extra_edges)


def buy_once_doc(*, below: str = "1.05", amount: str = "100") -> dict[str, Any]:
    """Buys ``amount`` USD once, while flat, whenever price is below ``below``."""
    return doc(
        [
            node("tick", "event.tick"),
            node("price", "data.price"),
            node("pos", "pos.state"),
            node("below", "math.const", value=below),
            node("cheap", "math.compare", op="<"),
            node("flat", "logic.not"),
            node("ok", "logic.and"),
            node("br", "flow.branch"),
            node("buy", "act.buy", amount_usd=amount),
        ],
        [
            edge("price", "value", "cheap", "a"),
            edge("below", "value", "cheap", "b"),
            edge("pos", "open", "flat", "a"),
            edge("cheap", "out", "ok", "a"),
            edge("flat", "out", "ok", "b"),
            edge("tick", "out", "br", "in"),
            edge("ok", "out", "br", "cond"),
            edge("br", "true", "buy", "in"),
        ],
    )
