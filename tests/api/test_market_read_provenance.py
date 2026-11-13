"""Market read models carry the contract's source and read-time age.

These tests pin round 3 of the market-event contract: a market read model reports where a
number came from and how old it is, using the contract's rule (``SourceRef.age_seconds``,
``SourceRef.is_stale``) against the reader's clock. The failure paths are the point — a
row with no source makes no freshness claim instead of looking fresh, and an adapter's
own write-time stale flag is not what the API serves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.config import Settings
from ezchain_api.deps import get_dexscreener, get_market_monitor, get_session
from ezchain_api.models import MarketSnapshotRow
from ezchain_api.routers import markets
from ezchain_api.services.market_reads import provenance, source_ref
from ezchain_core import MarketRow, SourceRef, TokenSnapshot

BASE = 1_760_000_000
NOW = datetime.fromtimestamp(BASE + 1_000, tz=UTC)
FRESH = timedelta(seconds=5)
OLD = timedelta(seconds=120)


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


# ---------------------------------------------------------------------------
# The shared rule: read-time age, never a stored verdict
# ---------------------------------------------------------------------------


def test_provenance_of_an_observation_ages_at_read_time() -> None:
    source = SourceRef("dexscreener", "https://dexscreener.com", _at(BASE))

    young = provenance(source, now=_at(BASE + 10))
    assert young.source == source
    assert young.age_seconds == 10
    assert young.stale is False

    # The same observation, read later, is older and stale. Nothing was rewritten.
    aged = provenance(source, now=_at(BASE + 120))
    assert aged.age_seconds == 120
    assert aged.stale is True
    assert aged.source == source


def test_provenance_without_a_source_reports_unknown_not_zero() -> None:
    unknown = provenance(None, now=NOW)
    assert unknown.source is None
    assert unknown.age_seconds is None
    assert unknown.stale is None
    assert unknown.to_dict() == {"source": None, "age_seconds": None, "stale": None}


@pytest.mark.parametrize("name", [None, "", "   ", "unknown", " UNKNOWN "])
def test_an_unprovenanced_row_has_no_contract_source(name: str | None) -> None:
    assert source_ref(name, NOW) is None


def test_a_named_row_source_is_a_contract_source_ref() -> None:
    origin = source_ref("dexscreener", NOW)
    assert origin == SourceRef("dexscreener", None, NOW)


def test_provenance_never_reports_a_negative_age() -> None:
    source = SourceRef("dexscreener", None, _at(BASE + 60))
    ahead = provenance(source, now=_at(BASE))
    assert ahead.age_seconds == 0
    assert ahead.stale is False


# ---------------------------------------------------------------------------
# The HTTP read models
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "data_mode": "fixture",
        "database_url": "sqlite+pysqlite://",
        "max_quote_age_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def _client(session: Session | None, settings: Settings, adapter: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(markets.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[markets.get_settings] = lambda: settings
    app.dependency_overrides[get_market_monitor] = lambda: None
    if adapter is not None:
        app.dependency_overrides[get_dexscreener] = lambda: adapter
    return TestClient(app)


def _row(**overrides: Any) -> MarketSnapshotRow:
    values: dict[str, Any] = {
        "chain": "ethereum",
        "token_address": "0x0000000000000000000000000000000000000abc",
        "symbol": "TEST",
        "name": "TEST",
        "price_usd": Decimal("2000"),
        "liquidity_usd": Decimal("500000"),
        "source": "dexscreener",
        "observed_at": datetime.now(UTC) - OLD,
        "raw": {},
    }
    values.update(overrides)
    return MarketSnapshotRow(**values)


def _market_row(chain: str, token: str, symbol: str, observed: datetime) -> MarketRow:
    return MarketRow(
        chain=chain,
        pair_address=f"pair-{chain}-{token}",
        dex="testdex",
        base_token=token,
        quote_token="USDC",
        symbol=symbol,
        name=symbol,
        price_usd=Decimal("1.5"),
        liquidity_usd=Decimal("100000"),
        volume_24h_usd=None,
        price_change_m5=None,
        price_change_h1=None,
        price_change_h6=None,
        price_change_h24=None,
        pair_created_at=None,
        url=None,
        source=SourceRef("dexscreener", "https://dexscreener.com", observed),
    )


class _Discovery:
    """Discovery stand-in: rows already carry their own observation times."""

    def __init__(self, rows: list[MarketRow], source: SourceRef) -> None:
        self.rows = rows
        self.source = source

    async def discover_markets(self, **_: Any) -> tuple[list[MarketRow], SourceRef, None]:
        return self.rows, self.source, None


def test_stored_rows_carry_a_contract_source_and_read_time_age(session: Session) -> None:
    session.add(_row(token_address="0xold", symbol="OLD"))
    session.add(
        _row(token_address="0xfresh", symbol="FRESH", observed_at=datetime.now(UTC) - FRESH)
    )
    session.commit()

    body = (
        _client(session, _settings()).get("/api/v1/markets/stored", params={"chain": "all"}).json()
    )

    rows = {row["symbol"]: row for row in body["rows"]}
    old, fresh = rows["OLD"], rows["FRESH"]

    assert old["source"]["name"] == "dexscreener"
    assert old["source"]["observed_at"]
    assert old["age_seconds"] >= 120
    assert old["stale"] is True

    assert fresh["source"]["name"] == "dexscreener"
    assert fresh["age_seconds"] < 60
    assert fresh["stale"] is False


def test_stored_row_without_a_source_makes_no_freshness_claim(session: Session) -> None:
    session.add(_row(token_address="0xunknown", symbol="UNK", source="unknown"))
    session.commit()

    body = (
        _client(session, _settings()).get("/api/v1/markets/stored", params={"chain": "all"}).json()
    )

    row = body["rows"][0]
    assert row["source"] is None
    assert row["age_seconds"] is None
    assert row["stale"] is None
    assert row["observed_at"]


def test_live_rows_age_from_their_own_source_not_the_adapter_answer() -> None:
    now = datetime.now(UTC)
    rows = [
        _market_row("ethereum", "0xold", "OLD", now - OLD),
        _market_row("ethereum", "0xfresh", "FRESH", now - FRESH),
    ]
    adapter = _Discovery(rows, SourceRef("dexscreener", "https://dexscreener.com", now))

    body = (
        _client(None, _settings(), adapter)
        .get("/api/v1/markets", params={"chain": "ethereum"})
        .json()
    )

    by_symbol = {row["symbol"]: row for row in body["rows"]}
    assert by_symbol["OLD"]["age_seconds"] >= 120
    assert by_symbol["OLD"]["stale"] is True
    assert by_symbol["FRESH"]["age_seconds"] < 60
    assert by_symbol["FRESH"]["stale"] is False
    # The list's own source is fresh, so a stale row does not make the whole page stale.
    assert body["stale"] is False
    assert all(row["source"]["name"] == "dexscreener" for row in body["rows"])


def test_token_read_serves_the_contract_verdict_not_the_adapter_flag() -> None:
    observed = datetime.now(UTC) - OLD

    class StaleAdapter:
        """An adapter that calls its old observation fresh — the bug this wiring removes."""

        async def get_token(self, chain: str, address: str) -> TokenSnapshot:
            return TokenSnapshot(
                chain=chain,
                address=address,
                symbol="TKN",
                name="Token",
                price_usd=Decimal("1.2"),
                liquidity_usd=Decimal("1000"),
                volume_24h_usd=None,
                price_change_m5=None,
                price_change_h1=None,
                price_change_h6=None,
                price_change_h24=None,
                source=SourceRef("dexscreener", "https://dexscreener.com", observed),
                stale=False,
            )

    body = _client(None, _settings(), StaleAdapter()).get("/api/v1/tokens/ethereum/0xabc").json()

    assert body["source"]["name"] == "dexscreener"
    assert body["age_seconds"] >= 120
    assert body["stale"] is True


def test_token_read_without_a_source_keeps_age_unknown() -> None:
    class SourcelessAdapter:
        async def get_token(self, chain: str, address: str) -> TokenSnapshot:
            return TokenSnapshot(
                chain=chain,
                address=address,
                symbol="TKN",
                name="Token",
                price_usd=Decimal("1.2"),
                liquidity_usd=None,
                volume_24h_usd=None,
                price_change_m5=None,
                price_change_h1=None,
                price_change_h6=None,
                price_change_h24=None,
                source=None,
                stale=True,
            )

    body = (
        _client(None, _settings(), SourcelessAdapter()).get("/api/v1/tokens/ethereum/0xabc").json()
    )

    assert body["source"] is None
    assert body["age_seconds"] is None
    assert body["stale"] is False
