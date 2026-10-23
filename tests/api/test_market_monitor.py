"""Market monitor: observation-driven all-chain scanning, no network.

Every source here is a local fake. The monitor's job is bounded scanning, fail-closed
status and persistence; HTTP adapters are covered by the adapter suite.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_adapters import CHAINS, ChainCoverage, DexScreenerClient
from ezchain_adapters.dexscreener import BASE
from ezchain_adapters.ratelimit import TokenBucket
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.deps import get_market_monitor, get_session
from ezchain_api.models import LedgerEntryRow, MarketSnapshotRow
from ezchain_api.routers import markets
from ezchain_api.services.market_monitor import (
    MarketMonitor,
    build_overview,
    stale_after_seconds,
)
from ezchain_core import MarketRow, SourceRef, TokenSnapshot
from ezchain_core.ledger import position_account

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
    """Chain-addressable discovery stand-in with optional coverage metadata.

    By default it returns all-chain coverage, so the monitor must not re-probe chains.
    Set ``coverage_available=False`` to model a source that cannot report which chains it
    attempted; only then may the monitor fall back to one probe per chain.
    """

    def __init__(
        self,
        rows: dict[str, list[MarketRow]] | None = None,
        *,
        coverage_available: bool = True,
    ) -> None:
        self.rows = rows or {}
        self.fail: dict[str, Exception] = {}
        self.error_chains: set[str] = set()
        self.coverage_available = coverage_available
        self.calls: list[tuple[str, int]] = []
        self.token_snapshots: dict[tuple[str, str], TokenSnapshot] = {}
        self.token_calls: list[tuple[str, str]] = []

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.token_calls.append((chain, address))
        snapshot = self.token_snapshots.get((chain, address))
        if snapshot is None:
            raise RuntimeError("no token snapshot")
        return snapshot

    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: str = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, list[ChainCoverage] | None]:
        key = chain or "all"
        self.calls.append((key, limit))
        failure = self.fail.get(key)
        if failure is not None:
            raise failure
        now = datetime.now(UTC)
        if key == "all":
            rows = [row for chain_rows in self.rows.values() for row in chain_rows]
            coverage = None
            if self.coverage_available:
                coverage = [
                    ChainCoverage(
                        meta.id,
                        1,
                        len(self.rows.get(meta.id, [])),
                        "error"
                        if meta.id in self.error_chains
                        else ("pairs" if self.rows.get(meta.id) else "no_pairs"),
                    )
                    for meta in CHAINS
                ]
        else:
            rows = list(self.rows.get(key, []))
            coverage = None
            if self.coverage_available:
                coverage = [
                    ChainCoverage(
                        key,
                        1,
                        len(rows),
                        "error" if key in self.error_chains else ("pairs" if rows else "no_pairs"),
                    )
                ]
        rows.sort(key=lambda row: row.volume_24h_usd or Decimal(0), reverse=True)
        return rows[:limit], SourceRef("dexscreener", "https://dexscreener.com", now), coverage


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
    # The all-chain pass already attempted every registered chain: no gap re-probes.
    assert source.calls == [("all", 50)]
    assert result.attempted == tuple(chain.id for chain in CHAINS)

    overview = build_overview(session, monitor, settings)
    by_id = {chain["id"]: chain for chain in overview["chains"]}
    assert len(overview["chains"]) == len(CHAINS)
    assert by_id["ethereum"]["status"] == "ok"
    assert by_id["solana"]["status"] == "ok"
    assert by_id["ethereum"]["rows"] == 1
    # Ethereum's ladder is executable now: the badge names the primary provider.
    assert by_id["ethereum"]["quote_kind"] == "kyberswap"
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
    # Coverage said no_pairs; the monitor must not spend a second request on it.
    assert source.calls == [("all", 50)]


async def test_coverage_unavailable_falls_back_to_one_probe_per_chain(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource(
        {
            "ethereum": [make_row()],
            "base": [make_row(chain="base", token=BASE_TOKEN, symbol="WETH")],
        },
        coverage_available=False,
    )
    monitor = MarketMonitor(session_factory, source, settings)

    result = await monitor.run_once()

    assert source.calls[0] == ("all", 50)
    assert len(source.calls) == 1 + len(CHAINS)
    assert result.rows_by_chain["base"] == 1
    overview = build_overview(session, monitor, settings)
    base = next(chain for chain in overview["chains"] if chain["id"] == "base")
    assert base["status"] == "ok"
    assert base["rows"] == 1


async def test_open_positions_get_a_fresh_mark(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    session.add(
        LedgerEntryRow(
            group_id="position-seed",
            account=position_account("base", BASE_TOKEN),
            asset=BASE_TOKEN,
            amount=Decimal("2"),
            usd_value=Decimal("4000"),
            kind="buy",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    source = FakeSource({"base": [make_row(chain="base", token=BASE_TOKEN, symbol="WETH")]})
    source.token_snapshots[("base", BASE_TOKEN)] = TokenSnapshot(
        chain="base",
        address=BASE_TOKEN,
        symbol="WETH",
        name="Wrapped Ether",
        price_usd=Decimal("2500"),
        liquidity_usd=Decimal("1000000"),
        volume_24h_usd=Decimal("500000"),
        price_change_m5=None,
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None,
        source=SourceRef("dexscreener", None, datetime.now(UTC)),
    )
    monitor = MarketMonitor(session_factory, source, settings)

    result = await monitor.run_once()

    assert result.positions_refreshed == 1
    assert source.token_calls == [("base", BASE_TOKEN)]
    stored = session.scalar(
        select(MarketSnapshotRow).where(MarketSnapshotRow.token_address == BASE_TOKEN)
    )
    assert stored is not None
    assert stored.price_usd == Decimal("2500.00000000")


async def test_failed_all_chain_scan_falls_back_to_at_most_one_probe_per_chain(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource({})
    source.fail["all"] = RuntimeError("all-chain down")
    monitor = MarketMonitor(session_factory, source, settings)

    result = await monitor.run_once()

    assert source.calls[0] == ("all", 50)
    assert len(source.calls) == 1 + len(CHAINS)
    assert result.attempted == tuple(chain.id for chain in CHAINS)


async def test_all_chain_coverage_error_is_reported(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    source = FakeSource({"ethereum": [make_row()]})
    source.error_chains.add("ethereum")
    monitor = MarketMonitor(session_factory, source, settings)

    await monitor.run_once()

    overview = build_overview(session, monitor, settings)
    ethereum = next(chain for chain in overview["chains"] if chain["id"] == "ethereum")
    assert ethereum["status"] == "error"
    assert ethereum["rows"] == 0
    assert ethereum["last_error"] == "all-chain probe failed"
    assert overview["totals"]["error"] == 1
    assert source.calls == [("all", 50)]


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


async def test_stale_threshold_includes_last_scan_duration(
    session: Session, session_factory: sessionmaker[Session]
) -> None:
    settings = make_settings(market_scan_interval_seconds=90)
    fixed_now = datetime.now(UTC)
    observed = fixed_now - timedelta(seconds=200)
    session.add(
        MarketSnapshotRow(
            chain="ethereum",
            token_address=ETH_TOKEN,
            symbol="WETH",
            name="Wrapped Ether",
            price_usd=Decimal("2000"),
            liquidity_usd=Decimal("1234"),
            volume_24h_usd=Decimal("0"),
            price_change_m5=None,
            price_change_h1=None,
            price_change_h6=None,
            price_change_h24=None,
            source="dexscreener",
            observed_at=observed,
        )
    )
    session.commit()

    times = iter([100.0, 220.0])  # a 120-second scan
    monitor = MarketMonitor(session_factory, FakeSource(), settings, monotonic=lambda: next(times))
    await monitor.run_once()

    assert monitor.last_scan_duration_seconds == 120
    # max(60, 90 + 120 + 15) = 225, so a 200-second-old row is not stale.
    assert stale_after_seconds(settings, 120) == 225
    assert stale_after_seconds(settings, 0) == 105
    overview = build_overview(session, monitor, settings, now=fixed_now)
    ethereum = next(chain for chain in overview["chains"] if chain["id"] == "ethereum")
    assert ethereum["status"] == "ok"

    short_scan = MarketMonitor(session_factory, FakeSource(), settings)
    stale_overview = build_overview(session, short_scan, settings, now=fixed_now)
    stale_ethereum = next(chain for chain in stale_overview["chains"] if chain["id"] == "ethereum")
    assert stale_ethereum["status"] == "stale"


async def test_error_with_existing_rows_does_not_double_count_totals(
    session: Session, session_factory: sessionmaker[Session], settings: Settings
) -> None:
    session.add(
        MarketSnapshotRow(
            chain="ethereum",
            token_address=ETH_TOKEN,
            symbol="WETH",
            name="Wrapped Ether",
            price_usd=Decimal("2000"),
            liquidity_usd=Decimal("1234"),
            volume_24h_usd=Decimal("0"),
            price_change_m5=None,
            price_change_h1=None,
            price_change_h6=None,
            price_change_h24=None,
            source="dexscreener",
            observed_at=datetime.now(UTC),
        )
    )
    session.commit()
    source = FakeSource({"ethereum": []})
    source.error_chains.add("ethereum")
    monitor = MarketMonitor(session_factory, source, settings)

    await monitor.run_once()

    overview = build_overview(session, monitor, settings)
    ethereum = next(chain for chain in overview["chains"] if chain["id"] == "ethereum")
    assert ethereum["status"] == "error"
    assert ethereum["rows"] == 1
    assert overview["totals"]["covered"] == 1
    assert overview["totals"]["error"] == 0
    assert overview["totals"]["covered"] + overview["totals"]["error"] == 1


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


@respx.mock
async def test_successful_scan_stays_within_upstream_call_budget(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    respx.get(f"{BASE}/token-profiles/latest/v1").mock(return_value=httpx.Response(200, json=[]))
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1"):
        respx.get(f"{BASE}{path}").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/community-takeovers/latest/v1").mock(
        return_value=httpx.Response(200, json=[])
    )
    token_route = respx.get(url__regex=rf"^{re.escape(BASE)}/latest/dex/tokens/.+$").mock(
        return_value=httpx.Response(200, json={"pairs": []})
    )
    pairs_route = respx.get(url__regex=rf"^{re.escape(BASE)}/token-pairs/v1/.+$").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = DexScreenerClient(limiter=TokenBucket(rate_per_second=10_000, burst=10_000))
    monitor = MarketMonitor(session_factory, client, settings)

    async with client:
        result = await monitor.run_once()

    upstream_calls = 4 + token_route.call_count + pairs_route.call_count
    assert token_route.call_count <= len(CHAINS)
    # Every registered chain now has a seed, plus four hint feeds and at most two
    # shared-address fallback lookups (Base/Optimism WETH).
    assert upstream_calls <= len(CHAINS) + 4 + 2
    assert result.attempted == tuple(chain.id for chain in CHAINS)


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
    stored_body = stored.json()
    rows = stored_body["rows"]
    assert stored_body["total"] == 2
    assert stored_body["offset"] == 0
    assert stored_body["limit"] == 50
    assert stored_body["has_more"] is False
    assert stored_body["next_offset"] is None
    assert rows[0]["liquidity_usd"] == "500000"
    assert all(isinstance(row["price_usd"], str) for row in rows)
    assert rows[0]["source"] == "dexscreener"
    assert rows[0]["observed_at"]
    assert "pair_address" not in rows[0]
    assert "dex" not in rows[0]
    assert "url" not in rows[0]

    first_page = client.get(
        "/api/v1/markets/stored", params={"chain": "all", "limit": 1, "offset": 0}
    ).json()
    second_page = client.get(
        "/api/v1/markets/stored", params={"chain": "all", "limit": 1, "offset": 1}
    ).json()
    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert first_page["next_offset"] == 1
    assert second_page["has_more"] is False
    assert second_page["next_offset"] is None
    assert first_page["rows"][0]["token_address"] != second_page["rows"][0]["token_address"]

    solana = client.get("/api/v1/markets/stored", params={"chain": "solana"})
    assert [row["token_address"] for row in solana.json()["rows"]] == [SOL_TOKEN]

    unknown = client.get("/api/v1/markets/stored", params={"chain": "not-a-chain"})
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
