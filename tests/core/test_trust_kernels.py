"""Golden vectors for the pure trust-boundary kernels.

The literals here are the contract: the intent digest is a fixed hex value, each
approved field has its own binding refusal, and each of the 16 ordered preflight checks
has a named refusal with a stable taxonomy code. Nothing in this file signs, opens a
socket, reads a clock or touches a database.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal

import pytest

from ezchain_core.reason_codes import REASON_CODES
from ezchain_core.trust import (
    CHECKS,
    BindingRefused,
    DecodedFields,
    Intent,
    PreflightFacts,
    PreflightInput,
    TransactionDecodeError,
    canonical_decimal,
    decode_and_bind,
    encode_transaction,
    parse_exact_decimal,
    run_preflight,
)

CHAIN = "solana"
CLUSTER = "mainnet-beta"
MINT = "So11111111111111111111111111111111111111112"
RECIPIENT = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
IDEM = "idem-trust-kernels-0001"

# Fixed inputs. Decimal spellings are deliberately non-canonical in places: the kernels
# must re-spell exact values, never round them.
INTENT_DATA = {
    "chain": CHAIN,
    "cluster": CLUSTER,
    "mint": MINT,
    "side": "buy",
    "amount": "250.000",
    "minimum_output": "0.5",
    "recipient": RECIPIENT,
    "program_id": PROGRAM,
    "idempotency_key": IDEM,
}
FIELD_DATA = {
    **INTENT_DATA,
    "fee_payer": RECIPIENT,
    "blockhash": "9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9x9",
    "version": "v0",
    "size_bytes": 800,
    "account_count": 12,
    "instruction_count": 4,
    "token_program_id": TOKEN_PROGRAM,
    "token_owner": RECIPIENT,
    "token_decimals": 9,
    "compute_units": 120000,
    "compute_unit_price": 500000,
    "allowance_unlimited": False,
    "allowance_spender": PROGRAM,
}
FACTS_DATA = {
    "mode": "live",
    "expected_fee_payer": RECIPIENT,
    "blockhash_valid": True,
    "expected_version": "v0",
    "max_size_bytes": 1232,
    "max_accounts": 64,
    "allowed_programs": (PROGRAM,),
    "max_instructions": 8,
    "expected_token_program": TOKEN_PROGRAM,
    "expected_token_decimals": 9,
    "max_compute_units": 200000,
    "max_compute_unit_price": 1000000,
    "expected_spender": PROGRAM,
    "quote_expired": False,
    "simulation_ok": True,
    "wallet_in_flight": False,
    "kill_switch": "off",
    "signed_digest": "a" * 64,
    "receipt_digest": "a" * 64,
}


def intent(**overrides: object) -> Intent:
    return Intent.from_mapping({**INTENT_DATA, **overrides})


def fields(**overrides: object) -> DecodedFields:
    return DecodedFields.from_mapping({**FIELD_DATA, **overrides})


def facts(**overrides: object) -> PreflightFacts:
    return PreflightFacts(**{**FACTS_DATA, **overrides})


def happy_input() -> PreflightInput:
    approved = intent()
    return PreflightInput(
        intent=approved,
        fields=decode_and_bind(approved, raw=encode_transaction(fields())),
        facts=facts(),
    )


# ---------------------------------------------------------------- intent digest


def test_intent_digest_golden_vector() -> None:
    """One fixed approval has one fixed digest, stable across processes."""
    approved = intent()
    assert approved.digest == "e32c6208da8228e3ce79361185f4265f473d5f6f34978a5ff259a98e0ca11c7e"
    canonical = approved.canonical_bytes()
    assert canonical.startswith(
        b"ezchain.trust.intent.v1\x00ezchain.live.approval.v1\x00chain\x00string\x006\x00solana\n"
    )
    assert canonical.endswith(b"idempotency_key\x00string\x0023\x00idem-trust-kernels-0001\n")
    assert len(canonical.decode("ascii")) == 407


@pytest.mark.parametrize("name", list(INTENT_DATA))
def test_every_intent_field_changes_the_digest(name: str) -> None:
    """Domain separation covers every field: a swap in any one changes the identity."""
    replacement: object = (
        "777.777"
        if name in ("amount", "minimum_output")
        else "sell"
        if name == "side"
        else "changed"
    )
    assert intent(**{name: replacement}).digest != intent().digest  # type: ignore[arg-type]


def test_amount_spelling_does_not_change_the_digest() -> None:
    """Exact decimals re-spell; ``250.0`` and ``250.000`` are one approved value."""
    left = intent(amount="250.0", minimum_output="0.50")
    right = intent(amount=Decimal("250.000"), minimum_output=Decimal("0.5"))
    assert canonical_decimal(Decimal("1.500")) == "1.5"
    assert canonical_decimal(Decimal("-0.00")) == "0"
    assert left.canonical_bytes() == right.canonical_bytes()
    assert left.digest == right.digest


@pytest.mark.parametrize("bad", [250.0, "NaN", "Infinity", "-Infinity", True, None])
def test_money_parsing_is_exact_and_finite(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_exact_decimal(bad)  # type: ignore[arg-type]


def test_intent_mapping_is_a_closed_record() -> None:
    with pytest.raises(ValueError, match="unknown intent fields"):
        intent(**{"actor": "someone"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="missing intent fields"):
        Intent.from_mapping({"chain": CHAIN})


# ------------------------------------------------------------- decode and bind


@pytest.mark.parametrize(
    ("field", "replacement", "code"),
    [
        ("chain", "ethereum", "TRANSACTION_CHAIN_MISMATCH"),
        ("cluster", "devnet", "TRANSACTION_CLUSTER_MISMATCH"),
        ("mint", "attacker-mint", "TRANSACTION_MINT_MISMATCH"),
        ("side", "sell", "TRANSACTION_SIDE_MISMATCH"),
        ("amount", "251", "TRANSACTION_AMOUNT_MISMATCH"),
        ("minimum_output", "0.4", "TRANSACTION_MIN_OUT_MISMATCH"),
        ("recipient", "attacker", "TRANSACTION_RECIPIENT_MISMATCH"),
        ("program_id", "foreign-program", "TRANSACTION_PROGRAM_MISMATCH"),
        ("idempotency_key", "replaced-key", "TRANSACTION_IDEMPOTENCY_MISMATCH"),
    ],
)
def test_each_swapped_field_has_its_own_refusal(field: str, replacement: str, code: str) -> None:
    approved = intent()
    with pytest.raises(BindingRefused) as caught:
        decode_and_bind(approved, fields={**FIELD_DATA, field: replacement})
    assert caught.value.refusal.code == code
    assert caught.value.refusal.check == f"binding.{field}"
    assert code in REASON_CODES


def test_happy_path_binds_from_bytes_and_from_a_field_map() -> None:
    approved = intent()
    raw = encode_transaction(fields(amount=Decimal("250.00"), minimum_output=Decimal("0.50")))
    bound = decode_and_bind(approved, raw=raw)
    assert bound.amount == Decimal("250.00")
    assert decode_and_bind(approved, fields=FIELD_DATA) == fields()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json",
        b"[]",
        b'{"schema":"other"}',
        b'{"schema":"ezchain.trust.transaction.v1","side":"buy"}',
        encode_transaction(fields()).replace(b'"buy"', b'"hold"'),
        encode_transaction(fields()).replace(b'"250"', b'"two-fifty"'),
        b'{"schema":"ezchain.trust.transaction.v1","chain":"solana","cluster":"mainnet-beta",'
        b'"mint":"m","side":"buy","amount":250.0,"minimum_output":"0.5","recipient":"r",'
        b'"program_id":"p","idempotency_key":"k"}',
    ],
)
def test_undecodable_bytes_never_reach_a_signer(raw: bytes) -> None:
    with pytest.raises(TransactionDecodeError):
        decode_and_bind(intent(), raw=raw)


def test_decode_and_bind_has_no_signer_parameter() -> None:
    """The gate cannot call a signer: there is no such argument in its signature."""
    assert set(inspect.signature(decode_and_bind).parameters) == {"intent", "raw", "fields"}


# ------------------------------------------------------------------- preflight


@pytest.mark.parametrize(
    ("check_name", "code", "target", "overrides"),
    [
        ("chain_cluster_mode", "TRANSACTION_CHAIN_MISMATCH", "fields", {"chain": "ethereum"}),
        ("chain_cluster_mode", "TRANSACTION_CLUSTER_MISMATCH", "fields", {"cluster": "devnet"}),
        ("chain_cluster_mode", "GATE_NOT_MET", "facts", {"mode": "other"}),
        ("fee_payer_signer_set", "CUSTODY_OWNER_MISMATCH", "fields", {"fee_payer": "attacker"}),
        ("blockhash_freshness", "TRANSACTION_BLOCKHASH_STALE", "facts", {"blockhash_valid": False}),
        ("version_and_size", "TRANSACTION_VERSION_UNSUPPORTED", "fields", {"version": "v1"}),
        ("version_and_size", "TRANSACTION_VERSION_UNSUPPORTED", "fields", {"size_bytes": 2000}),
        (
            "account_list_after_alt",
            "TRANSACTION_ACCOUNT_LIST_INVALID",
            "fields",
            {"account_count": 65},
        ),
        (
            "program_allowlist_instruction_count",
            "TRANSACTION_PROGRAM_MISMATCH",
            "fields",
            {"program_id": "foreign"},
        ),
        (
            "program_allowlist_instruction_count",
            "TRANSACTION_INSTRUCTION_LIMIT",
            "fields",
            {"instruction_count": 9},
        ),
        ("mint_owner_ata", "TRANSACTION_MINT_MISMATCH", "fields", {"mint": "attacker"}),
        ("mint_owner_ata", "TRANSACTION_RECIPIENT_MISMATCH", "fields", {"token_owner": "attacker"}),
        ("mint_owner_ata", "TRANSACTION_MINT_MISMATCH", "fields", {"token_decimals": 6}),
        (
            "recipient_allowlist",
            "TRANSACTION_RECIPIENT_MISMATCH",
            "fields",
            {"recipient": "attacker"},
        ),
        ("amount_bound", "TRANSACTION_AMOUNT_MISMATCH", "fields", {"amount": Decimal("251")}),
        (
            "slippage_min_out_expiry",
            "TRANSACTION_MIN_OUT_MISMATCH",
            "fields",
            {"minimum_output": Decimal("0.4")},
        ),
        ("slippage_min_out_expiry", "QUOTE_STALE", "facts", {"quote_expired": True}),
        (
            "compute_budget_caps",
            "TRANSACTION_COMPUTE_BUDGET_EXCEEDED",
            "fields",
            {"compute_units": 300000},
        ),
        (
            "compute_budget_caps",
            "TRANSACTION_COMPUTE_BUDGET_EXCEEDED",
            "fields",
            {"compute_unit_price": 2000000},
        ),
        (
            "allowance_shape",
            "TRANSACTION_ALLOWANCE_UNBOUNDED",
            "fields",
            {"allowance_unlimited": True},
        ),
        (
            "allowance_shape",
            "TRANSACTION_ALLOWANCE_UNBOUNDED",
            "fields",
            {"allowance_spender": "attacker"},
        ),
        ("simulation_result", "SIMULATION_FAILED", "facts", {"simulation_ok": False}),
        (
            "idempotency_nonce",
            "TRANSACTION_IDEMPOTENCY_MISMATCH",
            "fields",
            {"idempotency_key": "replaced"},
        ),
        ("idempotency_nonce", "TRANSACTION_WALLET_IN_FLIGHT", "facts", {"wallet_in_flight": True}),
        ("kill_switch_arm", "KILL_SWITCH_ACTIVE", "facts", {"kill_switch": "active"}),
        ("kill_switch_arm", "KILL_SWITCH_ARMED", "facts", {"kill_switch": "armed"}),
        ("kill_switch_arm", "KILL_SWITCH_UNAVAILABLE", "facts", {"kill_switch": "unknown"}),
        (
            "receipt_echoes_digest",
            "TRANSACTION_DIGEST_MISMATCH",
            "facts",
            {"receipt_digest": "b" * 64},
        ),
    ],
)
def test_preflight_golden_vectors(
    check_name: str, code: str, target: str, overrides: dict[str, object]
) -> None:
    base = happy_input()
    if target == "fields":
        inp = replace(base, fields=fields(**overrides))
    else:
        inp = replace(base, facts=facts(**overrides))
    refusal = run_preflight(inp)
    assert refusal is not None
    assert refusal.check == check_name
    assert refusal.code == code
    assert code in REASON_CODES


def test_preflight_happy_path_and_manifest() -> None:
    assert run_preflight(happy_input()) is None
    assert [c.order for c in CHECKS] == list(range(1, 17))
    assert len({c.name for c in CHECKS}) == 16
    # An armed switch blocks a new entry but deliberately lets an exit settle.
    sell = replace(happy_input(), intent=intent(side="sell"), facts=facts(kill_switch="armed"))
    assert run_preflight(sell) is None


def test_missing_facts_fail_closed() -> None:
    """No caller-supplied observation may quietly become a pass."""
    base = happy_input()
    bare = replace(base, facts=PreflightFacts(mode="live"))
    refusal = run_preflight(bare)
    assert refusal is not None
    assert (refusal.check, refusal.code) == ("fee_payer_signer_set", "CUSTODY_OWNER_MISMATCH")
