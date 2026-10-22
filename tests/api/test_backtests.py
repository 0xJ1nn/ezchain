"""Graph backtests: create, list, read, closed-bar-only, immutable artifacts."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.config import Settings, get_settings
from ezchain_api.deps import get_dexscreener, get_geckoterminal
from ezchain_api.models import StrategyRow
from ezchain_api.routers import backtests
from ezchain_api.services.candles import latest_closed_open, write_candles
from ezchain_core import Candle, PairInfo, SourceRef, TokenSnapshot

CHAIN = "ethereum"
TOKEN = "0x0000000000000000000000000000000000000abc"
POOL = "0xpool"
FIVE_MINUTES = 300


def node(node_id: str, node_type: str, **params: Any) -> dict[str, Any]:
    return {"id": node_id, "type": node_type, "params": params, "ui": {}}


def edge(source: str, source_port: str, target: str, target_port: str) -> dict[str, Any]:
    return {
        "from": {"node": source, "port": source_port},
        "to": {"node": target, "port": target_port},
    }


def price_gate_doc() -> dict[str, Any]:
    return {
        "schema": "ezchain.graph/1",
        "nodes": [
            node("tick", "event.tick"),
            node("seq", "flow.sequence"),
            node("price", "data.price"),
            node("pos", "pos.state"),
            node("below", "math.const", value="2"),
            node("above", "math.const", value="1.5"),
            node("cheap", "math.compare", op="<"),
            node("rich", "math.compare", op=">"),
            node("flat", "logic.not"),
            node("buy_ok", "logic.and"),
            node("sell_ok", "logic.and"),
            node("buy_branch", "flow.branch"),
            node("sell_branch", "flow.branch"),
            node("buy", "act.buy", amount_usd="100"),
            node("sell", "act.sell", pct_position="100"),
        ],
        "edges": [
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
        "comments": [],
    }


class FakeSnapshots:
    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("1"),
            liquidity_usd=Decimal("100000"),
            volume_24h_usd=Decimal("50000"),
            price_change_m5=None,
            price_change_h1=None,
            price_change_h6=None,
            price_change_h24=None,
            pairs=(
                PairInfo(
                    pair_address=POOL,
                    dex="uniswap",
                    quote_token="USDC",
                    price_usd=Decimal("1"),
                    liquidity_usd=Decimal("100000"),
                    volume_24h_usd=None,
                    url=None,
                ),
            ),
            source=SourceRef("dexscreener", None, datetime.now(UTC)),
        )


class FakeCandles:
    def __init__(self, bars: tuple[Candle, ...]) -> None:
        self.bars = bars
        self.calls = 0

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
        self.calls += 1
        return self.bars


def live_settings() -> Settings:
    return Settings(
        environment="test",
        data_mode="live",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )


def bars(count: int, *, closes: list[str] | None = None) -> tuple[Candle, ...]:
    end = latest_closed_open(datetime.now(UTC), FIVE_MINUTES)
    start = end - timedelta(seconds=FIVE_MINUTES * (count - 1))
    prices = closes or [str(index + 1) for index in range(count)]
    result: list[Candle] = []
    for index in range(count):
        close = Decimal(prices[index])
        result.append(
            Candle(
                open_time=start + timedelta(seconds=FIVE_MINUTES * index),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=Decimal("10"),
                timeframe_seconds=FIVE_MINUTES,
            )
        )
    return tuple(result)


def seed(session: Session, series: tuple[Candle, ...], *, timeframe: str = "5m") -> None:
    now = datetime.now(UTC)
    write_candles(
        session,
        [
            {
                "chain": CHAIN,
                "token_address": TOKEN,
                "pool_address": POOL,
                "timeframe": timeframe,
                "open_time": candle.open_time,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "source": "geckoterminal",
                "fetched_at": now,
            }
            for candle in series
        ],
    )
    session.commit()


def build(client_for: Callable[..., TestClient], series: tuple[Candle, ...]) -> TestClient:
    overrides: dict[Any, Any] = {
        get_dexscreener: lambda: FakeSnapshots(),
        get_geckoterminal: lambda: FakeCandles(series),
        get_settings: live_settings,
    }
    return client_for(backtests.router, overrides)


def request_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "graph": price_gate_doc(),
        "chain": CHAIN,
        "token_address": TOKEN,
        "timeframe": "5m",
        "bars": 5,
        "fee_bps": 10,
        "slippage_bps": 5,
        "starting_cash_usd": "1000",
    }
    body.update(overrides)
    return body


def test_backtest_runs_and_is_persisted(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    series = bars(5, closes=["1", "2", "3", "4", "5"])
    seed(session, series)
    client = build(client_for, series)

    response = client.post("/api/v1/backtests", json=request_body())

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "BACKTEST"
    assert body["fill_model"] == "next_bar_open"
    assert body["data_source"] == "provided_candles"
    assert body["id"]
    assert body["created_at"]
    assert body["request"]["fee_bps"] == 10
    assert body["metrics"]["trades"] == 2
    assert body["metrics"]["closed_trades"] == 1
    assert len(body["trades"]) == 1
    trade = body["trades"][0]
    assert trade["entry_time"] < trade["exit_time"]
    assert trade["qty"]
    assert trade["fees_usd"]
    assert len(body["equity"]) == 5
    assert body["node_hits"]

    listed = client.get("/api/v1/backtests")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [body["id"]]
    assert listed.json()[0]["metrics"] == body["metrics"]

    fetched = client.get(f"/api/v1/backtests/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_backtest_never_sees_a_forming_bar(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    closed = bars(5, closes=["1", "2", "3", "4", "5"])
    latest = closed[-1]
    forming = Candle(
        open_time=latest.open_time + timedelta(seconds=FIVE_MINUTES),
        open=Decimal("999"),
        high=Decimal("999"),
        low=Decimal("999"),
        close=Decimal("999"),
        volume=Decimal("1"),
        timeframe_seconds=FIVE_MINUTES,
    )
    seed(session, (*closed, forming))
    client = build(client_for, (*closed, forming))

    response = client.post("/api/v1/backtests", json=request_body(bars=6))

    assert response.status_code == 201
    body = response.json()
    assert len(body["equity"]) == 5
    assert body["equity"][-1]["t"] == latest.close_time.isoformat()
    # A $999 forming close is never marked: the last equity stays near the $1000 start.
    assert Decimal(body["equity"][-1]["equity_usd"]) < Decimal("1100")


def test_backtest_from_a_strategy_uses_its_graph_and_attribution(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    series = bars(5, closes=["1", "2", "3", "4", "5"])
    seed(session, series)
    strategy = StrategyRow(
        name="graph",
        chain=CHAIN,
        token_address=TOKEN,
        token_symbol="TEST",
        kind="graph",
        params={"graph": price_gate_doc(), "interval_minutes": 15, "max_position_usd": "250"},
        status="active",
    )
    session.add(strategy)
    session.commit()
    session.refresh(strategy)
    client = build(client_for, series)

    response = client.post(
        "/api/v1/backtests",
        json={
            "strategy_id": strategy.id,
            "timeframe": "5m",
            "bars": 5,
            "fee_bps": 10,
            "slippage_bps": 5,
            "starting_cash_usd": "1000",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["request"]["strategy_id"] == strategy.id
    assert body["request"]["chain"] == CHAIN
    listed = client.get(f"/api/v1/backtests?strategy_id={strategy.id}")
    assert [row["id"] for row in listed.json()] == [body["id"]]
    assert client.get("/api/v1/backtests?strategy_id=other").json() == []


def test_backtest_rejects_an_invalid_graph(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    series = bars(5)
    seed(session, series)
    client = build(client_for, series)
    invalid = {
        "schema": "ezchain.graph/1",
        "nodes": [{"id": "tick", "type": "event.tick"}],
        "edges": [],
    }

    response = client.post("/api/v1/backtests", json=request_body(graph=invalid))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "STRATEGY_GRAPH_INVALID"


def test_backtest_rejects_a_malformed_document_shape(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    series = bars(5)
    seed(session, series)
    client = build(client_for, series)
    malformed = {
        "schema": "ezchain.graph/1",
        "nodes": [
            {"id": "tick", "type": "event.tick"},
            {"id": "tick", "type": "event.tick"},
        ],
        "edges": [],
    }

    response = client.post("/api/v1/backtests", json=request_body(graph=malformed))

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "STRATEGY_GRAPH_INVALID"


def test_unknown_backtest_is_404(client_for: Callable[..., TestClient]) -> None:
    client = build(client_for, ())
    response = client.get("/api/v1/backtests/nope")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "BACKTEST_NOT_FOUND"
