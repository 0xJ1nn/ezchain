"""What is signed is exactly what was approved.

The venue adapter receives an immutable :class:`ApprovedIntent` and returns bytes; those
bytes are decoded and compared with the approval before the executor's key is touched. A
swapped mint, an inflated amount, a changed recipient, a foreign program, another cluster,
the wrong side, a lowered output floor or a replaced idempotency key each refuse with its
own code and no signature. The signed payload's digest then travels through submission and
must come back on the receipt, or confirmation books nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
from dataclasses import FrozenInstanceError, dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ezchain_core import (
    BINDING_REASONS,
    SIGNING_BOUNDARY_REQUIRED,
    TRANSACTION_AMOUNT_MISMATCH,
    TRANSACTION_APPROVAL_MISMATCH,
    TRANSACTION_BUILD_FAILED,
    TRANSACTION_CLUSTER_MISMATCH,
    TRANSACTION_DIGEST_MISMATCH,
    TRANSACTION_IDEMPOTENCY_MISMATCH,
    TRANSACTION_MIN_OUT_MISMATCH,
    TRANSACTION_MINT_MISMATCH,
    TRANSACTION_PROGRAM_MISMATCH,
    TRANSACTION_RECIPIENT_MISMATCH,
    TRANSACTION_SIDE_MISMATCH,
    TRANSACTION_SIGN_FAILED,
    TRANSACTION_UNDECODABLE,
    TRANSACTION_UNSIGNED,
    ApprovedIntent,
    Intent,
    PairInfo,
    PositionState,
    PreparedTransaction,
    RiskLimits,
    SignedTransaction,
    SourceRef,
    TokenSnapshot,
    TransactionPayload,
    decode_transaction,
    encode_transaction,
    utcnow,
    verify_prepared_transaction,
)
from ezchain_executor import (
    ExecutionPipeline,
    FakeVenue,
    LocalTransactionSigner,
    MemoryJournal,
    SigningBoundary,
)
from ezchain_executor.kill_switch import KillSwitch

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"
OTHER_TOKEN = "Mint2222222222222222222222222222222222222222"
WALLET = "Wallet11111111111111111111111111111111111111"
OTHER_WALLET = "Wallet22222222222222222222222222222222222222"
PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
OTHER_PROGRAM = "Foreign1111111111111111111111111111111111111"
CLUSTER = "devnet"

# A deterministic test vector. It is not a wallet, a seed or a deployed key.
TEST_ONLY_KEY_SOLANA = b"ezchain-test-only-signing-key-vector-0001"


@dataclass
class SignatureCounter:
    """Counts signatures so a refusal can prove the key was never touched."""

    key: bytes = TEST_ONLY_KEY_SOLANA
    calls: int = 0

    def sign(self, payload: bytes) -> bytes:
        self.calls += 1
        return hmac.new(self.key, payload, hashlib.sha256).digest()


@dataclass
class RaisingKeyRing:
    def sign(self, payload: bytes) -> bytes:
        raise RuntimeError("vault locked")


@dataclass
class EmptyKeyRing:
    def sign(self, payload: bytes) -> bytes:
        return b""


@dataclass
class TamperingVenue(FakeVenue):
    """Builds the approved payload, then changes one field like a hostile venue would."""

    changes: dict[str, object] = field(default_factory=dict)

    async def prepare(self, approved: ApprovedIntent) -> bytes:
        self.prepare_calls += 1
        self.prepared.append(approved)
        return encode_transaction(replace(self.payload_for(approved), **self.changes))


@dataclass
class GarbageVenue(FakeVenue):
    async def prepare(self, approved: ApprovedIntent) -> bytes:
        self.prepare_calls += 1
        self.prepared.append(approved)
        return b"not a transaction"


@dataclass
class BrokenBuilderVenue(FakeVenue):
    async def prepare(self, approved: ApprovedIntent) -> bytes:
        self.prepare_calls += 1
        raise RuntimeError("builder unavailable")


@dataclass
class UncheckedSigner:
    """A conforming signer that verifies nothing: it signs whatever it is handed.

    The binding must not depend on this class being well behaved. Whatever the signer does,
    the pipeline refused the transaction the venue prepared before this was ever called.
    """

    key: bytes = TEST_ONLY_KEY_SOLANA
    calls: int = 0

    def sign(self, prepared: PreparedTransaction, approved: ApprovedIntent) -> SignedTransaction:
        self.calls += 1
        return SignedTransaction(
            raw=prepared.raw,
            signature=hmac.new(self.key, prepared.raw, hashlib.sha256).digest(),
            approved=approved,
            payload_digest=prepared.payload_digest,
        )


@dataclass
class ForeignApprovalSigner(UncheckedSigner):
    """Signs the verified bytes but attaches an approval nobody approved."""

    def sign(self, prepared: PreparedTransaction, approved: ApprovedIntent) -> SignedTransaction:
        self.calls += 1
        foreign = replace(approved, amount=approved.amount + Decimal("1"))
        return SignedTransaction(
            raw=prepared.raw,
            signature=hmac.new(self.key, prepared.raw, hashlib.sha256).digest(),
            approved=foreign,
            payload_digest=prepared.payload_digest,
        )


@dataclass
class SwappedBytesSigner(UncheckedSigner):
    """Signs bytes the pipeline never decoded, with a digest that covers those bytes."""

    def sign(self, prepared: PreparedTransaction, approved: ApprovedIntent) -> SignedTransaction:
        self.calls += 1
        foreign = encode_transaction(
            replace(
                TransactionPayload(
                    cluster=approved.cluster,
                    program_id=approved.program_id,
                    mint=approved.mint,
                    side=approved.side,
                    amount=approved.amount,
                    min_out=approved.min_out,
                    recipient=approved.recipient,
                    idempotency_key=approved.idempotency_key,
                ),
                recipient=OTHER_WALLET,
            )
        )
        return SignedTransaction(
            raw=foreign,
            signature=hmac.new(self.key, foreign, hashlib.sha256).digest(),
            approved=approved,
            payload_digest=hashlib.sha256(foreign).hexdigest(),
        )


@dataclass
class SwappedReceiptVenue(FakeVenue):
    """Echoes a different payload digest than the one that was signed."""

    digest: str = "0" * 64

    async def submit(self, order, signed=None):  # type: ignore[no-untyped-def]
        fill = await super().submit(order, signed)
        return replace(fill, transaction_digest=self.digest)


@dataclass
class DigestlessReceiptVenue(FakeVenue):
    async def submit(self, order, signed=None):  # type: ignore[no-untyped-def]
        fill = await super().submit(order, signed)
        return replace(fill, transaction_digest=None)


def buy(**overrides: object) -> Intent:
    values: dict[str, object] = {
        "side": "buy",
        "amount_usd": Decimal("100"),
        "chain": CHAIN,
        "token_address": TOKEN,
        "idempotency_key": "intent-1",
    }
    values.update(overrides)
    return Intent(**values)  # type: ignore[arg-type]


def sell(**overrides: object) -> Intent:
    values: dict[str, object] = {
        "side": "sell",
        "amount_usd": Decimal("0"),
        "chain": CHAIN,
        "token_address": TOKEN,
        "idempotency_key": "exit-1",
        "quantity": Decimal("10"),
        "reduce_only": True,
    }
    values.update(overrides)
    return Intent(**values)  # type: ignore[arg-type]


def make_snapshot() -> TokenSnapshot:
    observed = utcnow()
    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=Decimal("2"),
        liquidity_usd=Decimal("500000"),
        volume_24h_usd=Decimal("1000000"),
        price_change_m5=Decimal("0"),
        price_change_h1=Decimal("1"),
        price_change_h6=Decimal("0"),
        price_change_h24=Decimal("0"),
        pairs=(
            PairInfo(
                pair_address="pair-1",
                dex="raydium",
                quote_token="USDC",
                price_usd=Decimal("2"),
                liquidity_usd=Decimal("500000"),
                volume_24h_usd=Decimal("1000000"),
                url=None,
            ),
        ),
        source=SourceRef("dexscreener", None, observed - timedelta(seconds=1)),
        stale=False,
        pair_created_at=observed - timedelta(days=1),
    )


def limits() -> RiskLimits:
    return RiskLimits(
        max_order_usd=Decimal("1000"),
        max_position_usd=Decimal("2500"),
        max_open_positions=10,
        max_daily_loss_usd=Decimal("500"),
        max_quote_age_seconds=60,
        min_liquidity_usd=Decimal("10000"),
        paper_mark_max_order_usd=Decimal("250"),
        min_pair_age_minutes=10,
        max_price_impact_pct=Decimal("0.5"),
    )


def position(quantity: str = "0", cost: str = "0") -> PositionState:
    return PositionState(
        chain=CHAIN,
        token_address=TOKEN,
        symbol="TEST",
        quantity=Decimal(quantity),
        cost_usd=Decimal(cost),
    )


def boundary(
    venue: FakeVenue,
    keyring: object = None,
    *,
    cluster: str = CLUSTER,
    recipient: str = WALLET,
    program_id: str = PROGRAM,
    signer: object = None,
) -> SigningBoundary:
    return SigningBoundary(
        cluster=cluster,
        recipient=recipient,
        program_id=program_id,
        adapter=venue,
        signer=signer or LocalTransactionSigner(keyring or SignatureCounter()),  # type: ignore[arg-type]
    )


def pipeline(
    venue: FakeVenue,
    signing: SigningBoundary | None = None,
    journal: MemoryJournal | None = None,
    *,
    mode: str = "paper",
) -> tuple[ExecutionPipeline, MemoryJournal]:
    journal = journal or MemoryJournal()
    return (
        ExecutionPipeline(
            venue=venue,
            sink=journal,
            limits=limits(),
            kill_switch=KillSwitch(),
            timeout_seconds=0.2,
            signing=signing,
            mode=mode,  # type: ignore[arg-type]
        ),
        journal,
    )


# --- The approval is the only source of truth --------------------------------


@pytest.mark.asyncio
async def test_the_adapter_receives_an_immutable_approved_intent() -> None:
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert venue.prepare_calls == 1
    approved = venue.prepared[0]
    assert isinstance(approved, ApprovedIntent)
    # Nothing to reach a second source of truth through: no order, no quote, no intent.
    assert not hasattr(approved, "order")
    assert not hasattr(approved, "quote")
    with pytest.raises(FrozenInstanceError):
        approved.amount = Decimal("1")  # type: ignore[misc]
    assert len(journal.groups) == 1


def test_the_adapter_can_only_be_handed_the_approval() -> None:
    parameters = list(inspect.signature(FakeVenue.prepare).parameters)
    assert parameters == ["self", "approved"]


@pytest.mark.asyncio
async def test_the_signed_payload_digest_is_carried_to_confirmation() -> None:
    keyring = SignatureCounter()
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue, keyring))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert result.order is not None and result.fill is not None
    signed = venue.signed[0]
    # The approval digest covers the approved fields; the payload digest covers the bytes.
    assert signed.approval_digest == venue.prepared[0].digest
    assert signed.payload_digest == hashlib.sha256(signed.raw).hexdigest()
    assert result.fill.transaction_digest == signed.payload_digest
    assert result.fill.receipt["transaction_digest"] == signed.payload_digest
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_a_sell_binds_the_token_amount_and_the_usd_floor() -> None:
    venue = FakeVenue()
    pipe, _ = pipeline(venue, boundary(venue))
    result = await pipe.run(sell(), snapshot=make_snapshot(), position=position("10", "20"))

    assert result.status == "filled"
    approved = venue.prepared[0]
    assert approved.side == "sell"
    assert approved.amount == Decimal("10")
    assert approved.min_out == Decimal("20")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"mint": OTHER_TOKEN}, TRANSACTION_MINT_MISMATCH),
        ({"amount": Decimal("101")}, TRANSACTION_AMOUNT_MISMATCH),
        ({"recipient": OTHER_WALLET}, TRANSACTION_RECIPIENT_MISMATCH),
        ({"program_id": OTHER_PROGRAM}, TRANSACTION_PROGRAM_MISMATCH),
        ({"cluster": "mainnet-beta"}, TRANSACTION_CLUSTER_MISMATCH),
        ({"side": "sell"}, TRANSACTION_SIDE_MISMATCH),
        ({"min_out": Decimal("1")}, TRANSACTION_MIN_OUT_MISMATCH),
        ({"idempotency_key": "another-key"}, TRANSACTION_IDEMPOTENCY_MISMATCH),
    ],
)
@pytest.mark.asyncio
async def test_a_changed_field_refuses_before_signing(change: dict[str, object], code: str) -> None:
    keyring = SignatureCounter()
    venue = TamperingVenue(changes=change)
    pipe, journal = pipeline(venue, boundary(venue, keyring))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == code
    assert keyring.calls == 0, "a mismatch must not reach the key ring"
    assert venue.submit_calls == 0
    assert result.fill is None
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_undecodable_transaction_produces_no_signature() -> None:
    keyring = SignatureCounter()
    venue = GarbageVenue()
    pipe, journal = pipeline(venue, boundary(venue, keyring))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_UNDECODABLE
    assert keyring.calls == 0
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_failed_build_refuses_before_signing() -> None:
    keyring = SignatureCounter()
    venue = BrokenBuilderVenue()
    pipe, journal = pipeline(venue, boundary(venue, keyring))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_BUILD_FAILED
    assert keyring.calls == 0
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_locked_vault_refuses_without_journal() -> None:
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue, RaisingKeyRing()))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_SIGN_FAILED
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_an_empty_signature_refuses_without_journal() -> None:
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue, EmptyKeyRing()))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_UNSIGNED
    assert venue.submit_calls == 0
    assert journal.groups == ()


# --- Confirmation ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_receipt_with_a_different_digest_is_not_journaled() -> None:
    venue = SwappedReceiptVenue()
    pipe, journal = pipeline(venue, boundary(venue))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_DIGEST_MISMATCH
    assert result.fill is not None, "the receipt is kept for the audit trail"
    assert result.journal is None
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_receipt_without_a_digest_is_not_journaled() -> None:
    venue = DigestlessReceiptVenue()
    pipe, journal = pipeline(venue, boundary(venue))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_DIGEST_MISMATCH
    assert journal.groups == ()


@pytest.mark.parametrize(
    "change",
    [
        {"mint": OTHER_TOKEN},
        {"amount": Decimal("101")},
        {"recipient": OTHER_WALLET},
        {"program_id": OTHER_PROGRAM},
        {"cluster": "mainnet-beta"},
        {"side": "sell"},
        {"min_out": Decimal("1")},
        {"idempotency_key": "another-key"},
    ],
)
@pytest.mark.asyncio
async def test_a_signer_that_skips_verification_cannot_disarm_the_binding(
    change: dict[str, object],
) -> None:
    """The pipeline verifies the decode itself, so a permissive signer changes nothing."""
    signer = UncheckedSigner()
    venue = TamperingVenue(changes=change)
    pipe, journal = pipeline(venue, boundary(venue, signer=signer))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason in BINDING_REASONS
    assert signer.calls == 0, "the pipeline must refuse before handing the bytes to any signer"
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_signer_that_attaches_a_different_approval_refuses() -> None:
    signer = ForeignApprovalSigner()
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue, signer=signer))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_APPROVAL_MISMATCH
    assert signer.calls == 1
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_signer_that_swaps_the_verified_bytes_refuses() -> None:
    signer = SwappedBytesSigner()
    venue = FakeVenue()
    pipe, journal = pipeline(venue, boundary(venue, signer=signer))
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == TRANSACTION_DIGEST_MISMATCH
    assert signer.calls == 1
    assert venue.submit_calls == 0
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_live_pipeline_without_a_signing_boundary_refuses_before_broadcast() -> None:
    venue = FakeVenue()
    pipe, journal = pipeline(venue, mode="live")
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == SIGNING_BOUNDARY_REQUIRED
    assert result.fill is None
    assert venue.submit_calls == 0, "an unsigned live submit must never reach the venue"
    assert journal.groups == ()


@pytest.mark.asyncio
async def test_a_paper_pipeline_without_signing_still_submits_unsigned() -> None:
    venue = FakeVenue()
    pipe, journal = pipeline(venue)
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert result.fill is not None
    assert result.fill.transaction_digest is None
    assert venue.signed == []
    assert len(journal.groups) == 1


# --- Core binding ------------------------------------------------------------


def test_the_approval_digest_changes_with_every_bound_field() -> None:
    approved = ApprovedIntent(
        chain=CHAIN,
        cluster=CLUSTER,
        mint=TOKEN,
        side="buy",
        amount=Decimal("100"),
        min_out=Decimal("50"),
        recipient=WALLET,
        program_id=PROGRAM,
        idempotency_key="intent-1",
    )
    changes = [
        {"chain": "other-chain"},
        {"cluster": "mainnet-beta"},
        {"mint": OTHER_TOKEN},
        {"side": "sell"},
        {"amount": Decimal("101")},
        {"min_out": Decimal("49")},
        {"recipient": OTHER_WALLET},
        {"program_id": OTHER_PROGRAM},
        {"idempotency_key": "another-key"},
    ]
    digests = {approved.digest}
    for change in changes:
        digests.add(replace(approved, **change).digest)
    assert len(digests) == len(changes) + 1


def test_the_same_amount_spelled_differently_is_the_same_approval() -> None:
    approved = ApprovedIntent(
        chain=CHAIN,
        cluster=CLUSTER,
        mint=TOKEN,
        side="buy",
        amount=Decimal("100"),
        min_out=Decimal("50"),
        recipient=WALLET,
        program_id=PROGRAM,
        idempotency_key="intent-1",
    )
    prepared = decode_transaction(
        encode_transaction(
            TransactionPayload(
                cluster=CLUSTER,
                program_id=PROGRAM,
                mint=TOKEN,
                side="buy",
                amount=Decimal("100.00"),
                min_out=Decimal("50.0"),
                recipient=WALLET,
                idempotency_key="intent-1",
            )
        )
    )
    assert verify_prepared_transaction(prepared, approved) is None
