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
:func:`unsupported_broadcast`. Sui's reviewed shape is built from the signer's *public*
key now carried by the signed transport; without it a Sui submission is refused before
any request is sent.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ezchain_core import SignedTransaction
from ezchain_core.reason_codes import BROADCAST_PUBLIC_KEY_REQUIRED, BROADCAST_UNSUPPORTED

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

    This is the fail-closed path for a chain with no reviewed request shape. Sui used to
    be the concrete case: its signed request needs the signer's public key, which the old
    signed transport did not carry. The transport now carries that public field and Sui has
    a reviewed shape (``sui_spec``), so an unsigned or keyless Sui submission is refused by
    :meth:`ChainBroadcaster.submit` instead of claiming support it cannot execute.
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
    """An unsigned chain-specific request/response shape. Data, not a shared-code branch.

    Most chains use JSON-RPC at the configured endpoint. TON's reviewed v2 API is a REST
    POST with a JSON body and a path suffix, so ``envelope`` and ``path_suffix`` keep that
    difference in data instead of a chain branch in shared code.
    """

    method: str
    params: Callable[[bytes, str], Any]
    hash_from_result: Callable[[Any], str]
    envelope: Callable[[str, Any], dict[str, Any]] | None = None
    path_suffix: str = ""


@dataclass(frozen=True)
class SignedBroadcastSpec:
    """A request shape built from signed bytes plus the signer's public key.

    Sui is the concrete case: ``sui_executeTransactionBlock`` takes a serialized signature
    of ``flag || sig || pk``, so the adapter reads the *public* key carried by
    :class:`SignedTransaction` to assemble it. It never signs, derives or stores a key, and
    it never puts signature or public-key bytes in a receipt or error.
    """

    method: str
    params: Callable[[SignedTransaction], list[Any]]
    hash_from_result: Callable[[Any], str]


def _json_rpc(method: str, params: Any) -> dict[str, Any]:
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
            {"encoding": "base64", "preflightCommitment": "finalized"},
        ],
        hash_from_result=_string_result,
    )


def evm_spec() -> BroadcastSpec:
    return BroadcastSpec(
        method="eth_sendRawTransaction",
        params=lambda raw, key: ["0x" + raw.hex()],
        hash_from_result=_string_result,
    )


def _ton_hash(result: Any) -> str:
    """TON v2 ``sendBocReturnHash`` result: ``{@type, hash, hash_norm}``."""
    if not isinstance(result, dict):
        raise ValueError("the venue returned no message info")
    digest = result.get("hash")
    if not isinstance(digest, str) or not digest:
        raise ValueError("the venue returned no message hash")
    return digest


def ton_spec() -> BroadcastSpec:
    """TON's reviewed v2 REST shape; plain ``sendBoc`` has no hash to receipt."""
    return BroadcastSpec(
        method="sendBocReturnHash",
        params=lambda raw, key: [base64.b64encode(raw).decode("ascii")],
        hash_from_result=_ton_hash,
        # The documented v2 body is {"boc": "<base64>"} at /api/v2/sendBocReturnHash.
        envelope=lambda method, params: {"boc": params[0]},
        path_suffix="sendBocReturnHash",
    )


def cosmos_spec() -> BroadcastSpec:
    def tx_hash(result: Any) -> str:
        if not isinstance(result, dict):
            raise ValueError("the venue returned no transaction response")
        # CometBFT's CheckTx response carries a string `code` and `hash`; a non-zero code
        # is a definitive rejection, not an ambiguous submission. `txhash` is a different
        # field of the Cosmos SDK REST GetTxResponse and is not returned here.
        code = result.get("code")
        if code not in (None, "0", 0):
            raise BroadcastError("the venue rejected the transaction", ambiguous=False)
        digest = result.get("hash")
        if not isinstance(digest, str) or not digest:
            raise ValueError("the venue returned no transaction hash")
        return digest

    return BroadcastSpec(
        method="broadcast_tx_sync",
        # CometBFT accepts byte args as quoted strings or 0x-prefixed hex, not base64.
        params=lambda raw, key: ["0x" + raw.hex()],
        hash_from_result=tx_hash,
    )


# Sui's Ed25519 scheme flag. It prefixes both the serialized signature and the public key
# on the wire; it is public metadata, not key material.
_SUI_ED25519_FLAG = b"\x00"


def _sui_params(signed: SignedTransaction) -> list[Any]:
    """The four documented ``sui_executeTransactionBlock`` params, in order.

    Sui's serialized signature is ``flag || sig || pk`` (Sui docs, "Signatures"). The signer
    returns a bare Ed25519 signature and its public key travels in the signed transport;
    concatenating those two *public* values here is serialization, not key handling. There
    is no separate public-key array on this method — older ``sui_executeTransaction`` had
    one, but the modern method assembles the serialized signature client-side.
    """
    public_key = signed.public_key
    if public_key is None or len(public_key) != 32:
        raise BroadcastError(
            "sui needs the signer's Ed25519 public key",
            code=BROADCAST_PUBLIC_KEY_REQUIRED,
            ambiguous=False,
        )
    signature = signed.signature
    if (
        len(signature) == 97
        and signature[0] == _SUI_ED25519_FLAG[0]
        and signature[-32:] == public_key
    ):
        # A signer that already returns Sui's serialized signature needs no assembly.
        serialized = signature
    elif len(signature) == 64:
        serialized = _SUI_ED25519_FLAG + signature + public_key
    else:
        # Ambiguous encoding: never guess, never send a signature in the wrong form.
        raise BroadcastError("unsupported Sui signature encoding", ambiguous=False)
    return [
        base64.b64encode(signed.raw).decode("ascii"),
        [base64.b64encode(serialized).decode("ascii")],
        {"showEffects": True},
        "WaitForLocalExecution",
    ]


def _sui_digest(result: Any) -> str:
    if not isinstance(result, dict):
        raise ValueError("the venue returned no transaction response")
    digest = result.get("digest")
    if not isinstance(digest, str) or not digest:
        raise ValueError("the venue returned no transaction digest")
    return digest


def sui_spec() -> SignedBroadcastSpec:
    """Sui's reviewed signed request shape (never a mock-only unsigned shape)."""
    return SignedBroadcastSpec(
        method="sui_executeTransactionBlock",
        params=_sui_params,
        hash_from_result=_sui_digest,
    )


class ChainBroadcaster:
    """Submit signed bytes once per idempotency key, with a hard deadline."""

    def __init__(
        self,
        *,
        chain: str,
        spec: BroadcastSpec | SignedBroadcastSpec,
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
        """Broadcast unsigned bytes once; a replay of the same key and bytes is idempotent."""
        if not isinstance(self._spec, BroadcastSpec):
            raise BroadcastError(
                f"{self.chain} needs a signed request", code=BROADCAST_UNSUPPORTED, ambiguous=False
            )
        if not raw or not idempotency_key:
            raise BroadcastError("nothing to broadcast", ambiguous=False)
        return await self._dispatch(raw, idempotency_key, self._spec.params(raw, idempotency_key))

    async def submit_signed(
        self, signed: SignedTransaction, idempotency_key: str
    ) -> BroadcastReceipt:
        """Broadcast a signed request once; only public signed-transport fields are read."""
        if not isinstance(self._spec, SignedBroadcastSpec):
            raise BroadcastError(
                f"{self.chain} takes unsigned bytes", code=BROADCAST_UNSUPPORTED, ambiguous=False
            )
        if not idempotency_key:
            raise BroadcastError("nothing to broadcast", ambiguous=False)
        # ``params`` raises a typed refusal (never a raw secret) when the public key is
        # missing, before any request reaches the venue.
        params = self._spec.params(signed)
        return await self._dispatch(signed.raw, idempotency_key, params)

    async def _dispatch(
        self, raw: bytes, idempotency_key: str, params: list[Any]
    ) -> BroadcastReceipt:
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
        build_body = getattr(self._spec, "envelope", None) or _json_rpc
        body = build_body(self._spec.method, params)
        suffix = getattr(self._spec, "path_suffix", "")
        url = self._rpc_url.rstrip("/") + "/" + suffix if suffix else self._rpc_url
        try:
            response = await asyncio.wait_for(
                self._client.post(
                    url,
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
    "SignedBroadcastSpec",
    "cosmos_spec",
    "evm_spec",
    "solana_spec",
    "sui_spec",
    "ton_spec",
    "unsupported_broadcast",
]
