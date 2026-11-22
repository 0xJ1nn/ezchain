"""Token metadata endpoint tests against recorded provider observations.

The observations below are fixtures, not live calls. Every address is a throwaway
reference and no provider credential is used. The tests pin the fail-closed contract:
unknown values are null, windows longer than the pair's age are null, and a source
outage returns what is known with a reason instead of invented numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_adapters.errors import AdapterError, UpstreamError
from ezchain_adapters.metadata import WindowFacts
from ezchain_api.routers.metadata import get_metadata_service, router
from ezchain_api.services.metadata.service import MetadataService
from ezchain_core import SourceRef

NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)

LAUNCH = "TokenLaunch1111111111111111111111111111111"
MAJOR = "TokenMajor111111111111111111111111111111111"
BRIDGED = "TokenBridged11111111111111111111111111111111"

DEX = SourceRef("dexscreener", "https://dexscreener.com", NOW - timedelta(seconds=20))
BIRDEYE = SourceRef("birdeye", "https://birdeye.so", NOW - timedelta(seconds=30))
RPC = SourceRef("solana-rpc", "https://rpc.example.invalid", NOW - timedelta(seconds=25))


class FakeFetcher:
    """A recorded source with a call counter, so the cache can be pinned."""

    def __init__(self, observation: Any | None = None, error: AdapterError | None = None) -> None:
        self.observation = observation
        self.error = error
        self.calls = 0

    async def fetch(self, chain: str, address: str) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.observation


def _client(fetcher: FakeFetcher) -> TestClient:
    from ezchain_api.services.metadata.models import TokenMetadataObservation

    service = MetadataService(
        fetcher=fetcher,
        clock=lambda: NOW,
        observation_type=TokenMetadataObservation,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_metadata_service] = lambda: service
    return TestClient(app)


def _full_windows() -> dict[str, WindowFacts]:
    return {
        "5m": WindowFacts(
            price_change_pct=Decimal("1.25"),
            buys=120,
            sells=80,
            buy_volume_usd=Decimal("90000"),
            sell_volume_usd=Decimal("60000"),
            buying_makers=90,
            selling_makers=60,
        ),
        "1h": WindowFacts(
            price_change_pct=Decimal("3.5"),
            buys=900,
            sells=600,
            buy_volume_usd=Decimal("500000"),
            sell_volume_usd=Decimal("420000"),
            buying_makers=610,
            selling_makers=430,
        ),
        "6h": WindowFacts(
            price_change_pct=Decimal("8.1"),
            buys=5000,
            sells=4100,
            buy_volume_usd=Decimal("2600000"),
            sell_volume_usd=Decimal("2100000"),
            buying_makers=3300,
            selling_makers=2900,
        ),
        "24h": WindowFacts(
            price_change_pct=Decimal("12.4"),
            buys=19000,
            sells=15000,
            buy_volume_usd=Decimal("9000000"),
            sell_volume_usd=Decimal("8100000"),
            buying_makers=12000,
            selling_makers=10500,
        ),
    }


def _launch() -> Any:
    from ezchain_api.services.metadata.models import TokenMetadataObservation

    return TokenMetadataObservation(
        chain="solana",
        address=LAUNCH,
        source=DEX,
        sources={"windows": DEX},
        symbol="LAUNCH",
        name="Launch token",
        price_usd=Decimal("0.00042"),
        liquidity_usd=Decimal("42000"),
        pair_created_at=NOW - timedelta(minutes=30),
        windows={
            "5m": WindowFacts(
                price_change_pct=Decimal("9.5"),
                buys=140,
                sells=30,
            ),
            "1h": WindowFacts(
                price_change_pct=Decimal("21.0"),
                buys=900,
                sells=220,
            ),
        },
    )


def _major() -> Any:
    from ezchain_api.services.metadata.models import TokenMetadataObservation

    return TokenMetadataObservation(
        chain="solana",
        address=MAJOR,
        source=DEX,
        sources={
            "market": BIRDEYE,
            "liquidity": BIRDEYE,
            "holders": BIRDEYE,
            "windows": BIRDEYE,
            "rpc": RPC,
            "safety": BIRDEYE,
        },
        symbol="MAJOR",
        name="Major token",
        price_usd=Decimal("187.25"),
        market_cap_usd=Decimal("89000000000"),
        fdv_usd=Decimal("187000000000"),
        liquidity_usd=Decimal("125000000"),
        liquidity_change_pct=Decimal("2.4"),
        lp_burned_pct=Decimal("100"),
        lp_locked_pct=Decimal("0"),
        lp_lock_duration_seconds=0,
        holders=123456,
        holders_change_h1_pct=Decimal("1.7"),
        top_10_pct=Decimal("12.5"),
        dev_holdings_pct=Decimal("1.2"),
        pair_created_at=NOW - timedelta(days=800),
        windows=_full_windows(),
        mint_revoked=True,
        freeze_revoked=True,
        sells_work=True,
        buy_tax_pct=Decimal("1"),
        sell_tax_pct=Decimal("1.5"),
    )


def _bridged() -> Any:
    from ezchain_api.services.metadata.models import TokenMetadataObservation

    return TokenMetadataObservation(
        chain="ethereum",
        address=BRIDGED,
        source=DEX,
        symbol="bTOKEN",
        name="Bridged token",
        price_usd=Decimal("0.998"),
        liquidity_usd=Decimal("310000"),
        pair_created_at=NOW - timedelta(days=40),
        degraded=("NO_PAIRS:uniswap",),
    )


def _get(client: TestClient, address: str, chain: str = "solana") -> dict[str, Any]:
    response = client.get(f"/api/tokens/{chain}/{address}/metadata")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_launch_token_reports_known_market_and_unknown_keyed_fields() -> None:
    body = _get(_client(FakeFetcher(_launch())), LAUNCH)
    assert body["market"]["price_usd"]["value"] == "0.00042"
    assert body["market"]["price_usd"]["known"] is True
    assert body["market"]["market_cap_usd"]["value"] is None
    assert body["market"]["market_cap_usd"]["known"] is False
    assert body["market"]["market_cap_usd"]["source"]["name"] == "dexscreener"
    assert body["liquidity"]["lp_burned_pct"]["value"] is None
    assert body["holders"]["count"]["value"] is None
    assert body["pair_age_seconds"]["known"] is True
    assert int(body["pair_age_seconds"]["value"]) == 1780


def test_window_longer_than_pair_age_is_null_with_a_reason() -> None:
    body = _get(_client(FakeFetcher(_launch())), LAUNCH)
    short = body["windows"]["5m"]
    long = body["windows"]["6h"]
    assert short["buys"]["value"] == "140"
    assert short["bsr"]["value"] == str(Decimal("140") / Decimal("30"))
    assert all(metric["value"] is None for metric in long.values())
    assert "WINDOW_EXCEEDS_PAIR_AGE:6h" in body["degraded"]
    assert "WINDOW_EXCEEDS_PAIR_AGE:24h" in body["degraded"]


def test_major_token_reports_every_group_with_its_own_source() -> None:
    body = _get(_client(FakeFetcher(_major())), MAJOR)
    assert body["market"]["market_cap_usd"]["value"] == "89000000000"
    assert body["market"]["market_cap_usd"]["source"]["name"] == "birdeye"
    assert body["liquidity"]["state"]["value"] == "burned"
    assert body["liquidity"]["lp_burned_pct"]["value"] == "100"
    assert body["holders"]["count"]["value"] == "123456"
    assert body["holders"]["top_10_pct"]["value"] == "12.5"
    assert body["safety"]["buy_tax_pct"]["value"] == "1"
    assert body["safety"]["sell_tax_pct"]["value"] == "1.5"
    checks = body["safety"]["checks"]
    assert set(checks) == {
        "mint_revoked",
        "freeze_revoked",
        "lp_burned_or_locked",
        "top_10_under_30",
        "dev_under_5",
        "sells_work",
        "buy_sell_tax",
    }
    assert all(check["status"] == "pass" for check in checks.values())
    assert checks["mint_revoked"]["source"]["name"] == "solana-rpc"
    assert body["windows"]["1h"]["bsr"]["value"] == "1.5"


def test_bridged_token_never_guesses_a_missing_value() -> None:
    body = _get(_client(FakeFetcher(_bridged())), BRIDGED, chain="ethereum")
    assert body["market"]["price_usd"]["value"] == "0.998"
    assert body["market"]["fdv_usd"]["value"] is None
    assert body["holders"]["dev_holdings_pct"]["value"] is None
    assert body["liquidity"]["lp_locked_pct"]["value"] is None
    assert all(check["status"] == "unknown" for check in body["safety"]["checks"].values())
    assert "NO_PAIRS:uniswap" in body["degraded"]


def test_source_outage_returns_reason_and_no_invented_values() -> None:
    fetcher = FakeFetcher(error=UpstreamError("dexscreener unreachable"))
    body = _get(_client(fetcher), LAUNCH)
    assert all(metric["value"] is None for metric in body["market"].values())
    assert all(check["status"] == "unknown" for check in body["safety"]["checks"].values())
    assert any(reason.startswith("UNAVAILABLE:") for reason in body["degraded"])
    assert body["pair_age_seconds"]["value"] is None


def test_every_known_metric_carries_source_and_observation_time() -> None:
    body = _get(_client(FakeFetcher(_major())), MAJOR)
    groups = [body["market"], body["liquidity"], body["holders"], body["windows"]["1h"]]
    groups.append({"pair_age": body["pair_age_seconds"]})
    groups.append(body["safety"]["checks"])
    for group in groups:
        for metric in group.values():
            assert metric["source"] is not None
            assert metric["observed_at"] is not None
            assert metric["observed_at"].endswith("Z") or "+" in metric["observed_at"]


def test_response_is_cached_per_token() -> None:
    fetcher = FakeFetcher(_major())
    client = _client(fetcher)
    _get(client, MAJOR)
    _get(client, MAJOR)
    assert fetcher.calls == 1
