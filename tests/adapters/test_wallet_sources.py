"""Wallet activity sources: normalization, identity, limits and fail-closed values."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ezchain_adapters.wallet_sources import (
    EvmExplorerSource,
    FirehoseLaunchBuyersSource,
    SolanaPublicRpcSource,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
PLACEHOLDER_SOL = "So11111111111111111111111111111111111111111"
PLACEHOLDER_EVM = "0x000000000000000000000000000000000000dEaD"
PLACEHOLDER_TOKEN = "TokenPLACEHOLDER0000000000000000000000000"


def test_solana_source_parses_a_signature_and_skips_malformed_ones() -> None:
    calls: list[tuple[str, list[Any]]] = []

    async def rpc(method: str, params: list[Any]) -> dict[str, Any]:
        calls.append((method, params))
        if method == "getSignaturesForAddress":
            return {
                "result": [
                    {"signature": "sig-ok", "blockTime": int(NOW.timestamp()), "err": None},
                    {"signature": "sig-failed", "blockTime": int(NOW.timestamp()), "err": {"x": 1}},
                    {
                        "signature": "sig-old",
                        "blockTime": int((NOW - timedelta(days=40)).timestamp()),
                        "err": None,
                    },
                ]
            }
        return {
            "result": {
                "meta": {"preBalances": [1_000_000_000, 0], "postBalances": [900_000_000, 5]},
                "transaction": {
                    "message": {
                        "instructions": [
                            {
                                "program": "spl-token",
                                "parsed": {
                                    "type": "transfer",
                                    "info": {
                                        "mint": PLACEHOLDER_TOKEN,
                                        "tokenAmount": {"amount": "5", "decimals": 0},
                                    },
                                },
                            }
                        ]
                    }
                },
            }
        }

    source = SolanaPublicRpcSource(rpc)
    swaps = asyncio.run(
        source.fetch_swaps(
            chain="solana",
            address=PLACEHOLDER_SOL,
            since=NOW - timedelta(days=30),
            limit=10,
        )
    )
    assert [swap.source_tx for swap in swaps] == ["sig-ok"]
    assert swaps[0].side == "buy"
    assert swaps[0].source == "solana-rpc"
    assert swaps[0].token_address == PLACEHOLDER_TOKEN
    # No USD value on the wire: unknown stays unknown, never zero.
    assert swaps[0].amount_usd is None
    # The failed and the out-of-window signature are never fetched in detail.
    assert [params[0] for method, params in calls if method == "getTransaction"] == ["sig-ok"]


def test_solana_source_is_cached_and_rate_limited() -> None:
    fetches = 0

    async def rpc(method: str, params: list[Any]) -> dict[str, Any]:
        nonlocal fetches
        fetches += 1
        return {"result": []}

    source = SolanaPublicRpcSource(rpc)
    kwargs = {
        "chain": "solana",
        "address": PLACEHOLDER_SOL,
        "since": NOW - timedelta(days=1),
        "limit": 5,
    }
    assert asyncio.run(source.fetch_swaps(**kwargs)) == []
    assert asyncio.run(source.fetch_swaps(**kwargs)) == []
    # One signatures call, cached on the second read; no detail calls for an empty page.
    assert fetches == 1
    assert source.cost == "free"


def test_evm_source_requires_the_persons_own_key() -> None:
    with pytest.raises(ValueError):
        EvmExplorerSource(api_key="")


def test_evm_source_normalizes_explorer_rows_with_a_key() -> None:
    async def get(path: str, params: dict[str, Any]) -> Any:
        assert params["apikey"] == "TEST_ONLY_KEY_DO_NOT_USE"
        return {
            "result": [
                {
                    "hash": "0xabc",
                    "timeStamp": str(int(NOW.timestamp())),
                    "side": "buy",
                    "contractAddress": PLACEHOLDER_TOKEN,
                    "tokenSymbol": "TKN",
                    "tokenAmount": "12.5",
                    "usdValue": "25.00",
                },
                {"hash": "0xbad", "timeStamp": "not-a-number", "side": "buy"},
            ]
        }

    source = EvmExplorerSource(api_key="TEST_ONLY_KEY_DO_NOT_USE", get=get)
    swaps = asyncio.run(
        source.fetch_swaps(
            chain="ethereum",
            address=PLACEHOLDER_EVM,
            since=NOW - timedelta(days=1),
            limit=10,
        )
    )
    assert source.cost == "paid"
    assert len(swaps) == 1
    assert swaps[0].source_tx == "0xabc"
    assert swaps[0].amount_usd == Decimal("25.00")
    assert swaps[0].token_symbol == "TKN"


def test_firehose_source_yields_only_labelled_buyers_and_dedupes() -> None:
    events = [
        {
            "kind": "trade",
            "direction": "buy",
            "user": PLACEHOLDER_SOL,
            "signature": "s1",
            "mint": PLACEHOLDER_TOKEN,
            "sol": "1.5",
        },
        {
            "kind": "trade",
            "direction": "sell",
            "user": PLACEHOLDER_SOL,
            "signature": "s2",
            "mint": PLACEHOLDER_TOKEN,
            "sol": "1.5",
        },
        {
            "kind": "trade",
            "direction": "buy",
            "user": PLACEHOLDER_SOL,
            "signature": "s1",
            "mint": PLACEHOLDER_TOKEN,
            "sol": "1.5",
        },
        {"kind": "create", "user": PLACEHOLDER_SOL, "signature": "s3", "mint": PLACEHOLDER_TOKEN},
    ]
    source = FirehoseLaunchBuyersSource(events=events)
    swaps = source.buyers()
    assert [swap.source_tx for swap in swaps] == ["s1"]
    assert swaps[0].source == "pumpfun-firehose"
    assert swaps[0].side == "buy"
    assert swaps[0].amount_tokens == Decimal("1.5")
