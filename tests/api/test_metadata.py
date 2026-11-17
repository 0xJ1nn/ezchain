"""Token metadata: recorded responses, honest unknowns and a source outage.

The provider is a fake fed from recorded DexScreener payloads, so these tests make no
network request. They pin the field mapping, the fail-closed treatment of unknown values
and the "a source outage returns what is known plus a reason" rule.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_adapters.errors import UpstreamError
from ezchain_adapters.metadata import normalize_metadata_pairs
from ezchain_api.routers.metadata import router
from ezchain_api.services.metadata import MetadataService, TokenMetadata
from ezchain_api.services.metadata.models import (
    REASON_NO_KEYED_SOURCE,
    REASON_PAIR_AGE_UNKNOWN,
    REASON_SOURCE_UNAVAILABLE,
    REASON_WINDOW_OUTSIDE_LIFE,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metadata"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        self.calls = 0

    async def token_metadata(self, chain: str, address: str) -> Any:
        self.calls += 1
        if self._fail:
            raise UpstreamError("provider unreachable")
        data = _fixture(self._name)
        return normalize_metadata_pairs(
            chain,
            address,
            data["pairs"],
            datetime.fromisoformat(data["observed_at"]),
        )


def _service(name: str, *, fail: bool = False) -> MetadataService:
    return MetadataService(FakeProvider(name, fail=fail), now=lambda: NOW)


def _client(service: MetadataService) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    # The service is created once per process on app.state; inject the fake there so the
    # route's dependency returns it without building a network client.
    app.state.metadata_service = service
    return TestClient(app)


@pytest.mark.asyncio
async def test_normalize_launch_maps_market_and_windows() -> None:
    data = _fixture("launch")
    address = data["pairs"][0]["baseToken"]["address"]
    snap = normalize_metadata_pairs(
        "solana", address, data["pairs"], datetime.fromisoformat(data["observed_at"])
    )
    assert snap.source == "dexscreener"
    assert snap.is_base is True
    assert snap.market_cap_usd is not None and int(snap.market_cap_usd) == 42100
    assert snap.fdv_usd is not None and int(snap.fdv_usd) == 42100
    assert snap.liquidity_usd is not None and int(snap.liquidity_usd) == 42000
    assert snap.windows["5m"].buys == 42
    assert snap.windows["24h"].sells == 3200


@pytest.mark.asyncio
async def test_launch_token_has_full_windows_and_seven_checks() -> None:
    meta = await _service("launch").get("solana", "LAUNCHMINT11111111111111111111111111111111")
    assert isinstance(meta, TokenMetadata)
    assert meta.market_cap_usd.known and meta.market_cap_usd.source == "dexscreener"
    assert meta.pair_age_seconds.known and meta.pair_age_seconds.value == 1200
    assert set(meta.windows) == {"5m", "1h", "6h", "24h"}
    assert meta.windows["5m"].applicable is True
    assert all(not meta.windows[w].applicable for w in ("1h", "6h", "24h"))
    assert meta.windows["1h"].buys.reason == REASON_WINDOW_OUTSIDE_LIFE
    assert meta.windows["5m"].bsr.known
    assert len(meta.checks) == 7
    assert all(check.status == "unknown" for check in meta.checks.values())
    # Fields DexScreener does not publish stay null with a reason, never derived or zeroed.
    assert meta.liquidity_change_pct.value is None
    assert meta.holders.value is None
    assert meta.holders.reason == REASON_NO_KEYED_SOURCE
    assert meta.windows["5m"].buy_volume_usd.value is None
    assert meta.lp.status == "unknown"


@pytest.mark.asyncio
async def test_major_token_established_pair_age_and_bsr_below_one() -> None:
    meta = await _service("major").get("ethereum", "0xmajor000000000000000000000000000000000000")
    assert meta.pair_age_seconds.known
    assert meta.pair_age_seconds.value is not None and meta.pair_age_seconds.value > 86400
    bsr = meta.windows["5m"].bsr
    assert bsr.known and bsr.value is not None and bsr.value > 1
    assert meta.market_cap_usd.known


@pytest.mark.asyncio
async def test_bridged_quote_side_withholds_base_token_fields() -> None:
    data = _fixture("bridged")
    address = data["pairs"][0]["quoteToken"]["address"]
    snap = normalize_metadata_pairs(
        "base", address, data["pairs"], datetime.fromisoformat(data["observed_at"])
    )
    assert snap.is_base is False
    assert snap.market_cap_usd is None
    assert snap.fdv_usd is None
    assert all(count.price_change_pct is None for count in snap.windows.values())


@pytest.mark.asyncio
async def test_outage_returns_warnings_and_null_with_reasons() -> None:
    service = _service("launch", fail=True)
    meta = await service.get("solana", "LAUNCHMINT11111111111111111111111111111111")
    assert meta.warnings == (REASON_SOURCE_UNAVAILABLE,)
    assert meta.market_cap_usd.value is None
    assert meta.holders.value is None
    assert meta.checks["mint_revoked"].status == "unknown"


@pytest.mark.asyncio
async def test_missing_pair_age_makes_all_windows_inapplicable() -> None:
    data = _fixture("launch")
    data["pairs"][0]["pairCreatedAt"] = None
    address = data["pairs"][0]["baseToken"]["address"]

    class NoAge(FakeProvider):
        async def token_metadata(self, chain: str, address: str) -> Any:
            return normalize_metadata_pairs(chain, address, data["pairs"], NOW)

    meta = await MetadataService(NoAge("launch"), now=lambda: NOW).get("solana", address)
    assert meta.pair_age_seconds.reason == REASON_PAIR_AGE_UNKNOWN
    assert all(not window.applicable for window in meta.windows.values())
    assert meta.windows["24h"].buys.reason == REASON_PAIR_AGE_UNKNOWN


def test_cache_short_circuits_a_second_read() -> None:
    import asyncio

    provider = FakeProvider("major")
    service = MetadataService(provider, now=lambda: NOW)
    asyncio.run(service.get("ethereum", "0xmajor000000000000000000000000000000000000"))
    asyncio.run(service.get("ethereum", "0xmajor000000000000000000000000000000000000"))
    assert provider.calls == 1


def test_router_returns_metadata_shape() -> None:
    service = _service("launch")
    client = _client(service)
    response = client.get(
        "/api/v1/tokens/solana/LAUNCHMINT11111111111111111111111111111111/metadata"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chain"] == "solana"
    assert body["market_cap_usd"]["source"] == "dexscreener"
    assert body["market_cap_usd"]["known"] is True
    assert body["holders"]["value"] is None
    assert body["holders"]["reason"] == REASON_NO_KEYED_SOURCE
    assert set(body["windows"]) == {"5m", "1h", "6h", "24h"}
    assert len(body["checks"]) == 7


def test_router_unknown_chain_is_404() -> None:
    client = _client(_service("launch"))
    response = client.get("/api/v1/tokens/notachain/someaddress/metadata")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
