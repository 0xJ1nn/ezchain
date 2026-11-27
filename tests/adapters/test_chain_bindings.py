"""Tests for the canonical binary binding payload embedded in chain transactions.

The payload is the part of a chain-native transaction that the trust ``decode_and_bind``
boundary reads. It is deliberately binary and versioned: the neutral JSON envelope stays a
separate test wire form, and no chain decoder depends on it. Every literal here is a
documented public-format test vector with obviously-invalid values; no key or funded
address appears.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ezchain_adapters.chains.chainbind import (
    BINDING_MAGIC,
    ChainBindingDecodeError,
    decode_binding_payload,
    encode_binding_payload,
)
from ezchain_core.reason_codes import REASON_CODES
from ezchain_core.trust import (
    BINDING_FIELDS,
    DecodedFields,
    canonical_decimal,
    decode_and_bind,
    encode_transaction,
)

FIELDS = DecodedFields(
    chain="solana",
    cluster="mainnet-beta",
    mint="So11111111111111111111111111111111111111112",
    side="buy",
    amount=Decimal("250.000"),
    minimum_output=Decimal("0.5"),
    recipient="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    program_id="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    idempotency_key="idem-chainbind-0001",
    fee_payer="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    blockhash="9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9",
    version="legacy",
    size_bytes=800,
    account_count=3,
    instruction_count=1,
    token_program_id="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    token_owner="9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    token_decimals=9,
    compute_units=120000,
    compute_unit_price=500000,
    allowance_unlimited=False,
    allowance_spender="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
)


def approval(fields: DecodedFields = FIELDS) -> object:
    """The trust approval matching ``fields`` (same nine field values)."""
    from ezchain_core.trust import Intent

    return Intent.from_mapping({name: getattr(fields, name) for name in BINDING_FIELDS})


def test_payload_round_trips_every_field() -> None:
    raw = encode_binding_payload(FIELDS)
    assert raw.startswith(BINDING_MAGIC)
    assert decode_binding_payload(raw) == FIELDS


def test_payload_feeds_the_trust_decode_and_bind_boundary() -> None:
    from dataclasses import replace

    bound = decode_binding_payload(encode_binding_payload(FIELDS))
    assert decode_and_bind(approval(), fields=bound) == FIELDS
    # Decimal spellings re-spell exactly; a different spelling binds the same value.
    respelled = replace(FIELDS, amount=Decimal("250.0"), minimum_output=Decimal("0.50"))
    assert decode_binding_payload(encode_binding_payload(respelled)).amount == Decimal("250.0")


@pytest.mark.parametrize("name", list(BINDING_FIELDS))
def test_every_bound_field_survives_the_payload(name: str) -> None:
    from dataclasses import replace

    changed = replace(FIELDS, **{name: "changed"})
    if name in ("amount", "minimum_output"):
        changed = replace(FIELDS, **{name: Decimal("999")})
    elif name == "side":
        changed = replace(FIELDS, side="sell")
    assert getattr(decode_binding_payload(encode_binding_payload(changed)), name) == getattr(
        changed, name
    )


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("chain", "ethereum", "TRANSACTION_CHAIN_MISMATCH"),
        ("cluster", "devnet", "TRANSACTION_CLUSTER_MISMATCH"),
        ("mint", "attacker", "TRANSACTION_MINT_MISMATCH"),
        ("side", "sell", "TRANSACTION_SIDE_MISMATCH"),
        ("amount", Decimal("251"), "TRANSACTION_AMOUNT_MISMATCH"),
        ("minimum_output", Decimal("0.4"), "TRANSACTION_MIN_OUT_MISMATCH"),
        ("recipient", "attacker", "TRANSACTION_RECIPIENT_MISMATCH"),
        ("program_id", "foreign", "TRANSACTION_PROGRAM_MISMATCH"),
        ("idempotency_key", "replaced", "TRANSACTION_IDEMPOTENCY_MISMATCH"),
    ],
)
def test_every_mutable_field_is_bound(field: str, replacement: object, code: str) -> None:
    from dataclasses import replace

    from ezchain_core.trust import BindingRefused

    tampered = decode_binding_payload(
        encode_binding_payload(replace(FIELDS, **{field: replacement}))
    )
    with pytest.raises(BindingRefused) as caught:
        decode_and_bind(approval(), fields=tampered)
    assert caught.value.refusal.code == code
    assert code in REASON_CODES


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not a binding payload",
        BINDING_MAGIC,  # truncated: no field count
        BINDING_MAGIC + b"\x00\x01x",  # truncated field payload
        encode_binding_payload(FIELDS)[:-1],  # truncated native section
        encode_binding_payload(FIELDS) + b"\x01",  # trailing garbage
    ],
)
def test_malformed_payloads_refuse_with_a_typed_reason(raw: bytes) -> None:
    with pytest.raises(ChainBindingDecodeError) as caught:
        decode_binding_payload(raw)
    assert caught.value.code == "TRANSACTION_UNDECODABLE"
    assert "TRANSACTION_UNDECODABLE" in REASON_CODES


def test_canonical_decimal_is_the_one_true_spelling() -> None:
    assert canonical_decimal(Decimal("250.000")) == "250"
    assert canonical_decimal(Decimal("0.50")) == "0.5"
    # The neutral JSON envelope is still available as a separate, tested wire form.
    assert b"ezchain.trust.transaction.v1" in encode_transaction(FIELDS)
