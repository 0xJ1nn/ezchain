"""Market monitor: observation-driven all-chain scanning, no network.

Every source here is a local fake. The monitor's job is bounded scanning, fail-closed
status and persistence; HTTP adapters are covered by the adapter suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_adapters import CHAINS
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.deps import get_market_monitor, get_session
from ezchain_api.models import MarketSnapshotRow
from ezchain_api.routers import markets
from ezchain_api.services.market_monitor import MarketMonitor, build_overview
from ezchain_core import MarketRow, SourceRef

ETH_TOKEN = "0x0000000000000000000000000000000000000abc"
SOL_TOKEN = "So11111111111111111111111111111111111111112"
BASE_TOKEN = "0x4200000000000000000000000000000000000006"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "data_mode": "fixture",
        "database_url": "sqlite+pysqlite://",
        "market_scan_interval_seconds": 30,
        "market_scan_limit": 50,
    }
    values.update(overrides)
    return Settings(**values)


def make_row(
    *,
    chain: str = "ethereum",
    token: str = ETH_TOKEN,
    symbol: str = "TEST",
    price: str = "2000",
    liquidity: str = "500000",
    observed_at: datetime | None = None,
) -> MarketRow:
    observed = observed_at or datetime.now(UTC)
    return MarketRow(
        chain=chain,
        pair_address=f"pair-{chain}-{token}",
        dex="uniswap",
        base_token=token,
        quote_token="USDC",
        symbol=symbol,
        name=symbol,
        price_usd=Decimal(price),
        liquidity_usd=Decimal(liquidity),
        volume_24h_usd=Decimal("100000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("1"),
        price_change_h6=None,
        price_change_h24=None,
        pair_created_at=None,
        url=f"https://dexscreener.com/{chain}/{token}",
        source=SourceRef("dexscreener", "https://dexscreener.com", observed),
    )


class FakeSource:
    """Chain-addressable discovery stand-in. ``all`` unions the configured chains."""

    def __init__(self, rows: dict[str, list[MarketRow]] | None = None) -> None:
        self.rows = rows or {}
        self.fail: dict[str, Exception] = {}
        self.calls: list[tuple[str, int]] = []

    async def list_markets(
        self, *, chain: str | None = None, query: str | None = None, limit: int = 50
    ) -> tuple[list[MarketRow], SourceRef]:
        key = chain or "all"
        self.calls.append((key, limit))
        failure = self.fail.get(key)
        if failure is not None:
            raise failure
        now = datetime.now(UTC)
        if key == "all":
            rows = [row for chain_rows in self.rows.values() for row in chain_rows]
        else:
            rows = list(self.rows.get(key, []))
        rows.sort(key=lambda row: row.liquidity_usd, reverse=True)
        return rows[:limit], SourceRef("dexscreener", "https://dexscreener.com", now)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield factory
    engine.dispose()


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as db:
        yield db


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def _count(session: Session) -> int:
    return int(session.scalar(select(func.count(MarketSnapshotRow.id))) or 0)


async def test_all_chain_scan_persists_rows_and_probes_only_gaps(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource(
        {
            "ethereum": [make_row()],
            "solana": [make_row(chain="solana", token=SOL_TOKEN, symbol="SOL", price="150")],
            "base": [make_row(chain="base", token=BASE_TOKEN, symbol="WETH", price="2000")],
        }
    )
    monitor = MarketMonitor(session_factory, source, settings)

    result = await monitor.run_once()

    assert result.persisted == 3
    assert _count(session) == 3
    # One all-chain call, then one bounded probe for every chain the all-scan missed.
    assert source.calls[0] == ("all", 50)
    probed = [chain for chain, _ in source.calls[1:]]
    assert "ethereum" not in probed
    assert "solana" not in probed
    assert len(probed) == len(CHAINS) - 3
    assert all(limit <= 50 for _, limit in source.calls)

    overview = build_overview(session, monitor, settings)
    by_id = {chain["id"]: chain for chain in overview["chains"]}
    assert len(overview["chains"]) == len(CHAINS)
    assert by_id["ethereum"]["status"] == "ok"
    assert by_id["solana"]["status"] == "ok"
    assert by_id["ethereum"]["rows"] == 1
    assert by_id["ethereum"]["quote_kind"] == "paper_mark"
    assert by_id["solana"]["quote_kind"] == "jupiter"
    assert overview["totals"]["covered"] == 3
    assert overview["totals"]["snapshots"] == 3
    assert overview["totals"]["chains"] == len(CHAINS)


def test_no_row_chain_is_scanning_before_the_first_scan(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    monitor = MarketMonitor(session_factory, FakeSource(), settings)

    overview = build_overview(session, monitor, settings)

    assert {chain["status"] for chain in overview["chains"]} == {"scanning"}
    assert overview["totals"]["covered"] == 0
    assert overview["totals"]["snapshots"] == 0


async def test_chain_without_rows_is_no_data_not_zero(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource({"ethereum": [make_row()]})
    monitor = MarketMonitor(session_factory, source, settings)

    await monitor.run_once()

    overview = build_overview(session, monitor, settings)
    base = next(chain for chain in overview["chains"] if chain["id"] == "base")
    assert base["status"] == "no_data"
    assert base["rows"] == 0
    assert base["last_observed_at"] is None
    assert base["age_seconds"] is None
    assert base["last_attempt_at"] is not None
    assert base["last_error"] is None
    assert "base" in [chain for chain, _ in source.calls]


async def test_per_chain_probe_recovers_a_chain_the_all_scan_missed(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource(
        {
            "ethereum": [make_row()],
            "base": [make_row(chain="base", token=BASE_TOKEN, symbol="WETH")],
        }
    )
    # The all-chain feed knows Ethereum only; Base is discovered by its own probe.
    original = source.list_markets

    async def list_markets(
        *, chain: str | None = None, query: str | None = None, limit: int = 50
    ) -> tuple[list[MarketRow], SourceRef]:
        if chain == "all":
            rows, ref = await original(chain=chain, query=query, limit=limit)
            return [row for row in rows if row.chain == "ethereum"], ref
        return await original(chain=chain, query=query, limit=limit)

    source.list_markets = list_markets  # type: ignore[method-assign]

    monitor = MarketMonitor(session_factory, source, settings)
    result = await monitor.run_once()

    assert result.rows_by_chain["base"] == 1
    overview = build_overview(session, monitor, settings)
    base = next(chain for chain in overview["chains"] if chain["id"] == "base")
    assert base["status"] == "ok"
    assert base["rows"] == 1


async def test_stale_row_is_stale_and_keeps_its_decimal_value(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    observed = datetime.now(UTC) - timedelta(minutes=10)
    session.add(
        MarketSnapshotRow(
            chain="ethereum",
            token_address=ETH_TOKEN,
            symbol="WETH",
            name="Wrapped Ether",
            price_usd=Decimal("2000.5"),
            liquidity_usd=Decimal("1234.5"),
            volume_24h_usd=Decimal("0"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=None,
            price_change_h24=None,
            source="dexscreener",
            observed_at=observed,
        )
    )
    session.commit()
    monitor = MarketMonitor(session_factory, FakeSource(), settings)
    await monitor.run_once()

    overview = build_overview(session, monitor, settings)
    ethereum = next(chain for chain in overview["chains"] if chain["id"] == "ethereum")
    assert ethereum["status"] == "stale"
    assert ethereum["rows"] == 1
    assert ethereum["age_seconds"] is not None and ethereum["age_seconds"] >= 600
    assert overview["totals"]["stale"] == 1

    from ezchain_api.services.market_monitor import stored_snapshots

    rows = stored_snapshots(session, chain="ethereum")
    assert rows[0].price_usd == Decimal("2000.5")
    assert rows[0].liquidity_usd == Decimal("1234.5")


async def test_provider_exception_records_error_and_scan_continues(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource({"solana": [make_row(chain="solana", token=SOL_TOKEN, symbol="SOL")]})
    source.fail["all"] = RuntimeError("discovery down")
    source.fail["ethereum"] = RuntimeError("provider exploded")
    monitor = MarketMonitor(session_factory, source, settings)

    result = await monitor.run_once()

    assert result.persisted == 1
    assert _count(session) == 1
    # Every chain was still attempted after Ethereum failed.
    assert result.attempted == tuple(chain.id for chain in CHAINS)
    assert "provider exploded" in result.errors["ethereum"]

    overview = build_overview(session, monitor, settings)
    by_id = {chain["id"]: chain for chain in overview["chains"]}
    assert by_id["ethereum"]["status"] == "error"
    assert "provider exploded" in (by_id["ethereum"]["last_error"] or "")
    assert by_id["solana"]["status"] == "ok"
    assert by_id["metis"]["status"] == "no_data"
    assert overview["totals"]["error"] == 1
    assert overview["totals"]["covered"] == 1


async def test_run_forever_stops_gracefully(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource({"ethereum": [make_row()]})
    monitor = MarketMonitor(session_factory, source, settings)

    task = asyncio.create_task(monitor.run_forever())
    for _ in range(50):
        if source.calls:
            break
        await asyncio.sleep(0.01)
    monitor.stop()
    await asyncio.wait_for(task, timeout=2)

    assert source.calls
    assert not monitor.scanning


def _client(session: Session, settings: Settings, monitor: MarketMonitor | None) -> TestClient:
    app = FastAPI()
    app.include_router(markets.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[markets.get_settings] = lambda: settings
    app.dependency_overrides[get_market_monitor] = lambda: monitor
    return TestClient(app)


def test_overview_and_stored_endpoints(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource(
        {
            "ethereum": [make_row()],
            "solana": [make_row(chain="solana", token=SOL_TOKEN, symbol="SOL", price="150")],
        }
    )
    monitor = MarketMonitor(session_factory, source, settings)
    asyncio.run(monitor.run_once())

    client = _client(session, settings, monitor)
    overview = client.get("/api/v1/markets/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["totals"]["chains"] == len(CHAINS)
    assert body["totals"]["covered"] == 2
    assert len(body["chains"]) == len(CHAINS)
    assert {"id", "name", "family", "quote_kind", "status", "rows", "age_seconds"} <= set(
        body["chains"][0]
    )

    stored = client.get("/api/v1/markets/stored", params={"chain": "all", "limit": 50})
    assert stored.status_code == 200
    rows = stored.json()
    assert len(rows) == 2
    assert rows[0]["liquidity_usd"] == "500000"
    assert all(isinstance(row["price_usd"], str) for row in rows)
    assert rows[0]["source"] == "dexscreener"
    assert rows[0]["observed_at"]
    assert "pair_address" not in rows[0]
    assert "dex" not in rows[0]
    assert "url" not in rows[0]

    solana = client.get("/api/v1/markets/stored", params={"chain": "solana"})
    assert [row["token_address"] for row in solana.json()] == [SOL_TOKEN]

    unknown = client.get("/api/v1/markets/stored", params={"chain": "not-a-chain"})
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
