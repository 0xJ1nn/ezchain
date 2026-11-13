"""Custody is the user's: one handle in, a signature or a typed refusal out.

The tests drive a test-only signer transport — no hardware, no real key, no vendor SDK.
Every refusal path is exercised: an unreachable signer, a person declining on the device,
an unknown handle, another user's handle, a locked signer, a watch-only handle, a device
that answers for a different key, and a live pipeline whose signer carries no handle. Each
produces no signature and one stable code. A real signature is produced only for the
handle that was asked for, and nothing a vault returns can be a key.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from ezchain_core import (
    AUTOMATION_FULL,
    AUTOMATION_MANUAL,
    CUSTODY_HARDWARE,
    CUSTODY_LOCAL_VAULT,
    CUSTODY_TIERS,
    CUSTODY_WATCH,
    DISCLOSURE_AUTOMATION_OPT_IN,
    DISCLOSURE_KEY_ADD,
    SIGNABLE_CUSTODY,
    SIGNING_BOUNDARY_REQUIRED,
    VAULT_APPROVAL_DENIED,
    VAULT_BOUNDARY_REQUIRED,
    VAULT_LOCKED,
    VAULT_REASONS,
    VAULT_SIGN_FAILED,
    VAULT_UNKNOWN_HANDLE,
    VAULT_UNREACHABLE,
    VAULT_WATCH_ONLY,
    ApprovedIntent,
    AutomationSetting,
    Intent,
    KeyHandle,
    PairInfo,
    PreparedTransaction,
    RiskDisclosure,
    RiskLimits,
    SourceRef,
    TokenSnapshot,
    TransactionPayload,
    VaultBackedSigner,
    VaultRefused,
    VaultSignature,
    encode_transaction,
    is_fingerprint,
    utcnow,
)
from ezchain_executor import (
    ExecutionPipeline,
    FakeVenue,
    HardwareSignerVault,
    LocalTransactionSigner,
    MemoryJournal,
    SignerApprovalDenied,
    SignerHandleUnknown,
    SignerLocked,
    SignerSignature,
    SignerUnreachable,
    SigningBoundary,
    VaultTransactionSigner,
)
from ezchain_executor.kill_switch import KillSwitch

CHAIN = "solana"
TOKEN = "Mint1111111111111111111111111111111111111111"
WALLET = "Wallet11111111111111111111111111111111111111"
PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
CLUSTER = "devnet"
ALICE = "user:alice"
BOB = "user:bob"

# A deterministic test-only signing key. Not a wallet, a seed, a deployed key or a fixture
# anyone could spend: it exists so a signature can be asserted byte for byte.
TEST_ONLY_KEY = b"ezchain-test-only-custody-key-vector-0001"
TEST_ONLY_OTHER_KEY = b"ezchain-test-only-custody-key-vector-0002"
TEST_ONLY_FINGERPRINT = hashlib.sha256(TEST_ONLY_KEY).hexdigest()
TEST_ONLY_OTHER_FINGERPRINT = hashlib.sha256(TEST_ONLY_OTHER_KEY).hexdigest()


def fingerprint_of(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


@dataclass
class FakeSignerTransport:
    """A test-only signer layer: the shape a USB device or daemon must satisfy.

    ``mode`` selects the answer. It holds a test-only key and returns a signature and the
    public fingerprint, never a key, because :class:`SignerSignature` has no field for one.
    """

    mode: str = "sign"
    key: bytes = TEST_ONLY_KEY
    calls: int = 0
    seen: list[tuple[str, bytes]] = field(default_factory=list)

    def sign(self, handle_id: str, payload: bytes) -> SignerSignature:
        self.calls += 1
        self.seen.append((handle_id, payload))
        if self.mode == "unreachable":
            raise SignerUnreachable("the signer did not answer")
        if self.mode == "denied":
            raise SignerApprovalDenied("the person declined on the device")
        if self.mode == "unknown":
            raise SignerHandleUnknown("no key for that handle")
        if self.mode == "locked":
            raise SignerLocked("the signer is locked")
        if self.mode == "exception":
            raise RuntimeError("driver exploded")
        if self.mode == "empty":
            return SignerSignature(signature=b"", fingerprint=fingerprint_of(self.key))
        if self.mode == "wrong-key":
            return SignerSignature(
                signature=hmac.new(self.key, payload, hashlib.sha256).digest(),
                fingerprint=TEST_ONLY_OTHER_FINGERPRINT,
            )
        return SignerSignature(
            signature=hmac.new(self.key, payload, hashlib.sha256).digest(),
            fingerprint=fingerprint_of(self.key),
        )


def handle(
    *,
    handle_id: str = "h-alice-1",
    owner: str = ALICE,
    custody: str = CUSTODY_HARDWARE,
    fingerprint: str = TEST_ONLY_FINGERPRINT,
    chain: str = CHAIN,
) -> KeyHandle:
    return KeyHandle(
        handle_id=handle_id,
        owner=owner,
        custody=custody,  # type: ignore[arg-type]
        chain=chain,
        address=WALLET,
        fingerprint=fingerprint,
    )


def approved() -> ApprovedIntent:
    return ApprovedIntent(
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


def prepared(intent: ApprovedIntent | None = None) -> PreparedTransaction:
    value = intent or approved()
    payload = TransactionPayload(
        cluster=value.cluster,
        program_id=value.program_id,
        mint=value.mint,
        side=value.side,
        amount=value.amount,
        min_out=value.min_out,
        recipient=value.recipient,
        idempotency_key=value.idempotency_key,
    )
    raw = encode_transaction(payload)
    return PreparedTransaction(raw=raw, payload=payload)


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


def custody_pipeline(
    venue: FakeVenue,
    transport: FakeSignerTransport,
    key_handle: KeyHandle | None = None,
    *,
    owner: str = ALICE,
    mode: str = "paper",
) -> tuple[ExecutionPipeline, MemoryJournal, VaultTransactionSigner]:
    signer = VaultTransactionSigner(
        vault=HardwareSignerVault(transport),
        handle=key_handle or handle(),
        owner=owner,
    )
    journal = MemoryJournal()
    pipe = ExecutionPipeline(
        venue=venue,
        sink=journal,
        limits=limits(),
        kill_switch=KillSwitch(),
        timeout_seconds=0.2,
        signing=SigningBoundary(
            cluster=CLUSTER,
            recipient=WALLET,
            program_id=PROGRAM,
            adapter=venue,
            signer=signer,
        ),
        mode=mode,  # type: ignore[arg-type]
    )
    return pipe, journal, signer


# --- The contract has no room for a key --------------------------------------


def test_a_key_handle_carries_only_public_facts_and_a_hash() -> None:
    fields = set(KeyHandle.__dataclass_fields__)
    for forbidden in ("key", "secret", "seed", "private_key", "passphrase", "mnemonic"):
        assert forbidden not in fields
    assert handle().fingerprint == TEST_ONLY_FINGERPRINT
    assert is_fingerprint(handle().fingerprint)
    assert not is_fingerprint("not-a-hash")
    assert not is_fingerprint(TEST_ONLY_KEY.decode())


def test_a_signature_carries_no_key_material() -> None:
    assert set(VaultSignature.__dataclass_fields__) == {"handle_id", "fingerprint", "signature"}


def test_the_vault_protocol_has_one_operation_and_it_returns_a_signature() -> None:
    operations = [name for name in dir(HardwareSignerVault) if not name.startswith("_")]
    assert operations == ["sign"]


def test_an_unknown_reason_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        VaultRefused("NOT_A_REAL_REASON")


def test_a_fingerprint_that_is_not_a_hash_is_refused() -> None:
    with pytest.raises(ValueError):
        KeyHandle(
            handle_id="h1",
            owner=ALICE,
            custody=CUSTODY_HARDWARE,
            chain=CHAIN,
            address=WALLET,
            fingerprint="not-a-hash",
        )


def test_a_watch_only_handle_is_not_signable() -> None:
    watch = handle(custody=CUSTODY_WATCH)
    assert not watch.signable
    assert {tier for tier in CUSTODY_TIERS if tier in SIGNABLE_CUSTODY} == {
        CUSTODY_LOCAL_VAULT,
        CUSTODY_HARDWARE,
    }


# --- Failure paths: no signature, one typed code ----------------------------


def test_an_unreachable_signer_produces_no_signature() -> None:
    transport = FakeSignerTransport(mode="unreachable")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_UNREACHABLE
    assert transport.calls == 1


def test_a_denied_approval_produces_no_signature() -> None:
    transport = FakeSignerTransport(mode="denied")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_APPROVAL_DENIED


def test_an_unknown_handle_produces_no_signature() -> None:
    transport = FakeSignerTransport(mode="unknown")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(handle_id="h-missing"), owner=ALICE)
    assert exc.value.code == VAULT_UNKNOWN_HANDLE


def test_another_users_handle_refuses_like_an_unknown_one() -> None:
    transport = FakeSignerTransport()
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(owner=BOB), owner=ALICE)
    assert exc.value.code == VAULT_UNKNOWN_HANDLE
    assert transport.calls == 0, "another user's handle must not reach the signer"


def test_a_locked_signer_produces_no_signature() -> None:
    transport = FakeSignerTransport(mode="locked")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_LOCKED


def test_a_watch_only_handle_never_reaches_the_signer() -> None:
    transport = FakeSignerTransport()
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(custody=CUSTODY_WATCH), owner=ALICE)
    assert exc.value.code == VAULT_WATCH_ONLY
    assert transport.calls == 0


def test_a_signer_that_answers_for_another_key_is_refused() -> None:
    transport = FakeSignerTransport(mode="wrong-key")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_SIGN_FAILED


def test_an_empty_signature_is_refused() -> None:
    transport = FakeSignerTransport(mode="empty")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_SIGN_FAILED


def test_a_broken_transport_is_a_typed_refusal_not_a_crash() -> None:
    transport = FakeSignerTransport(mode="exception")
    vault = HardwareSignerVault(transport)
    with pytest.raises(VaultRefused) as exc:
        vault.sign(prepared(), handle(), owner=ALICE)
    assert exc.value.code == VAULT_SIGN_FAILED


def test_no_signature_bytes_are_returned_on_any_failure() -> None:
    for mode, code in (
        ("unreachable", VAULT_UNREACHABLE),
        ("denied", VAULT_APPROVAL_DENIED),
        ("unknown", VAULT_UNKNOWN_HANDLE),
        ("locked", VAULT_LOCKED),
        ("wrong-key", VAULT_SIGN_FAILED),
        ("empty", VAULT_SIGN_FAILED),
    ):
        vault = HardwareSignerVault(FakeSignerTransport(mode=mode))
        with pytest.raises(VaultRefused) as exc:
            vault.sign(prepared(), handle(), owner=ALICE)
        assert exc.value.code == code
        assert code in VAULT_REASONS


# --- The happy path: one signature, for the handle that was asked for --------


def test_the_handle_owner_gets_a_signature_for_their_own_handle() -> None:
    transport = FakeSignerTransport()
    vault = HardwareSignerVault(transport)
    signed = vault.sign(prepared(), handle(), owner=ALICE)

    assert signed.handle_id == "h-alice-1"
    assert signed.fingerprint == TEST_ONLY_FINGERPRINT
    assert signed.signature == hmac.new(TEST_ONLY_KEY, prepared().raw, hashlib.sha256).digest()
    assert transport.seen[0][0] == "h-alice-1"


def test_the_vault_only_ever_receives_the_unsigned_payload() -> None:
    transport = FakeSignerTransport()
    vault = HardwareSignerVault(transport)
    vault.sign(prepared(), handle(), owner=ALICE)
    _, payload = transport.seen[0]
    assert payload == prepared().raw
    assert b"PRIVATE" not in payload


# --- The signer wrapper: binding stays in front of the vault ----------------


def test_the_vault_signer_binds_before_the_vault_is_touched() -> None:
    transport = FakeSignerTransport()
    signer = VaultTransactionSigner(
        vault=HardwareSignerVault(transport), handle=handle(), owner=ALICE
    )
    foreign = replace(approved(), mint="Mint9999999999999999999999999999999999999999")
    with pytest.raises(Exception) as exc:
        signer.sign(prepared(), foreign)
    assert getattr(exc.value, "code", None) == "TRANSACTION_MINT_MISMATCH"
    assert transport.calls == 0, "a changed field must refuse before the vault runs"


def test_the_vault_signer_carries_the_handle_so_live_can_tell_custody_apart() -> None:
    signer = VaultTransactionSigner(
        vault=HardwareSignerVault(FakeSignerTransport()), handle=handle(), owner=ALICE
    )
    assert isinstance(signer, VaultBackedSigner)
    assert signer.fingerprint == TEST_ONLY_FINGERPRINT


def test_a_vault_refusal_becomes_a_signing_refusal_with_the_same_code() -> None:
    transport = FakeSignerTransport(mode="denied")
    signer = VaultTransactionSigner(
        vault=HardwareSignerVault(transport), handle=handle(), owner=ALICE
    )
    with pytest.raises(Exception) as exc:
        signer.sign(prepared(), approved())
    assert getattr(exc.value, "code", None) == VAULT_APPROVAL_DENIED


# --- The pipeline: custody is required in live, paper is unchanged ----------


@pytest.mark.asyncio
async def test_paper_runs_unchanged_when_custody_is_configured() -> None:
    venue = FakeVenue()
    transport = FakeSignerTransport()
    pipe, journal, _ = custody_pipeline(venue, transport)
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert transport.calls == 1
    assert venue.signed[0].payload_digest == prepared().payload_digest
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_paper_without_custody_still_submits_unsigned_unchanged() -> None:
    venue = FakeVenue()
    journal = MemoryJournal()
    pipe = ExecutionPipeline(
        venue=venue,
        sink=journal,
        limits=limits(),
        kill_switch=KillSwitch(),
        timeout_seconds=0.2,
    )
    result = await pipe.run(buy(), snapshot=make_snapshot())
    assert result.status == "filled"
    assert venue.submit_calls == 1
    assert len(journal.groups) == 1


@pytest.mark.asyncio
async def test_live_without_a_signing_boundary_refuses_before_the_venue() -> None:
    venue = FakeVenue()
    pipe = ExecutionPipeline(
        venue=venue,
        sink=MemoryJournal(),
        limits=limits(),
        kill_switch=KillSwitch(),
        timeout_seconds=0.2,
        mode="live",
    )
    result = await pipe.run(buy(), snapshot=make_snapshot())
    assert result.status == "rejected"
    assert result.reason == SIGNING_BOUNDARY_REQUIRED
    assert venue.submit_calls == 0


@pytest.mark.asyncio
async def test_live_with_an_in_memory_signer_refuses_for_want_of_custody() -> None:
    venue = FakeVenue()
    pipe = ExecutionPipeline(
        venue=venue,
        sink=MemoryJournal(),
        limits=limits(),
        kill_switch=KillSwitch(),
        timeout_seconds=0.2,
        signing=SigningBoundary(
            cluster=CLUSTER,
            recipient=WALLET,
            program_id=PROGRAM,
            adapter=venue,
            signer=LocalTransactionSigner(StaticTestKeyRing()),
        ),
        mode="live",
    )
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == VAULT_BOUNDARY_REQUIRED
    assert venue.submit_calls == 0, "a key nobody custodies must not spend real money"


@pytest.mark.asyncio
async def test_live_with_a_watch_only_handle_refuses() -> None:
    venue = FakeVenue()
    pipe, _, _ = custody_pipeline(
        venue, FakeSignerTransport(), handle(custody=CUSTODY_WATCH), mode="live"
    )
    result = await pipe.run(buy(), snapshot=make_snapshot())
    assert result.status == "rejected"
    assert result.reason == VAULT_BOUNDARY_REQUIRED
    assert venue.submit_calls == 0


@pytest.mark.asyncio
async def test_live_with_a_custody_handle_reaches_the_vault() -> None:
    venue = FakeVenue()
    transport = FakeSignerTransport()
    pipe, _, _ = custody_pipeline(venue, transport, mode="live")
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "filled"
    assert transport.calls == 1
    assert transport.seen[0][0] == "h-alice-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("unreachable", VAULT_UNREACHABLE),
        ("denied", VAULT_APPROVAL_DENIED),
        ("unknown", VAULT_UNKNOWN_HANDLE),
        ("locked", VAULT_LOCKED),
        ("wrong-key", VAULT_SIGN_FAILED),
    ],
)
async def test_a_vault_failure_in_live_never_reaches_the_venue(mode: str, code: str) -> None:
    venue = FakeVenue()
    pipe, journal, _ = custody_pipeline(venue, FakeSignerTransport(mode=mode), mode="live")
    result = await pipe.run(buy(), snapshot=make_snapshot())

    assert result.status == "rejected"
    assert result.reason == code
    assert result.fill is None
    assert venue.submit_calls == 0
    assert journal.groups == ()


# --- Automation tiers and the disclosure points -----------------------------


def test_manual_is_the_default_tier_and_needs_no_opt_in() -> None:
    setting = AutomationSetting(user=ALICE, tier=AUTOMATION_MANUAL)
    assert setting.requires_approval
    assert setting.disclosure is None


def test_full_automation_is_refused_without_the_opt_in_disclosure() -> None:
    with pytest.raises(ValueError):
        AutomationSetting(user=ALICE, tier=AUTOMATION_FULL)


def test_full_automation_needs_the_disclosure_for_that_same_user() -> None:
    alice_disclosure = RiskDisclosure(
        point=DISCLOSURE_AUTOMATION_OPT_IN,
        user=ALICE,
        acknowledged_at=utcnow(),
        version="2026-11-13",
    )
    with pytest.raises(ValueError):
        AutomationSetting(user=BOB, tier=AUTOMATION_FULL, disclosure=alice_disclosure)
    setting = AutomationSetting(user=ALICE, tier=AUTOMATION_FULL, disclosure=alice_disclosure)
    assert not setting.requires_approval


def test_a_key_add_disclosure_is_not_an_automation_disclosure() -> None:
    add = RiskDisclosure(
        point=DISCLOSURE_KEY_ADD,
        user=ALICE,
        acknowledged_at=utcnow(),
        version="2026-11-13",
    )
    with pytest.raises(ValueError):
        AutomationSetting(user=ALICE, tier=AUTOMATION_FULL, disclosure=add)


def test_an_unknown_disclosure_point_is_refused() -> None:
    with pytest.raises(ValueError):
        RiskDisclosure(
            point="somewhere",
            user=ALICE,
            acknowledged_at=utcnow(),
            version="v1",
        )


def test_a_local_vault_handle_signs_too_hardware_is_one_option() -> None:
    transport = FakeSignerTransport()
    vault = HardwareSignerVault(transport)
    signed = vault.sign(prepared(), handle(custody=CUSTODY_LOCAL_VAULT), owner=ALICE)
    assert signed.signature


@dataclass
class StaticTestKeyRing:
    key: bytes = TEST_ONLY_KEY

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self.key, payload, hashlib.sha256).digest()
