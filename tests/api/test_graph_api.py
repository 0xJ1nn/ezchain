"""Graph catalogue, validate and dry-run endpoints.

The dry run must use the real evaluator with a live-shaped context and must never reach
the order executor. Unknown values stay ``null``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_geckoterminal
from ezchain_api.models import PaperOrderRow, StrategyRow, StrategyRunRow
from ezchain_api.routers import graph
from ezchain_api.services.graph import PaperGraphRuntime, build_tick
from ezchain_core import Candle, PairInfo, SourceRef, TokenSnapshot

CHAIN = "ethereum"
TOKEN = "0x0000000000000000000000000000000000000abc"
POOL = "0xpool"
NOW = datetime(2026, 10, 22, 12, 30, tzinfo=UTC)
FIVE_MINUTES = 300


def node(node_id: str, node_type: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "params": params, "ui": {}}


def edge(source: str, source_port: str, target: str, target_port: str) -> dict[str, Any]:
    return {
        "from": {"node": source, "port": source_port},
        "to": {"node": target, "port": target_port},
    }


def doc(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "ezchain.graph/1", "nodes": nodes, "edges": edges, "comments": []}


def price_gate_doc(
    *, buy_below: str = "2", sell_above: str = "1.5", sell_pct: str = "100"
) -> dict[str, Any]:
    return doc(
        [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("price", "data.price"),
            node("pos", "pos.state"),
            node("below", "math.const", value=buy_below),
            node("above", "math.const", value=sell_above),
            node("cheap", "math.compare", op="<"),
            node("rich", "math.compare", op=">"),
            node("flat", "logic.not"),
            node("buy_ok", "logic.and"),
            node("sell_ok", "logic.and"),
            node("buy_branch", "flow.branch"),
            node("sell_branch", "flow.branch"),
            node("buy", "act.buy", amount_usd="10"),
            node("sell", "act.sell", pct_position=sell_pct),
        ],
        [
            edge("tick", "out", "seq", "in"),
            edge("seq", "then0", "buy_branch", "in"),
            edge("seq", "then1", "sell_branch", "in"),
            edge("price", "value", "cheap", "a"),
            edge("below", "value", "cheap", "b"),
            edge("price", "value", "rich", "a"),
            edge("above", "value", "rich", "b"),
            edge("pos", "open", "flat", "a"),
            edge("cheap", "out", "buy_ok", "a"),
            edge("flat", "out", "buy_ok", "b"),
            edge("rich", "out", "sell_ok", "a"),
            edge("pos", "open", "sell_ok", "b"),
            edge("buy_ok", "out", "buy_branch", "cond"),
            edge("sell_ok", "out", "sell_branch", "cond"),
            edge("buy_branch", "true", "buy", "in"),
            edge("sell_branch", "true", "sell", "in"),
        ],
    )


def candles_doc() -> dict[str, Any]:
    """A valid graph that references 5m candles (orphan data is only a warning)."""
    return doc(
        [
            node("tick", "event.tick"),
            node("buy", "act.buy", amount_usd="10"),
            node("bars", "data.candles", timeframe="5m"),
        ],
        [edge("tick", "out", "buy", "in")],
    )


class FakeSnapshots:
    def __init__(self, price: str | None = "1.00", stale: bool = False) -> None:
        self.price = None if price is None else Decimal(price)
        self.stale = stale
        self.calls = 0

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls += 1
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test Token",
            price_usd=self.price,
            liquidity_usd=Decimal("100000"),
            volume_24h_usd=Decimal("50000"),
            price_change_m5=Decimal("1"),
            price_change_h1=Decimal("2"),
            price_change_h6=Decimal("3"),
            price_change_h24=Decimal("4"),
            pairs=(
                PairInfo(
                    pair_address=POOL,
                    dex="uniswap",
                    quote_token="USDC",
                    price_usd=self.price or Decimal("0"),
                    liquidity_usd=Decimal("100000"),
                    volume_24h_usd=None,
                    url=None,
                ),
            ),
            source=SourceRef("dexscreener", None, NOW),
            stale=self.stale,
            pair_created_at=NOW - timedelta(days=2),
        )


class FakeCandles:
    def __init__(self, bars: tuple[Candle, ...] = (), *, explode: bool = False) -> None:
        self.bars = bars
        self.explode = explode
        self.calls: list[str] = []

    async def ohlcv(
        self,
        chain_id: str,
        pool_address: str,
        token_address: str,
        timeframe: str,
        *,
        limit: int = 300,
        before: datetime | int | None = None,
    ) -> tuple[Candle, ...]:
        self.calls.append(timeframe)
        if self.explode:
            raise AssertionError("this graph must not request candles")
        return self.bars


def live_settings() -> Settings:
    return Settings(
        environment="test",
        data_mode="live",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )


def build(
    client_for: Callable[..., TestClient],
    *,
    snapshots: FakeSnapshots | None = None,
    candles: FakeCandles | None = None,
    settings: Settings | None = None,
) -> TestClient:
    overrides: dict[Any, Any] = {
        get_dexscreener: lambda: snapshots or FakeSnapshots(),
        get_geckoterminal: lambda: candles or FakeCandles(explode=True),
        get_settings: lambda: settings or live_settings(),
    }
    return client_for(graph.router, overrides)


def test_catalogue_is_the_core_table(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.get("/api/v1/graph/catalogue")

    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "ezchain.graph/1"
    assert body["pin_types"] == ["exec", "bool", "number", "series"]
    types = [entry["type"] for entry in body["node_types"]]
    assert "event.tick" in types
    assert "act.buy" in types
    assert len(types) == len(set(types))


def test_validate_rejects_a_body_without_a_graph(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.post("/api/v1/graph/validate", json={"graph": None})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "STRATEGY_GRAPH_INVALID"


def test_validate_returns_a_report_for_a_well_formed_invalid_graph(
    client_for: Callable[..., TestClient],
) -> None:
    client = build(client_for)
    response = client.post(
        "/api/v1/graph/validate",
        json={"graph": doc([node("only", "math.const", value="1")], [])},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert {issue["code"] for issue in body["errors"]} >= {"NO_EVENT", "NO_ACTION"}


def test_validate_accepts_a_runnable_graph(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.post("/api/v1/graph/validate", json={"graph": price_gate_doc()})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_dry_run_evaluates_without_placing_an_order(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    client = build(client_for, snapshots=FakeSnapshots(price="1.00"))
    response = client.post(
        "/api/v1/graph/dry-run",
        json={"graph": price_gate_doc(), "chain": CHAIN, "token_address": TOKEN},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "DRY_RUN"
    assert body["decision"]["action"] == "buy"
    assert body["decision"]["amount_usd"] == "10"
    assert body["decision"]["reason"] == "GRAPH_BUY"
    assert "trace" not in body["decision"]
    assert body["trace"]["exec"][0] == "tick"
    assert body["context"]["price_usd"] == "1.00"
    assert body["context"]["position"]["open"] is False
    assert session.scalar(select(func.count(PaperOrderRow.id))) == 0


def test_dry_run_keeps_an_unknown_price_null(
    client_for: Callable[..., TestClient],
) -> None:
    client = build(client_for, snapshots=FakeSnapshots(price=None))
    response = client.post(
        "/api/v1/graph/dry-run",
        json={"graph": price_gate_doc(), "chain": CHAIN, "token_address": TOKEN},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["action"] == "hold"
    assert body["decision"]["reason"] == "UNKNOWN_CONDITION"
    assert body["context"]["price_usd"] is None


def test_dry_run_refuses_an_invalid_graph(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.post(
        "/api/v1/graph/dry-run",
        json={
            "graph": doc([node("tick", "event.tick")], []),
            "chain": CHAIN,
            "token_address": TOKEN,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "STRATEGY_GRAPH_INVALID"
    assert detail["report"]["ok"] is False


def test_dry_run_rejects_an_unknown_chain(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for)
    response = client.post(
        "/api/v1/graph/dry-run",
        json={"graph": price_gate_doc(), "chain": "nope", "token_address": TOKEN},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"


async def test_paper_runtime_loads_vars_and_the_strategy_position_cap(
    session: Session,
) -> None:
    strategy = StrategyRow(
        name="graph",
        chain=CHAIN,
        token_address=TOKEN,
        token_symbol="TEST",
        kind="graph",
        params={
            "graph": price_gate_doc(),
            "interval_minutes": 15,
            "max_position_usd": "250",
        },
        status="active",
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    session.add(
        StrategyRunRow(
            strategy_id=strategy.id,
            action="hold",
            reason="GRAPH_NO_ACTION",
            inputs={"graph_vars": {"entry": "3"}},
        )
    )
    session.commit()

    snapshots = FakeSnapshots(price="1")
    runtime = PaperGraphRuntime(FakeCandles(explode=True), snapshots, "fixture")
    context = await runtime.build_context(
        session,
        strategy,
        snapshot=await snapshots.get_token(CHAIN, TOKEN),
        now=NOW,
    )

    assert context.vars == {"entry": Decimal("3")}
    assert context.max_position_usd == Decimal("250")
    assert context.position_quantity == Decimal(0)


async def test_context_only_sees_closed_bars(session: Session) -> None:
    now = datetime(2026, 10, 22, 12, 12, 30, tzinfo=UTC)
    latest_closed = datetime(2026, 10, 22, 12, 5, tzinfo=UTC)
    closed = Candle(
        open_time=latest_closed,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("2"),
        volume=Decimal("10"),
        timeframe_seconds=FIVE_MINUTES,
    )
    forming = Candle(
        open_time=datetime(2026, 10, 22, 12, 10, tzinfo=UTC),
        open=Decimal("999"),
        high=Decimal("999"),
        low=Decimal("999"),
        close=Decimal("999"),
        volume=Decimal("1"),
        timeframe_seconds=FIVE_MINUTES,
    )
    snapshots = FakeSnapshots(price="2")
    tick = await build_tick(
        session=session,
        candle_source=FakeCandles((closed, forming)),
        snapshot_source=snapshots,
        document=candles_doc(),
        chain=CHAIN,
        token_address=TOKEN,
        snapshot=await snapshots.get_token(CHAIN, TOKEN),
        now=now,
        max_position_usd=None,
        data_mode="live",
    )

    bars = tick.context.candles["5m"]
    assert len(bars) == 1
    assert bars[-1].close == Decimal("2")
    assert tick.candles["5m"].bars == 1
    assert tick.candles["5m"].last_close_time == datetime(2026, 10, 22, 12, 10, tzinfo=UTC)
