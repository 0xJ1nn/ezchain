"""Broadcast adapter: one idempotent, time-bounded, fail-closed submission path.

A broadcaster is given a chain-specific :class:`BroadcastSpec` (request shape and result
parser) and an injected ``httpx.AsyncClient``; tests use ``MockTransport`` so no venue is
contacted. Submission is deduplicated by idempotency key in-process: a retry with the same
key and the same bytes returns the first receipt without a second call, and the same key
with different bytes is a conflict. A timeout, a 5xx, a transport error or a response with
no unambiguous transaction hash raises :class:`BroadcastError` with ``ambiguous=True``:
the venue may have accepted the transaction, so no caller may report success. Only a
parsed, non-empty hash from a 2xx response is a receipt, and that receipt means only
``submitted`` — an RPC accepting bytes is not an executed or confirmed transaction.

This is deliberately *not* an executor ``Venue`` implementation. There is no conversion
from a submission receipt to a ``Fill`` and no ``Venue.submit`` wiring: that stays undone
until every chain has a reviewed confirmation/status path and reconciliation. Inventing
confirmation here would put an unconfirmed claim into the ledger path, which violates the
fail-closed rule. Durable reconciliation of an ambiguous claim remains the executor's
outbox, exactly as it is today. Chains with no reviewed request shape are refused by
:func:`unsupported_broadcast`; Sui is one of them, because a signed Sui execution needs a
public key the signer contract does not carry.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ezchain_core.reason_codes import BROADCAST_UNSUPPORTED

from ..errors import AdapterError

DEFAULT_BROADCAST_TIMEOUT_SECONDS = 8.0


class BroadcastError(AdapterError):
    """A broadcast did not produce an unambiguous receipt.

    ``ambiguous`` records whether the venue may have accepted the transaction. It is
    deliberately separate from ``code``: an ambiguous failure must never be turned into a
    success or a blind resend by a caller that only looks at the code.
    """

    code = "VENUE_ERROR"

    def __init__(self, message: str, *, code: str = "VENUE_ERROR", ambiguous: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.ambiguous = ambiguous


def unsupported_broadcast(chain: str) -> None:
    """Fail closed for a chain with no reviewed broadcast request shape.

    Sui is the concrete case: `sui_executeTransactionBlock` needs signed transaction bytes,
    a signature and the signer's public key. `SignedTransaction` and `VaultSignature` carry
    no public key, and adapters must not handle key material, so no executable request can
    be built here. Refusing is safer than shipping a mock-only shape that claims support.
    """
    raise BroadcastError(
        f"broadcast is not supported for {chain}",
        code=BROADCAST_UNSUPPORTED,
        ambiguous=False,
    )


@dataclass(frozen=True)
class BroadcastReceipt:
    """A venue answer that proves one transaction was submitted, nothing more.

    ``status`` is ``submitted`` by construction. An RPC hash is not a fill: there is no
    confirmation, receipt, balance delta or journal implied by this type.
    """

    chain: str
    tx_hash: str
    idempotency_key: str
    status: str = "submitted"


@dataclass(frozen=True)
class BroadcastSpec:
    """Chain-specific request/response shape. Data, not a branch in shared code."""

    method: str
    params: Callable[[bytes, str], list[Any]]
    hash_from_result: Callable[[Any], str]


def _json_rpc(method: str, params: list[Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _string_result(result: Any) -> str:
    if not isinstance(result, str) or not result:
        raise ValueError("the venue returned no transaction hash")
    return result


def solana_spec() -> BroadcastSpec:
    return BroadcastSpec(
        method="sendTransaction",
        params=lambda raw, key: [
            base64.b64encode(raw).decode("ascii"),
            {"encoding": "base64", "preflightCommitment": "confirmed"},
        ],
        hash_from_result=_string_result,
    )


def evm_spec() -> BroadcastSpec:
    return BroadcastSpec(
        method="eth_sendRawTransaction",
        params=lambda raw, key: ["0x" + raw.hex()],
        hash_from_result=_string_result,
    )


def ton_spec() -> BroadcastSpec:
    return BroadcastSpec(
        method="sendBoc",
        params=lambda raw, key: [base64.b64encode(raw).decode("ascii")],
        hash_from_result=_string_result,
    )


def cosmos_spec() -> BroadcastSpec:
    def tx_hash(result: Any) -> str:
        if not isinstance(result, dict):
            raise ValueError("the venue returned no transaction response")
        digest = result.get("txhash")
        if not isinstance(digest, str) or not digest:
            raise ValueError("the venue returned no transaction hash")
        return digest

    return BroadcastSpec(
        method="broadcast_tx_sync",
        params=lambda raw, key: [base64.b64encode(raw).decode("ascii")],
        hash_from_result=tx_hash,
    )


class ChainBroadcaster:
    """Submit signed bytes once per idempotency key, with a hard deadline."""

    def __init__(
        self,
        *,
        chain: str,
        spec: BroadcastSpec,
        rpc_url: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = DEFAULT_BROADCAST_TIMEOUT_SECONDS,
    ) -> None:
        if not rpc_url or timeout_seconds <= 0:
            raise ValueError("a broadcaster needs an endpoint and a positive timeout")
        self.chain = chain
        self._spec = spec
        self._rpc_url = rpc_url
        self._client = client
        self._timeout = timeout_seconds
        self._sent: dict[str, tuple[bytes, BroadcastReceipt]] = {}

    async def submit(self, raw: bytes, idempotency_key: str) -> BroadcastReceipt:
        """Broadcast once; a replay of the same key and bytes returns the first receipt."""
        if not raw or not idempotency_key:
            raise BroadcastError("nothing to broadcast", ambiguous=False)
        prior = self._sent.get(idempotency_key)
        if prior is not None:
            sent, receipt = prior
            if sent != raw:
                raise BroadcastError(
                    "the idempotency key names a different transaction",
                    code="IDEMPOTENCY_CONFLICT",
                    ambiguous=False,
                )
            return receipt
        body = _json_rpc(self._spec.method, self._spec.params(raw, idempotency_key))
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    self._rpc_url,
                    json=body,
                    headers={"Idempotency-Key": idempotency_key},
                ),
                timeout=self._timeout,
            )
        except TimeoutError:
            # The request may already be on chain: a timeout is never a success and never
            # a reason to resend with a fresh nonce.
            raise BroadcastError("broadcast timed out", code="TIMEOUT", ambiguous=True) from None
        except httpx.HTTPError:
            raise BroadcastError("broadcast transport failed", ambiguous=True) from None
        if response.status_code >= 500:
            raise BroadcastError("the venue was unavailable", ambiguous=True)
        if response.status_code >= 400:
            raise BroadcastError("the venue rejected the transaction", ambiguous=False)
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                raise ValueError("the venue returned an error")
            tx_hash = self._spec.hash_from_result(payload.get("result"))
        except (ValueError, TypeError):
            raise BroadcastError(
                "the venue did not confirm a transaction", ambiguous=True
            ) from None
        receipt = BroadcastReceipt(self.chain, tx_hash, idempotency_key)
        self._sent[idempotency_key] = (raw, receipt)
        return receipt


__all__ = [
    "BroadcastError",
    "BroadcastReceipt",
    "BroadcastSpec",
    "ChainBroadcaster",
    "cosmos_spec",
    "evm_spec",
    "solana_spec",
    "ton_spec",
    "unsupported_broadcast",
]
