"""The ordered pre-broadcast checklist — 16 pure checks from the trust design.

Source of the list: the 2026-11-21 trust-boundary research, §4.3 "the correct
pre-broadcast check set". Each check is named, ordered, and a pure function from data to
a taxonomy code or ``None``. ``run_preflight`` walks them in order and returns the first
:class:`~ezchain_core.trust.types.Refusal`; there is no I/O, no clock, no key and no
venue access anywhere in this module. Facts that a real runner would have to read
(blockhash validity, ALT resolution, simulation, kill switch, in-flight state) arrive as
plain values on :class:`PreflightFacts`. Missing facts fail closed instead of becoming a
pass: this is the CONTRACT's "unknown is not zero" rule applied to a trust boundary.

What is deliberately NOT here: signature verification (the signer's proof, verified at
the attestation stage), venue identity (a venue supplies bytes, not authority), and any
money arithmetic (these checks compare exact decimals; they never compute a price, fee,
allowance amount or size). Numeric policy limits are caller-supplied facts, not defaults
baked into a kernel: the design names the checks but not the operator's numbers.

The future signer boundary will call this after
:func:`~ezchain_core.trust.attestation.decode_and_bind` and before ``sign``::

    decoded = decode_and_bind(intent, raw=unsigned_bytes)
    refusal = run_preflight(PreflightInput(intent=intent, fields=decoded, facts=facts))
    if refusal is None:
        signed = signer.sign(decoded, intent)   # the only call that may touch a key

Gaps, stated rather than invented: the design does not fix the numeric limits for checks
4, 5, 6 and 11, the slot window for 3, or the expiry window for 10 — those are supplied
as facts. Check 2's "signatures valid" half is delegated to attestation/signature
verification; this pure kernel checks the fee-payer identity half only. Checks 3, 5, 13,
14, 15 and 16 consume results an injected reader must have gathered; the kernel never
calls that reader itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from ..reason_codes import (
    CUSTODY_OWNER_MISMATCH,
    GATE_NOT_MET,
    KILL_SWITCH_ACTIVE,
    KILL_SWITCH_ARMED,
    KILL_SWITCH_UNAVAILABLE,
    QUOTE_STALE,
    SIMULATION_FAILED,
    TRANSACTION_ACCOUNT_LIST_INVALID,
    TRANSACTION_ALLOWANCE_UNBOUNDED,
    TRANSACTION_AMOUNT_MISMATCH,
    TRANSACTION_BLOCKHASH_STALE,
    TRANSACTION_CHAIN_MISMATCH,
    TRANSACTION_CLUSTER_MISMATCH,
    TRANSACTION_COMPUTE_BUDGET_EXCEEDED,
    TRANSACTION_DIGEST_MISMATCH,
    TRANSACTION_IDEMPOTENCY_MISMATCH,
    TRANSACTION_INSTRUCTION_LIMIT,
    TRANSACTION_MIN_OUT_MISMATCH,
    TRANSACTION_MINT_MISMATCH,
    TRANSACTION_PROGRAM_MISMATCH,
    TRANSACTION_RECIPIENT_MISMATCH,
    TRANSACTION_VERSION_UNSUPPORTED,
    TRANSACTION_WALLET_IN_FLIGHT,
)
from .attestation import DecodedFields
from .intent import Intent
from .types import Refusal, canonical_decimal


@dataclass(frozen=True)
class PreflightFacts:
    """Values the caller has already read, bundled as data for the pure checks.

    ``mode`` is required and must be ``paper`` or ``live``; it is derived server-side,
    never taken from a request. Every other fact defaults to "unknown", and the checks
    that need it fail closed until the caller supplies it.
    """

    mode: str
    expected_fee_payer: str | None = None
    blockhash_valid: bool | None = None
    expected_version: str | None = None
    max_size_bytes: int | None = None
    max_accounts: int | None = None
    allowed_programs: tuple[str, ...] = ()
    max_instructions: int | None = None
    expected_token_program: str | None = None
    expected_token_decimals: int | None = None
    max_compute_units: int | None = None
    max_compute_unit_price: int | None = None
    expected_spender: str | None = None
    quote_expired: bool | None = None
    simulation_ok: bool | None = None
    wallet_in_flight: bool | None = None
    kill_switch: str = "unknown"  # off | armed | active | unknown
    signed_digest: str | None = None
    receipt_digest: str | None = None


@dataclass(frozen=True)
class PreflightInput:
    """The approved intent, the decoded transaction and the observed facts."""

    intent: Intent
    fields: DecodedFields
    facts: PreflightFacts


# --------------------------------------------------------------------------- checks


def _c01_chain_cluster_mode(inp: PreflightInput) -> str | None:
    if inp.fields.chain != inp.intent.chain:
        return TRANSACTION_CHAIN_MISMATCH
    if inp.fields.cluster != inp.intent.cluster:
        return TRANSACTION_CLUSTER_MISMATCH
    if inp.facts.mode not in ("paper", "live"):
        return GATE_NOT_MET
    return None


def _c02_fee_payer_signer_set(inp: PreflightInput) -> str | None:
    expected = inp.facts.expected_fee_payer
    if expected is None or inp.fields.fee_payer != expected:
        return CUSTODY_OWNER_MISMATCH
    return None


def _c03_blockhash_freshness(inp: PreflightInput) -> str | None:
    if not inp.fields.blockhash or inp.facts.blockhash_valid is not True:
        return TRANSACTION_BLOCKHASH_STALE
    return None


def _c04_version_and_size(inp: PreflightInput) -> str | None:
    facts = inp.facts
    if (
        facts.expected_version is None
        or facts.max_size_bytes is None
        or inp.fields.version is None
        or inp.fields.size_bytes is None
    ):
        return TRANSACTION_VERSION_UNSUPPORTED
    if inp.fields.version != facts.expected_version or inp.fields.size_bytes > facts.max_size_bytes:
        return TRANSACTION_VERSION_UNSUPPORTED
    return None


def _c05_account_list_after_alt(inp: PreflightInput) -> str | None:
    facts = inp.facts
    if inp.fields.account_count is None or facts.max_accounts is None:
        return TRANSACTION_ACCOUNT_LIST_INVALID
    if inp.fields.account_count > facts.max_accounts:
        return TRANSACTION_ACCOUNT_LIST_INVALID
    return None


def _c06_program_allowlist_and_instruction_count(inp: PreflightInput) -> str | None:
    facts = inp.facts
    if inp.fields.program_id not in facts.allowed_programs:
        return TRANSACTION_PROGRAM_MISMATCH
    if (
        facts.expected_token_program is not None
        and inp.fields.token_program_id != facts.expected_token_program
    ):
        return TRANSACTION_PROGRAM_MISMATCH
    if (
        inp.fields.instruction_count is None
        or facts.max_instructions is None
        or inp.fields.instruction_count > facts.max_instructions
    ):
        return TRANSACTION_INSTRUCTION_LIMIT
    return None


def _c07_mint_owner_ata(inp: PreflightInput) -> str | None:
    facts = inp.facts
    if inp.fields.mint != inp.intent.mint:
        return TRANSACTION_MINT_MISMATCH
    if inp.fields.token_owner is not None and inp.fields.token_owner != inp.intent.recipient:
        return TRANSACTION_RECIPIENT_MISMATCH
    if (
        facts.expected_token_decimals is not None
        and inp.fields.token_decimals != facts.expected_token_decimals
    ):
        return TRANSACTION_MINT_MISMATCH
    return None


def _c08_recipient_allowlist(inp: PreflightInput) -> str | None:
    if inp.fields.recipient != inp.intent.recipient:
        return TRANSACTION_RECIPIENT_MISMATCH
    return None


def _c09_amount_bound(inp: PreflightInput) -> str | None:
    if canonical_decimal(inp.fields.amount) != canonical_decimal(inp.intent.amount):
        return TRANSACTION_AMOUNT_MISMATCH
    if inp.fields.amount > inp.intent.amount:
        return TRANSACTION_AMOUNT_MISMATCH
    return None


def _c10_slippage_and_expiry(inp: PreflightInput) -> str | None:
    if canonical_decimal(inp.fields.minimum_output) != canonical_decimal(inp.intent.minimum_output):
        return TRANSACTION_MIN_OUT_MISMATCH
    if inp.facts.quote_expired is True:
        return QUOTE_STALE
    return None


def _c11_compute_budget_caps(inp: PreflightInput) -> str | None:
    facts = inp.facts
    fields = inp.fields
    if (
        facts.max_compute_units is None
        or facts.max_compute_unit_price is None
        or fields.compute_units is None
        or fields.compute_unit_price is None
    ):
        return TRANSACTION_COMPUTE_BUDGET_EXCEEDED
    if (
        fields.compute_units > facts.max_compute_units
        or fields.compute_unit_price > facts.max_compute_unit_price
    ):
        return TRANSACTION_COMPUTE_BUDGET_EXCEEDED
    return None


def _c12_allowance_shape(inp: PreflightInput) -> str | None:
    expected = inp.facts.expected_spender
    if expected is None:
        return None  # no EVM approval in this transaction
    if inp.fields.allowance_unlimited is True:
        return TRANSACTION_ALLOWANCE_UNBOUNDED
    if inp.fields.allowance_spender != expected:
        return TRANSACTION_ALLOWANCE_UNBOUNDED
    return None


def _c13_simulation_result(inp: PreflightInput) -> str | None:
    if inp.facts.simulation_ok is not True:
        return SIMULATION_FAILED
    return None


def _c14_idempotency_nonce(inp: PreflightInput) -> str | None:
    if inp.fields.idempotency_key != inp.intent.idempotency_key:
        return TRANSACTION_IDEMPOTENCY_MISMATCH
    if inp.facts.wallet_in_flight is True:
        return TRANSACTION_WALLET_IN_FLIGHT
    return None


def _c15_kill_switch_arm(inp: PreflightInput) -> str | None:
    state = inp.facts.kill_switch
    if state == "unknown" or state not in ("off", "armed", "active"):
        return KILL_SWITCH_UNAVAILABLE
    if state == "active":
        return KILL_SWITCH_ACTIVE
    if state == "armed" and inp.intent.side == "buy":
        return KILL_SWITCH_ARMED
    return None


def _c16_receipt_echoes_digest(inp: PreflightInput) -> str | None:
    signed = inp.facts.signed_digest
    receipt = inp.facts.receipt_digest
    if not signed or not receipt or signed != receipt:
        return TRANSACTION_DIGEST_MISMATCH
    return None


# -------------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class PreflightCheck:
    """One ordered check: its design title, its stable name and its pure evaluator."""

    order: int
    name: str
    title: str
    evaluate: Callable[[PreflightInput], str | None]


CHECKS: Final[tuple[PreflightCheck, ...]] = (
    PreflightCheck(
        1, "chain_cluster_mode", "Chain/cluster + mode equals approved", _c01_chain_cluster_mode
    ),
    PreflightCheck(
        2,
        "fee_payer_signer_set",
        "Fee payer / signer set equals expected wallet",
        _c02_fee_payer_signer_set,
    ),
    PreflightCheck(
        3, "blockhash_freshness", "Blockhash freshness / durable nonce", _c03_blockhash_freshness
    ),
    PreflightCheck(
        4, "version_and_size", "Transaction version & size within policy", _c04_version_and_size
    ),
    PreflightCheck(
        5,
        "account_list_after_alt",
        "Account list after ALT resolution",
        _c05_account_list_after_alt,
    ),
    PreflightCheck(
        6,
        "program_allowlist_instruction_count",
        "Program/router allowlist and instruction count",
        _c06_program_allowlist_and_instruction_count,
    ),
    PreflightCheck(7, "mint_owner_ata", "Mint/owner/ATA check", _c07_mint_owner_ata),
    PreflightCheck(8, "recipient_allowlist", "Recipient allowlist", _c08_recipient_allowlist),
    PreflightCheck(9, "amount_bound", "Amount bound", _c09_amount_bound),
    PreflightCheck(
        10,
        "slippage_min_out_expiry",
        "Slippage bound / min-out and quote expiry",
        _c10_slippage_and_expiry,
    ),
    PreflightCheck(11, "compute_budget_caps", "Compute budget caps", _c11_compute_budget_caps),
    PreflightCheck(12, "allowance_shape", "Allowance/approval shape (EVM)", _c12_allowance_shape),
    PreflightCheck(13, "simulation_result", "Simulation result", _c13_simulation_result),
    PreflightCheck(14, "idempotency_nonce", "Idempotency/nonce", _c14_idempotency_nonce),
    PreflightCheck(
        15, "kill_switch_arm", "Kill switch / arm state re-read before sign", _c15_kill_switch_arm
    ),
    PreflightCheck(
        16, "receipt_echoes_digest", "Receipt echoes signed digest", _c16_receipt_echoes_digest
    ),
)


def run_preflight(inp: PreflightInput) -> Refusal | None:
    """Return the first refusal in the ordered list, or ``None`` when all pass."""
    for check in CHECKS:
        code = check.evaluate(inp)
        if code is not None:
            return Refusal(code=code, check=check.name, detail=f"check {check.order}")
    return None


__all__ = [
    "CHECKS",
    "PreflightCheck",
    "PreflightFacts",
    "PreflightInput",
    "run_preflight",
]
