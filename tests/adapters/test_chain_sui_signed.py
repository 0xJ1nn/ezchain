"""Sui's reviewed signed broadcast shape and the minimal signed-transport extension.

`sui_executeTransactionBlock` needs the signed bytes, a serialized signature and the
signer's public key. The old signer output carried the first two only, so the signed
transport gains one optional *public* field; no key handling is invented in an adapter.
Every request here is built in-process and answered by ``httpx.MockTransport``: no funded
key, no live RPC and no transaction is sent. Signature and public-key bytes never appear
in a receipt, an exception message or a repr.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from ezchain_adapters.chains.broadcast import (
    BroadcastError,
    BroadcastReceipt,
    ChainBroadcaster,
    sui_spec,
)
from ezchain_adapters.chains.registry import support_for
from ezchain_core import ApprovedIntent, SignedTransaction

# Non-secret, obviously-invalid constants: a 32-byte Ed25519-shaped public key and a
# 64-byte Ed25519-shaped signature. Neither is a real key nor signs anything.
PUBLIC_KEY = bytes((0x42,)) * 32
SIGNATURE = bytes((0x43,)) * 64
RAW = b"sui-bcs-transaction-bytes"
KEY = "idem-sui-signed-0001"


def signed(*, public_key: bytes | None = PUBLIC_KEY) -> SignedTransaction:
    import hashlib

    return SignedTransaction(
        raw=RAW,
        signature=SIGNATURE,
        approved=ApprovedIntent(
            chain="sui",
            cluster="mainnet",
            mint="0x2::sui::SUI",
            side="buy",
            amount=Decimal("10"),
            min_out=Decimal("1"),
            recipient="0xsui-recipient",
            program_id="0xsui-package",
            idempotency_key=KEY,
        ),
        payload_digest=hashlib.sha256(RAW).hexdigest(),
        public_key=public_key,
    )


def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_signed_transport_carries_a_public_key_without_leaking_it() -> None:
    item = signed()
    assert item.public_key == PUBLIC_KEY
    rendered = repr(item)
    assert "0x42" not in rendered
    assert base64.b64encode(PUBLIC_KEY).decode("ascii") not in rendered
    assert base64.b64encode(SIGNATURE).decode("ascii") not in rendered


def test_signed_transport_keeps_the_field_public_only() -> None:
    # The core contract only says "public bytes, not secret material"; a scheme-specific
    # length is the venue adapter's job.
    assert signed(public_key=b"short-but-public").public_key == b"short-but-public"
    with pytest.raises(ValueError):
        signed(public_key=b"x" * 65)
    with pytest.raises(ValueError):
        signed(public_key="not-bytes")  # type: ignore[arg-type]


@pytest.mark.parametrize("public_key", [None, b"too-short"])
def test_missing_or_wrong_scheme_public_key_refuses_with_a_typed_error(
    public_key: bytes | None,
) -> None:
    spec = sui_spec()
    with pytest.raises(BroadcastError) as caught:
        spec.params(signed(public_key=public_key))
    assert caught.value.code == "BROADCAST_PUBLIC_KEY_REQUIRED"
    assert caught.value.ambiguous is False
    assert "0x42" not in str(caught.value)


def test_request_shape_follows_the_documented_sui_execute_transaction_block_schema() -> None:
    """Four ordered params: tx bytes, serialized signatures, options, request type.

    Sui's docs define the serialized signature as `flag || sig || pk`; the modern method
    has no separate public-key array (that belonged to the removed `sui_executeTransaction`).
    """
    params = sui_spec().params(signed())
    assert len(params) == 4
    assert params[0] == base64.b64encode(RAW).decode("ascii")
    serialized = b"\x00" + SIGNATURE + PUBLIC_KEY
    assert params[1] == [base64.b64encode(serialized).decode("ascii")]
    assert isinstance(params[2], dict) and params[2].get("showEffects") is True
    assert params[3] == "WaitForLocalExecution"


def test_an_already_serialized_signature_is_not_assembled_twice() -> None:
    from dataclasses import replace

    item = signed()
    already = replace(item, signature=b"\x00" + SIGNATURE + PUBLIC_KEY)
    params = sui_spec().params(already)
    assert params[1] == [base64.b64encode(already.signature).decode("ascii")]


def test_an_unknown_signature_encoding_is_refused_not_guessed() -> None:
    from dataclasses import replace

    item = signed()
    odd = replace(item, signature=b"\x07" + SIGNATURE)
    import pytest as _pytest

    with _pytest.raises(BroadcastError) as caught:
        sui_spec().params(odd)
    assert caught.value.ambiguous is False


@pytest.mark.asyncio
async def test_sui_broadcast_is_submitted_only_under_mocked_transport() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json={"result": {"digest": "sui-digest-0001", "effects": {"status": "success"}}}
        )

    adapter = ChainBroadcaster(
        chain="sui",
        spec=sui_spec(),
        rpc_url="https://rpc.invalid",
        client=client(handler),
    )
    receipt = await adapter.submit_signed(signed(), KEY)
    assert receipt == BroadcastReceipt("sui", "sui-digest-0001", KEY)
    assert receipt.status == "submitted"
    assert not hasattr(receipt, "quantity")
    body = json.loads(calls[0].content)
    assert body["method"] == "sui_executeTransactionBlock"
    assert len(body["params"]) == 4


@pytest.mark.asyncio
async def test_sui_cannot_be_submitted_as_unsigned_bytes() -> None:
    adapter = ChainBroadcaster(
        chain="sui",
        spec=sui_spec(),
        rpc_url="https://rpc.invalid",
        client=client(lambda request: httpx.Response(200, json={"result": "unused"})),
    )
    with pytest.raises(BroadcastError) as caught:
        await adapter.submit(RAW, KEY)
    assert caught.value.code == "BROADCAST_UNSUPPORTED"


def test_registry_exposes_the_reviewed_sui_signed_shape() -> None:
    support = support_for("sui")
    assert support is not None
    assert support.broadcast is not None
    assert support.broadcast().method == "sui_executeTransactionBlock"
