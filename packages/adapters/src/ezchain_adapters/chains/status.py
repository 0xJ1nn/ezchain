"""Read-only per-chain confirmation/status adapters.

A status read answers one narrow question: what can a public RPC prove about a transaction
hash? The typed outcomes are ``confirmed``, ``failed``, ``pending`` and ``unknown``; an
unreadable or malformed answer is ``unknown``, never an assumed success and never a fill.
The reader holds no key, signs nothing, writes no ledger and returns no quantity, price or
fee. It is one input a reconciler may consult, not a venue implementation.

Request shapes follow the chains' documented RPC schemas. Tests answer them with mocked
HTTP transports and recorded public responses; no live node is contacted here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import httpx

from ezchain_core import utcnow

Outcome = Literal["confirmed", "failed", "pending", "unknown"]

# One declared state set, one writer (Confirmation.__post_init__), one next action per
# state. A read-only observation is not a persisted lifecycle: the outbox owns transitions
# of a claim, and these values only report what chain evidence can prove right now.
CONFIRMATION_STATES: Final[tuple[str, ...]] = ("pending", "confirmed", "failed", "unknown")
CONFIRMATION_ACTIONS: Final[Mapping[str, str]] = {
    "pending": "read status again after the chain's finality window; never journal a hash",
    "confirmed": "require a bound receipt before any reconciliation journal",
    "failed": "resolve an unjournaled claim as never filled; never blindly resend",
    "unknown": "treat the read as unavailable and leave the claim pending",
}
_ALIASES: Final[Mapping[str, str]] = {
    "success": "confirmed",
    "finalized": "confirmed",
    "reverted": "failed",
    "rejected": "failed",
    "error": "failed",
    "processed": "pending",
    "waiting": "pending",
    "not_found": "unknown",
    "": "unknown",
}

DEFAULT_STATUS_TIMEOUT_SECONDS = 8.0


def normalize_confirmation(value: object) -> Outcome:
    """Read any legacy spelling through one canonical vocabulary.

    Missing, blank and unrecognised values are `unknown` — never an assumed pass and never
    a new status string. Writers construct :class:`Confirmation`, which normalises once.
    """
    token = value.strip().lower() if isinstance(value, str) else ""
    resolved = _ALIASES.get(token, token)
    if resolved in CONFIRMATION_STATES:
        return resolved  # type: ignore[return-value]
    return "unknown"


@dataclass(frozen=True)
class Confirmation:
    """One typed status observation, with the public hash it was read for."""

    chain: str
    tx_hash: str
    outcome: Outcome
    observed_at: datetime
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", normalize_confirmation(self.outcome))


@dataclass(frozen=True)
class StatusSpec:
    """One documented status request shape and its response classifier.

    JSON-RPC is the default envelope. TON's v2 API is a REST POST with a JSON body and a
    path suffix; that difference is data, not a branch in the reader.
    """

    method: str
    params: Callable[[str], Any]
    classify: Callable[[dict[str, Any], str], Outcome]
    envelope: Callable[[str, Any], dict[str, Any]] | None = None
    path_suffix: str = ""


def _rpc(method: str, params: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _classification(chain: str, tx_hash: str, payload: dict[str, Any], spec: StatusSpec) -> Outcome:
    try:
        return spec.classify(payload, tx_hash)
    except (AttributeError, KeyError, TypeError, ValueError):
        return "unknown"


def _solana_status(payload: dict[str, Any], _tx_hash: str) -> Outcome:
    if payload.get("error"):
        return "unknown"
    value = (payload.get("result") or {}).get("value")
    if not isinstance(value, list) or not value:
        return "unknown"
    entry = value[0]
    if entry is None:
        # A recent transaction can be absent from the status cache even when it will land.
        return "pending"
    if not isinstance(entry, dict):
        return "unknown"
    if entry.get("err") is not None:
        return "failed"
    state = entry.get("confirmationStatus")
    if state in ("confirmed", "finalized"):
        return "confirmed"
    if state == "processed":
        return "pending"
    return "unknown"


def _evm_status(payload: dict[str, Any], _tx_hash: str) -> Outcome:
    if payload.get("error"):
        return "unknown"
    receipt = payload.get("result")
    if receipt is None:
        # No receipt yet: the transaction has not been mined. Absence is not a failure.
        return "pending"
    if not isinstance(receipt, dict):
        return "unknown"
    status = receipt.get("status")
    if status in ("0x1", "1", 1):
        return "confirmed"
    if status in ("0x0", "0", 0):
        return "failed"
    return "unknown"


def _sui_status(payload: dict[str, Any], _tx_hash: str) -> Outcome:
    if payload.get("error"):
        return "unknown"
    result = payload.get("result")
    if result is None:
        return "pending"
    if not isinstance(result, dict):
        return "unknown"
    effects = result.get("effects")
    if not isinstance(effects, dict) or not isinstance(effects.get("status"), dict):
        return "unknown"
    state = effects["status"].get("status")
    if state == "success":
        return "confirmed"
    if state == "failure":
        return "failed"
    return "unknown"


def _cosmos_status(payload: dict[str, Any], _tx_hash: str) -> Outcome:
    error = payload.get("error")
    if error:
        message = str(error).lower()
        # CometBFT reports an unknown hash as an error rather than a null result.
        return "pending" if "not found" in message else "unknown"
    result = payload.get("result")
    if not isinstance(result, dict):
        return "unknown"
    height = result.get("height")
    try:
        included = int(height) > 0 if isinstance(height, (str, int)) else False
    except ValueError:
        included = False
    if not included:
        return "pending"
    # The CometBFT `tx` response has `tx_result`, not the SDK REST `tx_response`.
    tx_result = result.get("tx_result")
    if not isinstance(tx_result, dict):
        return "unknown"
    code = tx_result.get("code")
    if code is not None:
        return "confirmed" if code in (0, "0") else "failed"
    log = tx_result.get("log")
    if isinstance(log, str):
        normalised = log.replace(" ", "").lower()
        if '"success":true' in normalised:
            return "confirmed"
        if '"success":false' in normalised:
            return "failed"
    return "unknown"


def _ton_status(payload: dict[str, Any], tx_hash: str) -> Outcome:
    """A match in TON's v2 `getTransactions` proves inclusion, not execution success.

    The schema has no failure flag; decoding a transaction's compute/action phase would be
    a separate, reviewed parser. Until one exists a non-match stays `pending` and a
    malformed answer stays `unknown` — never `failed` from absence of evidence.
    """
    if not payload.get("ok") or payload.get("error"):
        return "unknown"
    result = payload.get("result")
    if not isinstance(result, list):
        return "unknown"
    for tx in result:
        if not isinstance(tx, dict):
            continue
        transaction_id = tx.get("transaction_id")
        incoming = tx.get("in_msg")
        hashes = {
            transaction_id.get("hash") if isinstance(transaction_id, dict) else None,
            incoming.get("hash") if isinstance(incoming, dict) else None,
        }
        if tx_hash in hashes or tx_hash.lower() in {
            value.lower() for value in hashes if isinstance(value, str)
        }:
            return "confirmed"
    # No matching transaction in the returned window: not yet processed, not a failure.
    return "pending"


def solana_status_spec() -> StatusSpec:
    return StatusSpec(
        method="getSignatureStatuses",
        params=lambda tx_hash: [[tx_hash], {"searchTransactionHistory": True}],
        classify=_solana_status,
    )


def evm_status_spec() -> StatusSpec:
    return StatusSpec(
        method="eth_getTransactionReceipt", params=lambda tx_hash: [tx_hash], classify=_evm_status
    )


def sui_status_spec() -> StatusSpec:
    return StatusSpec(
        method="sui_getTransactionBlock",
        params=lambda tx_hash: [tx_hash, {"showEffects": True}],
        classify=_sui_status,
    )


def cosmos_status_spec() -> StatusSpec:
    return StatusSpec(
        method="tx",
        params=lambda tx_hash: {"hash": "0x" + tx_hash.lower().removeprefix("0x")},
        classify=_cosmos_status,
    )


def ton_status_spec(address: str) -> StatusSpec:
    """TON v2 ``getTransactions`` needs the account address as public context.

    The hash is passed as the pagination anchor; a matching transaction in the returned
    window proves inclusion. A non-match is `pending`, never `failed`.
    """
    if not address:
        raise ValueError("a TON status read needs the account address")
    return StatusSpec(
        method="getTransactions",
        params=lambda tx_hash: {"address": address, "hash": tx_hash, "limit": 5},
        classify=_ton_status,
        envelope=lambda method, params: params,
        path_suffix="getTransactions",
    )


class ChainStatusReader:
    """Ask one documented read-only status method, with a hard deadline."""

    def __init__(
        self,
        *,
        chain: str,
        spec: StatusSpec,
        rpc_url: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = DEFAULT_STATUS_TIMEOUT_SECONDS,
    ) -> None:
        if not rpc_url or timeout_seconds <= 0:
            raise ValueError("a status reader needs an endpoint and a positive timeout")
        self.chain = chain
        self._spec = spec
        self._rpc_url = rpc_url
        self._client = client
        self._timeout = timeout_seconds

    async def status(self, tx_hash: str) -> Confirmation:
        """Return a typed outcome; an ambiguous read is ``unknown``, never a success."""
        if not tx_hash:
            return Confirmation(self.chain, tx_hash, "unknown", utcnow(), "no transaction hash")
        params = self._spec.params(tx_hash)
        build_body = self._spec.envelope or _rpc
        body = build_body(self._spec.method, params)
        url = (
            self._rpc_url.rstrip("/") + "/" + self._spec.path_suffix
            if self._spec.path_suffix
            else self._rpc_url
        )
        try:
            response = await asyncio.wait_for(
                self._client.post(url, json=body),
                timeout=self._timeout,
            )
            payload = response.json()
        except (TimeoutError, httpx.HTTPError, ValueError):
            return Confirmation(self.chain, tx_hash, "unknown", utcnow(), "status unreadable")
        if not isinstance(payload, dict):
            return Confirmation(self.chain, tx_hash, "unknown", utcnow(), "status malformed")
        if response.status_code >= 400:
            # A definite rejection of the read says nothing about the transaction.
            return Confirmation(self.chain, tx_hash, "unknown", utcnow(), "status rejected")
        outcome = _classification(self.chain, tx_hash, payload, self._spec)
        return Confirmation(self.chain, tx_hash, outcome, utcnow())


__all__ = [
    "CONFIRMATION_ACTIONS",
    "CONFIRMATION_STATES",
    "Confirmation",
    "Outcome",
    "StatusSpec",
    "ChainStatusReader",
    "cosmos_status_spec",
    "evm_status_spec",
    "normalize_confirmation",
    "solana_status_spec",
    "sui_status_spec",
    "ton_status_spec",
]
