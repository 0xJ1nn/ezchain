"""Adapter tests for token metadata, using recorded payloads only.

No network is reached: the HTTP sources are injected as fakes, and the payloads below are
trimmed recordings of the shapes the normalization code expects. Addresses are throwaway
references and the key is an invalid test constant, not a real credential.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ezchain_adapters.errors import UpstreamError
from ezchain_adapters.metadata import (
    BirdeyeClient,
    TokenMetadataClient,
    normalize_birdeye,
    normalize_dexscreener_pairs,
    parse_mint_authorities,
)
from ezchain_core import SourceRef

OBSERVED = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
BRIDGED = "TokenBridged11111111111111111111111111111111"
LAUNCH = "TokenLaunch1111111111111111111111111111111"
TEST_KEY = "TEST_KEY_DO_NOT_USE"


def _pair() -> dict[str, Any]:
    return {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "Pair1111111111111111111111111111111111111",
        "baseToken": {"address": LAUNCH, "symbol": "LAUNCH", "name": "Launch token"},
        "quoteToken": {
            "address": "Quote1111111111111111111111111111111111111",
            "symbol": "SOL",
            "name": "Wrapped SOL",
        },
        "priceUsd": "0.00042",
        "priceNative": "0.00031",
        "liquidity": {"usd": 42000},
        "volume": {"h24": 90000},
        "priceChange": {"m5": 9.5, "h1": 21.0},
        "txns": {
            "m5": {"buys": 140, "sells": 30},
            "h1": {"buys": 900, "sells": 220},
            "h6": {"buys": 3000, "sells": 2100},
            "h24": {"buys": 12000, "sells": 9000},
        },
        "pairCreatedAt": int((OBSERVED - timedelta(minutes=30)).timestamp() * 1000),
        "url": "https://dexscreener.com/solana/pair",
    }


def _source() -> SourceRef:
    return SourceRef("dexscreener", "https://dexscreener.com", OBSERVED)


def test_recorded_dexscreener_payload_maps_each_window() -> None:
    observation = normalize_dexscreener_pairs(
        "solana", LAUNCH, [_pair()], observed_at=OBSERVED, source=_source()
    )
    assert observation.symbol == "LAUNCH"
    assert observation.price_usd == Decimal("0.00042")
    assert observation.liquidity_usd == Decimal("42000")
    assert observation.windows["5m"].buys == 140
    assert observation.windows["5m"].sells == 30
    assert observation.windows["5m"].price_change_pct == Decimal("9.5")
    assert observation.windows["24h"].buys == 12000
    market_source = observation.source_for("market")
    assert market_source is not None
    assert market_source.name == "dexscreener"


def test_quote_side_token_derives_price_and_leaves_changes_unknown() -> None:
    pair = _pair()
    pair["quoteToken"]["address"] = BRIDGED
    pair["baseToken"]["address"] = "Other11111111111111111111111111111111111111"
    pair["priceUsd"] = "2.004"
    pair["priceNative"] = "2.004"
    observation = normalize_dexscreener_pairs(
        "solana", BRIDGED, [pair], observed_at=OBSERVED, source=_source()
    )
    assert observation.symbol == "SOL"
    assert observation.price_usd == Decimal("1")
    assert observation.windows["5m"].price_change_pct is None
    assert observation.degraded == ()


def test_quote_side_without_native_price_is_unknown() -> None:
    pair = _pair()
    pair["quoteToken"]["address"] = BRIDGED
    pair["baseToken"]["address"] = "Other11111111111111111111111111111111111111"
    pair["priceNative"] = None
    observation = normalize_dexscreener_pairs(
        "solana", BRIDGED, [pair], observed_at=OBSERVED, source=_source()
    )
    assert observation.price_usd is None
    assert "NO_PAIRS:dexscreener" in observation.degraded


def test_missing_pairs_report_the_provider_was_asked() -> None:
    observation = normalize_dexscreener_pairs(
        "solana", LAUNCH, [], observed_at=OBSERVED, source=_source()
    )
    assert observation.price_usd is None
    assert observation.source is not None
    assert observation.degraded == ("NO_PAIRS:dexscreener",)


def test_negative_provider_counts_are_unknown_not_zero() -> None:
    pair = _pair()
    pair["txns"]["m5"]["buys"] = -5
    observation = normalize_dexscreener_pairs(
        "solana", LAUNCH, [pair], observed_at=OBSERVED, source=_source()
    )
    assert observation.windows["5m"].buys is None
    assert observation.windows["5m"].sells == 30


def test_mint_layout_decodes_authority_options() -> None:
    raw = bytearray(82)
    raw[0:4] = (0).to_bytes(4, "little")
    raw[46:50] = (1).to_bytes(4, "little")
    encoded = base64.b64encode(bytes(raw)).decode()
    assert parse_mint_authorities(encoded) == (True, False)
    assert parse_mint_authorities("not base64!!") == (None, None)
    assert parse_mint_authorities(base64.b64encode(b"short").decode()) == (None, None)
    # Only the exact SPL mint layout is decoded: a longer account is not a mint.
    assert parse_mint_authorities(base64.b64encode(bytes(100)).decode()) == (None, None)


def test_recorded_birdeye_payload_enriches_without_overwriting_free_values() -> None:
    payload = {
        "data": {
            "marketCap": 89000000000,
            "fdv": 187000000000,
            "top10HolderPercent": 12.5,
            "devHoldingsPercent": 1.2,
            "holder": 123456,
            "holderChange1hPercent": 1.7,
            "lpBurnedPercent": 100,
            "lpLockedPercent": 0,
            "sellsWork": True,
            "buyTaxPercent": 1,
            "sellTaxPercent": 1.5,
            "windows": {
                "1h": {
                    "buy_volume_usd": 500000,
                    "sell_volume_usd": 420000,
                    "buying_makers": 610,
                    "selling_makers": 430,
                }
            },
        }
    }
    source = SourceRef("birdeye", "https://birdeye.so", OBSERVED)
    observation = normalize_birdeye(payload, source=source, chain="solana", address=LAUNCH)
    assert observation.market_cap_usd == Decimal("89000000000")
    assert observation.top_10_pct == Decimal("12.5")
    assert observation.dev_holdings_pct == Decimal("1.2")
    assert observation.windows["1h"].buying_makers == 610
    holders_source = observation.source_for("holders")
    assert holders_source is not None
    assert holders_source.name == "birdeye"
    assert observation.price_usd is None
    assert observation.windows["5m"].buys is None


class FakeDexScreener:
    def __init__(self, pairs: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.pairs = pairs
        self.error = error

    async def token_pairs_observed(self, chain: str, address: str) -> tuple[Any, datetime]:
        if self.error is not None:
            raise self.error
        return self.pairs, OBSERVED


class FakeRpc:
    def __init__(self, value: tuple[bool | None, bool | None] = (True, True)) -> None:
        self.value = value
        self.calls = 0
        self.endpoint = "https://rpc.example.invalid"

    async def mint_authorities(self, address: str) -> tuple[bool | None, bool | None]:
        self.calls += 1
        return self.value


class FakeBirdeye:
    def __init__(self) -> None:
        self.calls = 0

    async def enrichment(self, address: str) -> dict[str, Any]:
        self.calls += 1
        return {"data": {"marketCap": 89000000000, "top10HolderPercent": 12.5}}


def _client(
    dex: FakeDexScreener,
    rpc: FakeRpc | None = None,
    birdeye: FakeBirdeye | None = None,
    key_provider: Any = None,
) -> TokenMetadataClient:
    return TokenMetadataClient(
        dexscreener=dex,
        rpc=rpc,
        birdeye=birdeye,
        key_provider=key_provider,
        now=lambda: OBSERVED,
    )


def test_without_a_user_key_no_keyed_source_is_called() -> None:
    birdeye = FakeBirdeye()
    observation = asyncio.run(
        _client(FakeDexScreener([_pair()]), FakeRpc(), birdeye).fetch("solana", LAUNCH)
    )
    assert birdeye.calls == 0
    assert observation.market_cap_usd is None
    assert observation.mint_revoked is True
    rpc_source = observation.source_for("rpc")
    assert rpc_source is not None
    assert rpc_source.name == "solana-rpc"
    assert observation.price_usd == Decimal("0.00042")


def test_with_a_user_key_the_keyed_source_enriches() -> None:
    birdeye = FakeBirdeye()
    observation = asyncio.run(
        _client(
            FakeDexScreener([_pair()]),
            FakeRpc(),
            birdeye,
            key_provider=lambda: asyncio.sleep(0, result=TEST_KEY),
        ).fetch("solana", LAUNCH)
    )
    assert birdeye.calls == 1
    assert observation.market_cap_usd == Decimal("89000000000")
    market_source = observation.source_for("market")
    assert market_source is not None
    assert market_source.name == "birdeye"
    assert observation.price_usd == Decimal("0.00042")
    windows_source = observation.source_for("windows")
    assert windows_source is not None
    assert windows_source.name == "dexscreener"


def test_a_source_outage_keeps_known_values_and_names_the_reason() -> None:
    observation = asyncio.run(
        _client(
            FakeDexScreener([], error=UpstreamError("offline")),
            FakeRpc((None, None)),
        ).fetch("solana", LAUNCH)
    )
    assert observation.price_usd is None
    assert observation.source is None
    assert "UNAVAILABLE:dexscreener:UPSTREAM_ERROR" in observation.degraded
    assert "UNAVAILABLE:public-rpc" not in observation.degraded


def test_birdeye_client_has_no_keyless_fallback() -> None:
    assert BirdeyeClient.keyed_base_url is not None
    assert BirdeyeClient.base_url == BirdeyeClient.keyed_base_url
