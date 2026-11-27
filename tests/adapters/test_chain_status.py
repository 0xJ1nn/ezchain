"""Read-only confirmation/status readers with recorded public RPC responses.

Each family's request shape and four typed outcomes are pinned against documented RPC
schemas, answered by ``httpx.MockTransport``. A status read never returns a Fill, a balance
delta or a journal entry, and no live node is contacted. Response bodies are the public
examples from the chains' RPC documentation; transaction hashes are public examples, not
secrets or funded keys.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ezchain_adapters.chains.status import (
    CONFIRMATION_ACTIONS,
    CONFIRMATION_STATES,
    ChainStatusReader,
    Confirmation,
    cosmos_status_spec,
    evm_status_spec,
    normalize_confirmation,
    solana_status_spec,
    sui_status_spec,
    ton_status_spec,
)

HASH = "public-example-transaction-hash"


def test_confirmation_states_have_one_vocabulary_and_an_action_each() -> None:
    assert set(CONFIRMATION_STATES) == {"pending", "confirmed", "failed", "unknown"}
    assert set(CONFIRMATION_ACTIONS) == set(CONFIRMATION_STATES)
    assert all(CONFIRMATION_ACTIONS[state].strip() for state in CONFIRMATION_STATES)
    for legacy in ("success", "finalized"):
        assert normalize_confirmation(legacy) == "confirmed"
    assert normalize_confirmation("reverted") == "failed"
    assert normalize_confirmation("processed") == "pending"
    for missing in (None, "", "  ", "made_up"):
        assert normalize_confirmation(missing) == "unknown"


def reader(spec: Any, handler: Any, chain: str = "solana") -> ChainStatusReader:
    return ChainStatusReader(
        chain=chain,
        spec=spec,
        rpc_url="https://rpc.invalid",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        timeout_seconds=0.05,
    )


def reply(payload: dict[str, Any], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spec", "method", "chain"),
    [
        (solana_status_spec(), "getSignatureStatuses", "solana"),
        (evm_status_spec(), "eth_getTransactionReceipt", "ethereum"),
        (sui_status_spec(), "sui_getTransactionBlock", "sui"),
        (cosmos_status_spec(), "tx", "cosmos"),
    ],
)
async def test_request_shape_follows_the_documented_rpc_schema(
    spec: Any, method: str, chain: str
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply({"result": None})

    item = await reader(spec, handler, chain).status(HASH)
    assert item.outcome in ("confirmed", "failed", "pending", "unknown")
    body = json.loads(calls[0].content)
    assert body["method"] == method
    assert body["jsonrpc"] == "2.0"
    assert isinstance(body["params"], (list, dict))


@pytest.mark.asyncio
async def test_solana_four_outcomes() -> None:
    finalized = {
        "result": {
            "value": [
                {
                    "err": None,
                    "confirmationStatus": "finalized",
                    "confirmations": None,
                    "slot": 1,
                }
            ]
        }
    }
    failed = {"result": {"value": [{"err": {"InstructionError": [0, 1]}, "slot": 1}]}}
    processed = {"result": {"value": [{"err": None, "confirmationStatus": "processed"}]}}
    cases = [
        (reply(finalized), "confirmed"),
        (reply(failed), "failed"),
        (reply(processed), "pending"),
        (reply({"result": {"value": [None]}}), "pending"),
        (reply({"result": {}}), "unknown"),
        (reply({"error": {"code": -32602}}), "unknown"),
    ]
    for response, expected in cases:
        item = await reader(
            solana_status_spec(), lambda request, response=response: response
        ).status(HASH)
        assert item.outcome == expected


@pytest.mark.asyncio
async def test_evm_four_outcomes() -> None:
    cases = [
        (reply({"result": {"status": "0x1", "blockNumber": "0x1"}}), "confirmed"),
        (reply({"result": {"status": "0x0", "blockNumber": "0x1"}}), "failed"),
        (reply({"result": None}), "pending"),
        (reply({"result": {"blockNumber": "0x1"}}), "unknown"),
    ]
    for response, expected in cases:
        item = await reader(
            evm_status_spec(), lambda request, response=response: response, "ethereum"
        ).status(HASH)
        assert item.outcome == expected


@pytest.mark.asyncio
async def test_sui_four_outcomes() -> None:
    def result(state: str) -> dict[str, Any]:
        return {"result": {"effects": {"status": {"status": state}}}}

    cases = [
        (reply(result("success")), "confirmed"),
        (reply(result("failure")), "failed"),
        (reply({"result": None}), "pending"),
        (reply({"result": {"effects": {"status": {}}}}), "unknown"),
    ]
    for response, expected in cases:
        item = await reader(
            sui_status_spec(), lambda request, response=response: response, "sui"
        ).status(HASH)
        assert item.outcome == expected


@pytest.mark.asyncio
async def test_cosmos_four_outcomes() -> None:
    """CometBFT `tx` has `height`/`tx_result`, not the SDK REST `tx_response`."""
    cases = [
        (
            reply(
                {
                    "result": {
                        "height": "1000",
                        "tx_result": {"log": '[{"msg_index":"0","success":true}]'},
                    }
                }
            ),
            "confirmed",
        ),
        (
            reply({"result": {"height": "1000", "tx_result": {"code": 5}}}),
            "failed",
        ),
        (reply({"error": {"code": -32603, "message": "tx not found"}}), "pending"),
        (reply({"result": {"height": "0", "tx_result": {}}}), "pending"),
        (reply({"error": {"code": -32603, "message": "internal error"}}), "unknown"),
    ]
    for response, expected in cases:
        item = await reader(
            cosmos_status_spec(), lambda request, response=response: response, "cosmos"
        ).status(HASH)
        assert item.outcome == expected


@pytest.mark.asyncio
async def test_ton_outcomes_and_request_shape() -> None:
    """TON v2 getTransactions proves inclusion by hash match, never execution success."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return reply(
            {
                "ok": True,
                "result": [
                    {
                        "@type": "ext.transaction",
                        "transaction_id": {"hash": HASH, "lt": "1"},
                    }
                ],
            }
        )

    item = await reader(ton_status_spec("0:public-example-address"), handler, "ton").status(HASH)
    assert item.outcome == "confirmed"
    body = json.loads(calls[0].content)
    assert body["address"] == "0:public-example-address"
    assert body["hash"] == HASH

    no_match = await reader(
        ton_status_spec("0:public-example-address"),
        lambda request: reply({"ok": True, "result": []}),
        "ton",
    ).status(HASH)
    assert no_match.outcome == "pending"

    malformed = await reader(
        ton_status_spec("0:public-example-address"),
        lambda request: reply({"ok": False}),
        "ton",
    ).status(HASH)
    assert malformed.outcome == "unknown"


@pytest.mark.asyncio
async def test_ambiguous_transport_and_malformed_answers_are_unknown() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise TimeoutError

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    for handler in (timeout, broken, lambda request: reply({"result": None}, 503)):
        item = await reader(solana_status_spec(), handler).status(HASH)
        assert item.outcome == "unknown"
        assert item.detail


@pytest.mark.asyncio
async def test_status_observation_has_no_fill_fields() -> None:
    item = await reader(
        solana_status_spec(), lambda request: reply({"result": {"value": [None]}})
    ).status(HASH)
    assert isinstance(item, Confirmation)
    assert item.tx_hash == HASH
    for field in ("order_id", "quantity", "notional_usd", "fee_usd", "journal"):
        assert not hasattr(item, field)
