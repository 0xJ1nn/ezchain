"""The domain-separated intent digest — the identity of what was approved.

Design note (the whole trust package shares this contract, see ``ezchain_core.trust``):
what is checked here is the identity of the approval: chain, cluster, mint, side,
amount, output floor, recipient, program id and idempotency key. What is deliberately
*not* checked here is the signature (that is the signer's proof, not the approval's
identity), the venue (a venue is a source of bytes, never a source of truth), and any
money arithmetic (these kernels parse and compare exact decimals; they never compute a
price, a fee or a size). The future signer boundary will hash an :class:`Intent` with
:meth:`Intent.digest`, give that digest and the intent to its adapter, decode the bytes
the adapter returns with :func:`ezchain_core.trust.attestation.decode_and_bind`, run
:func:`ezchain_core.trust.preflight.run_preflight`, and only then call ``sign``. Every
refusal is a typed reason code and no branch of that path touches a key.

The digest is EIP-712-shaped without borrowing its crypto: a public domain string, a
public schema version and salt, then a fixed-order, length-prefixed, type-tagged set of
fields. It is stable across processes and Python versions, and changing any one field
changes it. There are no keys and no secrets in the preimage — this is an identifier,
not a signature and not a MAC.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from ..types import Side
from .types import BINDING_FIELDS, canonical_decimal, parse_exact_decimal

INTENT_DOMAIN: Final = "ezchain.trust.intent.v1"
INTENT_SALT: Final = "ezchain.live.approval.v1"

_DECIMAL_FIELDS: Final = frozenset({"amount", "minimum_output"})


@dataclass(frozen=True)
class Intent:
    """The typed approval the transaction must reproduce exactly.

    This is *not* :class:`ezchain_core.execution.Intent`: that type is a request a
    caller makes and is later sized and priced. This type is the settled result a signer
    boundary is allowed to bind against. It carries no actor, no quote, no strategy and
    no key handle — only the nine fields the venue can change on the wire.

    ``amount`` is what the side spends and ``minimum_output`` the floor it receives: for
    a buy, USD in and token quantity out; for a sell, token quantity in and USD out.
    Both must be exact; no float is accepted anywhere in construction.
    """

    chain: str
    cluster: str
    mint: str
    side: Side
    amount: Decimal
    minimum_output: Decimal
    recipient: str
    program_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("chain", "cluster", "mint", "recipient", "program_id", "idempotency_key"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"an approved intent needs a non-empty {name}")
        if self.side not in ("buy", "sell"):
            raise ValueError("an approved intent needs a side of buy or sell")
        if not isinstance(self.amount, Decimal) or not isinstance(self.minimum_output, Decimal):
            raise ValueError("an approved intent needs Decimal money fields")
        if not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("an approved intent needs a positive exact amount")
        if not self.minimum_output.is_finite() or self.minimum_output < 0:
            raise ValueError("an approved intent needs a non-negative exact output floor")

    def canonical_bytes(self) -> bytes:
        """The exact bytes the digest covers.

        A domain and salt prefix, then one length-prefixed record per field in
        :data:`BINDING_FIELDS` order. The length prefix means a field value cannot bleed
        into the next one, and the type tag stops a decimal from being read as a string.
        """
        parts: list[bytes] = [
            INTENT_DOMAIN.encode("ascii"),
            b"\x00",
            INTENT_SALT.encode("ascii"),
            b"\x00",
        ]
        for name in BINDING_FIELDS:
            value = getattr(self, name)
            tag = "decimal" if name in _DECIMAL_FIELDS else "side" if name == "side" else "string"
            encoded = (
                canonical_decimal(value).encode("ascii")
                if name in _DECIMAL_FIELDS
                else str(value).encode("utf-8")
            )
            parts.append(
                name.encode("ascii")
                + b"\x00"
                + tag.encode("ascii")
                + b"\x00"
                + str(len(encoded)).encode("ascii")
                + b"\x00"
                + encoded
                + b"\n"
            )
        return b"".join(parts)

    @property
    def digest(self) -> str:
        """Stable SHA-256 identifier for the whole approval."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Intent:
        """Build an intent from a decoded field map, parsing decimals exactly.

        Unknown keys are refused: an intent is a closed record, and a field nobody
        declared must not silently enter the identity that gets signed.
        """
        unknown = set(data) - set(BINDING_FIELDS)
        if unknown:
            raise ValueError(f"unknown intent fields: {', '.join(sorted(unknown))}")
        missing = set(BINDING_FIELDS) - set(data)
        if missing:
            raise ValueError(f"missing intent fields: {', '.join(sorted(missing))}")
        return cls(
            chain=data["chain"],
            cluster=data["cluster"],
            mint=data["mint"],
            side=data["side"],
            amount=parse_exact_decimal(data["amount"]),
            minimum_output=parse_exact_decimal(data["minimum_output"]),
            recipient=data["recipient"],
            program_id=data["program_id"],
            idempotency_key=data["idempotency_key"],
        )


__all__ = ["INTENT_DOMAIN", "INTENT_SALT", "Intent"]
