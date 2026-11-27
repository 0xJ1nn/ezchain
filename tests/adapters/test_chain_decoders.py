"""Reviewed binding tests for every chain-native unsigned-transaction decoder.

For each of the five families the same three properties are pinned: valid bytes bind to the
approval through ``decode_and_bind``; changing any one of the nine mutable bound fields
refuses with that field's own typed code; and malformed, truncated or unsupported bytes
refuse with ``TRANSACTION_UNDECODABLE``. Byte fixtures are constructed from each chain's
published wire format with obviously-invalid constants (see the module docstrings of the
chain modules); no key, funded address or live transaction is involved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from ezchain_adapters.chains.chainbind import ChainBindingDecodeError
from ezchain_adapters.chains.cosmos import (
    build_cosmos_transaction,
    decode_cosmos_transaction,
)
from ezchain_adapters.chains.evm import build_evm_transaction, decode_evm_transaction
from ezchain_adapters.chains.solana import (
    b58encode,
    build_solana_transaction,
    decode_solana_transaction,
)
from ezchain_adapters.chains.sui import build_sui_transaction, decode_sui_transaction
from ezchain_adapters.chains.ton import build_ton_transaction, decode_ton_transaction
from ezchain_core.reason_codes import REASON_CODES
from ezchain_core.trust import (
    BINDING_FIELDS,
    BindingRefused,
    DecodedFields,
    Intent,
    decode_and_bind,
)

Build = Callable[[DecodedFields], bytes]
Decode = Callable[[bytes], DecodedFields]

FAMILIES: tuple[tuple[str, Build, Decode, str], ...] = (
    ("solana", build_solana_transaction, decode_solana_transaction, "legacy"),
    ("evm", build_evm_transaction, decode_evm_transaction, "eip155-legacy"),
    ("ton", build_ton_transaction, decode_ton_transaction, "boc-v1"),
    ("sui", build_sui_transaction, decode_sui_transaction, "bcs-v1"),
    ("cosmos", build_cosmos_transaction, decode_cosmos_transaction, "proto-v1"),
)


def fields_for(version: str) -> DecodedFields:
    return DecodedFields(
        chain="chain-test",
        cluster="testnet",
        mint="mint-test",
        side="buy",
        amount=Decimal("250.000"),
        minimum_output=Decimal("0.5"),
        recipient="recipient-test",
        program_id="program-test",
        idempotency_key="idem-chain-decoders-0001",
        fee_payer="fee-payer-test",
        blockhash=b58encode(bytes((0x44,)) * 32),
        version=version,
        size_bytes=900,
        account_count=3,
        instruction_count=1,
        allowance_unlimited=False,
    )


def approval_for(fields: DecodedFields) -> Intent:
    return Intent.from_mapping({name: getattr(fields, name) for name in BINDING_FIELDS})


def bound(fields: DecodedFields) -> dict[str, object]:
    return {name: getattr(fields, name) for name in BINDING_FIELDS}


@pytest.mark.parametrize(
    ("family", "build", "decode", "version"), FAMILIES, ids=[f[0] for f in FAMILIES]
)
def test_valid_bytes_bind(family: str, build: Build, decode: Decode, version: str) -> None:
    approved = fields_for(version)
    decoded = decode(build(approved))
    assert bound(decode_and_bind(approval_for(approved), fields=decoded)) == bound(approved)
    assert decoded.version == version
    assert decoded.size_bytes is not None and decoded.size_bytes > 0


@pytest.mark.parametrize(
    ("family", "build", "decode", "version"), FAMILIES, ids=[f[0] for f in FAMILIES]
)
@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("chain", "other-chain", "TRANSACTION_CHAIN_MISMATCH"),
        ("cluster", "mainnet-beta", "TRANSACTION_CLUSTER_MISMATCH"),
        ("mint", "attacker-mint", "TRANSACTION_MINT_MISMATCH"),
        ("side", "sell", "TRANSACTION_SIDE_MISMATCH"),
        ("amount", Decimal("251"), "TRANSACTION_AMOUNT_MISMATCH"),
        ("minimum_output", Decimal("0.4"), "TRANSACTION_MIN_OUT_MISMATCH"),
        ("recipient", "attacker", "TRANSACTION_RECIPIENT_MISMATCH"),
        ("program_id", "foreign", "TRANSACTION_PROGRAM_MISMATCH"),
        ("idempotency_key", "replaced", "TRANSACTION_IDEMPOTENCY_MISMATCH"),
    ],
)
def test_every_mutable_bound_field_is_rejected(
    family: str,
    build: Build,
    decode: Decode,
    version: str,
    field: str,
    replacement: object,
    code: str,
) -> None:
    approved = fields_for(version)
    decoded = decode(build(replace(approved, **{field: replacement})))
    with pytest.raises(BindingRefused) as caught:
        decode_and_bind(approval_for(approved), fields=decoded)
    assert caught.value.refusal.code == code
    assert caught.value.refusal.check == f"binding.{field}"
    assert code in REASON_CODES


@pytest.mark.parametrize(
    ("family", "build", "decode", "version"), FAMILIES, ids=[f[0] for f in FAMILIES]
)
@pytest.mark.parametrize(
    "corruption",
    [
        lambda raw: b"",
        lambda raw: b"not a transaction",
        lambda raw: raw[:-1],  # truncated
        lambda raw: raw + b"\x00",  # trailing byte
    ],
)
def test_malformed_and_truncated_bytes_refuse(
    family: str,
    build: Build,
    decode: Decode,
    version: str,
    corruption: Callable[[bytes], bytes],
) -> None:
    with pytest.raises(ChainBindingDecodeError) as caught:
        decode(corruption(build(fields_for(version))))
    assert caught.value.code == "TRANSACTION_UNDECODABLE"


@pytest.mark.parametrize(
    ("family", "build", "decode", "version"), FAMILIES, ids=[f[0] for f in FAMILIES]
)
def test_unsupported_transaction_layouts_refuse(
    family: str, build: Build, decode: Decode, version: str
) -> None:
    with pytest.raises(ChainBindingDecodeError):
        decode(b"\x00\x01\x02\x03\x04")


def test_decoders_are_chain_family_specific_not_one_json_envelope() -> None:
    """The neutral JSON envelope is no longer the only decoder for these bytes."""
    fields = fields_for("legacy")
    raw = build_solana_transaction(fields)
    assert not raw.startswith(b"{")
    assert bound(decode_solana_transaction(raw)) == bound(fields)
