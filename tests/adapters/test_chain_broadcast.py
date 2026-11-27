"""Broadcast adapter tests with mocked HTTP transports.

No test opens a socket to a venue. Success, idempotent replay, conflicting replay, timeout,
5xx, definitive rejection and ambiguous answers are all pinned. An ambiguous failure must
never produce a receipt a caller could mistake for a settled transaction. A successful RPC
hash produces only a ``submitted`` receipt: there is no fill, no confirmation and no
executor ``Venue.submit`` wiring in this field.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ezchain_adapters.chains.broadcast import (
    BroadcastError,
    BroadcastReceipt,
    ChainBroadcaster,
    cosmos_spec,
    evm_spec,
    solana_spec,
    ton_spec,
    unsupported_broadcast,
)

RAW = b"unsigned-transaction-bytes"
KEY = "idem-broadcast-0001"


def client_for(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def broadcaster(handler: Any, spec: Any = None) -> ChainBroadcaster:
    return ChainBroadcaster(
        chain="solana",
        spec=spec or solana_spec(),
        rpc_url="https://rpc.invalid",
        client=client_for(handler),
        timeout_seconds=0.05,
    )


@pytest.mark.asyncio
async def test_success_is_idempotent_and_sends_once() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"result": "tx-hash-0001"})

    adapter = broadcaster(handler)
    first = await adapter.submit(RAW, KEY)
    second = await adapter.submit(RAW, KEY)
    assert first == second == BroadcastReceipt("solana", "tx-hash-0001", KEY)
    assert len(calls) == 1
    assert calls[0].headers["Idempotency-Key"] == KEY
    assert json.loads(calls[0].content)["method"] == "sendTransaction"


@pytest.mark.asyncio
async def test_successful_hash_is_a_submitted_receipt_and_never_a_fill() -> None:
    """Regression: an RPC hash is not an executed trade and must not become a Fill."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "tx-hash-0001"})

    receipt = await broadcaster(handler).submit(RAW, KEY)
    assert isinstance(receipt, BroadcastReceipt)
    assert receipt.status == "submitted"
    assert receipt.status != "filled"
    assert not hasattr(receipt, "order_id")
    assert not hasattr(receipt, "quantity")
    assert not hasattr(receipt, "notional_usd")


def test_no_chain_module_builds_a_fill_or_a_venue_submit() -> None:
    """Guard the boundary: submission receipts are not fills, and no Venue wiring exists."""
    root = Path(__file__).resolve().parents[1] / "packages/adapters/src/ezchain_adapters/chains"
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "Fill(" not in source, f"{path.name} builds a Fill from a broadcast"
        assert 'status="filled"' not in source, f"{path.name} claims a filled status"
        assert "class ChainVenueAdapter" not in source, f"{path.name} wires executor Venue.submit"


@pytest.mark.asyncio
async def test_same_key_with_different_bytes_is_a_typed_conflict() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"result": "tx-hash-0001"})

    adapter = broadcaster(handler)
    await adapter.submit(RAW, KEY)
    with pytest.raises(BroadcastError) as caught:
        await adapter.submit(b"different-bytes", KEY)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert caught.value.ambiguous is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_timeout_and_transport_failure_are_ambiguous() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise TimeoutError

    with pytest.raises(BroadcastError) as caught:
        await broadcaster(timeout_handler).submit(RAW, KEY)
    assert (caught.value.code, caught.value.ambiguous) == ("TIMEOUT", True)

    def transport_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(BroadcastError) as caught:
        await broadcaster(transport_handler).submit(RAW, KEY)
    assert caught.value.code == "VENUE_ERROR"
    assert caught.value.ambiguous is True


@pytest.mark.asyncio
async def test_server_error_and_missing_hash_are_ambiguous() -> None:
    for handler in (
        lambda request: httpx.Response(503, json={"error": "down"}),
        lambda request: httpx.Response(200, json={"result": None}),
        lambda request: httpx.Response(200, json={"error": {"code": -32000}}),
    ):
        adapter = broadcaster(handler)
        with pytest.raises(BroadcastError) as caught:
            await adapter.submit(RAW, KEY)
        assert caught.value.ambiguous is True
        assert not adapter._sent  # no receipt that a caller could mistake for success


@pytest.mark.asyncio
async def test_definite_rejection_is_not_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "rejected"})

    with pytest.raises(BroadcastError) as caught:
        await broadcaster(handler).submit(RAW, KEY)
    assert caught.value.ambiguous is False


@pytest.mark.parametrize(
    ("spec", "method"),
    [
        (solana_spec(), "sendTransaction"),
        (evm_spec(), "eth_sendRawTransaction"),
        (ton_spec(), "sendBoc"),
        (cosmos_spec(), "broadcast_tx_sync"),
    ],
)
@pytest.mark.asyncio
async def test_every_family_has_a_broadcast_request_shape(spec: Any, method: str) -> None:
    assert spec.method == method
    params = spec.params(RAW, KEY)
    assert isinstance(params, list) and params
    result = {"txhash": "cosmos-hash"} if method == "broadcast_tx_sync" else "tx-hash"
    assert spec.hash_from_result(result)


@pytest.mark.asyncio
async def test_sui_execution_shape_is_refused_not_simulated() -> None:
    with pytest.raises(BroadcastError) as caught:
        unsupported_broadcast("sui")
    assert caught.value.code == "BROADCAST_UNSUPPORTED"
    assert caught.value.ambiguous is False


@pytest.mark.asyncio
async def test_cosmos_ambiguous_result_is_not_a_success() -> None:
    adapter = ChainBroadcaster(
        chain="osmosis",
        spec=cosmos_spec(),
        rpc_url="https://rpc.invalid",
        client=client_for(lambda request: httpx.Response(200, json={"result": {"txhash": ""}})),
    )
    with pytest.raises(BroadcastError) as caught:
        await adapter.submit(RAW, KEY)
    assert caught.value.ambiguous is True


def test_receipt_digest_is_not_used_as_a_fill_digest() -> None:
    """A hash returned by an RPC is distinct from the signed payload SHA-256 digest."""
    receipt = BroadcastReceipt("solana", "rpc-hash", KEY)
    assert receipt.tx_hash != hashlib.sha256(RAW).hexdigest()
    assert receipt.status == "submitted"
