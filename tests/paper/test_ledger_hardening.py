"""Ledger hardening: minor-unit rounding, zero drift, replay determinism, seals.

The ledger is the only accountant (CONTRACT I1). Every USD figure it writes is an
integer number of minor units at :data:`ezchain_core.money.MONEY_QUANTUM`; the tests
here prove the policy on the arithmetic itself, independent of any read model.

The mixed-tape property walks random sequences of the operations this ledger actually
has: a funding top-up (the only credit path after genesis), a buy with an explicit quote
notional, a buy whose notional is derived from ``quantity * price``, a full exit, and a
partial sell whose proceeds *and* cost basis are both derived. There is **no withdrawal
or transfer journal** in this slice — paper cash leaves the book only as a fill's
notional and fee — and the tapes say so instead of implying an exit path that does not
exist.

After *every* operation the test checks that the group sums to exactly zero minor units,
that summing Decimals and summing integer minor units agree to the last unit, that replay
of the same tape twice produces the same balances and the same digest, and that cash, cost
basis and funded capital equal independent integer accumulators.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from ezchain_core import buy_entries, genesis_entries, sell_entries
from ezchain_core.ledger import (
    FUNDING_ACCOUNT,
    LEDGER_GENESIS_DIGEST,
    LedgerEntry,
    LedgerTamperedError,
    assert_balanced,
    cash_account,
    entry_from_row,
    funding_entries,
    ledger_digest,
    position_account,
    replay_balances,
    replay_position,
    seal_entries,
    verify_seal,
)
from ezchain_core.money import (
    MONEY_QUANTUM,
    CostPreview,
    FloatMoneyError,
    MoneyError,
    SourceRef,
    SubQuantumError,
    bps_fee,
    exact_sum,
    from_minor_units,
    preview_cost,
    quantize_money,
    require_money,
    to_minor_units,
)

GENESIS = Decimal("10000")
CHAIN = "base"
TOKEN = "0x00000000000000000000000000000000000000a1"
POSITION = position_account(CHAIN, TOKEN)
START = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
PRECISION = 120


def _source() -> SourceRef:
    return SourceRef("jupiter", None, START)


def _buy(notional: Decimal, price: Decimal, fee_bps: int) -> tuple[LedgerEntry, ...]:
    return buy_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=notional / price,
        price_usd=price,
        fee_bps=fee_bps,
        notional_usd=notional,
    )


def _stamp(
    entries: tuple[LedgerEntry, ...] | list[LedgerEntry],
    *,
    group: str,
    at: datetime,
    wallet: str,
    actor: str = "human:test",
) -> list[LedgerEntry]:
    """Give a computed journal the attribution the persisted row will carry."""
    return [
        replace(
            entry,
            group_id=group,
            created_at=at,
            wallet_id=wallet if entry.account == cash_account() else None,
            actor_kind="human",
            actor_id=actor,
        )
        for entry in entries
    ]


def _preview(**overrides: object) -> CostPreview:
    inputs: dict[str, object] = {
        "kind": "jupiter",
        "notional_usd": Decimal("100"),
        "venue_fee_usd": Decimal("0"),
        "price_impact_pct": None,
        "amount_out": Decimal("50"),
        "fee_bps": 30,
        "source": _source(),
    }
    inputs.update(overrides)
    return preview_cost(**inputs)  # type: ignore[arg-type]


def test_rounding_policy_is_one_minor_unit_of_exact_usd() -> None:
    assert MONEY_QUANTUM == Decimal("0.00000001")
    assert to_minor_units(Decimal("1")) == 100_000_000
    assert from_minor_units(100_000_000) == Decimal("1")
    assert to_minor_units(from_minor_units(123_456_789)) == 123_456_789


def test_minor_units_are_exact_beyond_the_ambient_precision() -> None:
    """A 22-integer-digit notional converts exactly, not at the 28-digit default."""
    base = Decimal("1234567890123456789012.00000001")
    neighbour = Decimal("1234567890123456789012.00000002")
    assert to_minor_units(base) == 123_456_789_012_345_678_901_200_000_001
    assert to_minor_units(neighbour) - to_minor_units(base) == 1
    # The same conversion without a sized context cannot see the last unit.
    with localcontext() as context:
        context.prec = 28
        assert int(base.scaleb(8)) != 123_456_789_012_345_678_901_200_000_001
    assert from_minor_units(to_minor_units(base)) == base


def test_assert_balanced_rejects_one_minor_unit_at_high_notional() -> None:
    """The review's reproduction: a group exactly one minor unit out must fail.

    The legs are negated with ``copy_negate``: a bare ``-value`` would itself round at the
    ambient 28-digit precision, and the test would then prove the wrong thing.
    """
    cash = Decimal("1234567890123456789012.00000001")
    position = Decimal("1234567890123456789012.00000002")
    with pytest.raises(ValueError, match="minor units"):
        assert_balanced(
            (
                LedgerEntry(cash_account(), "USD", cash, cash, "buy"),
                LedgerEntry(
                    "position:base:token", "T", Decimal("1"), position.copy_negate(), "buy"
                ),
            )
        )
    # One unit in the other direction is rejected just as hard.
    with pytest.raises(ValueError, match="minor units"):
        assert_balanced(
            (
                LedgerEntry(cash_account(), "USD", cash, cash, "buy"),
                LedgerEntry("position:base:token", "T", Decimal("1"), cash.copy_negate(), "buy"),
                LedgerEntry("fees:USD", "USD", MONEY_QUANTUM, MONEY_QUANTUM, "fee"),
            )
        )
    # The exact pair balances at the same notional, so the rejection above is the unit.
    assert_balanced(
        (
            LedgerEntry(cash_account(), "USD", cash, cash, "buy"),
            LedgerEntry("position:base:token", "T", Decimal("1"), cash.copy_negate(), "buy"),
        )
    )


def test_minor_units_fail_closed_off_the_quantum() -> None:
    with pytest.raises(SubQuantumError):
        to_minor_units(Decimal("1234567890123456789012.000000001"))
    with pytest.raises(SubQuantumError):
        to_minor_units(Decimal("0.000000001"))


def test_rounding_policy_is_half_even_at_the_ledger_boundary() -> None:
    # Exactly halfway between 0 and 1e-8: the even neighbour wins.
    assert quantize_money(Decimal("0.000000005")) == Decimal("0.00000000")
    # Exactly halfway between 1e-8 and 2e-8: 2e-8 is the even neighbour.
    assert quantize_money(Decimal("0.000000015")) == Decimal("0.00000002")
    # A value already on the quantum is unchanged.
    assert quantize_money(Decimal("0.00000001")) == Decimal("0.00000001")
    assert quantize_money(Decimal("-0.000000015")) == Decimal("-0.00000002")


def test_bps_fee_is_exact_for_long_tail_notionals() -> None:
    """The bps product is sized from its operands, not from the 28-digit default."""
    notional = Decimal("1234567890123456789012.5")
    with localcontext() as context:
        context.prec = PRECISION
        expected = quantize_money(notional * Decimal(30) / Decimal(10_000))
    assert bps_fee(notional, 30) == expected
    assert to_minor_units(bps_fee(notional, 30)) == to_minor_units(expected)


def test_fee_quantum_change_matches_the_documented_table() -> None:
    """docs/LEDGER.md §2.1 is pinned by tests: the old 1e-6 rule and the shipped 1e-8 rule.

    The change is not dust-only. Every fee whose exact value carries more than six
    decimals moves, so the docs must name a mid-sized notional, not just sub-cent ones.
    """
    table = {
        (Decimal("0.0000033333"), 30): (Decimal("0.000000"), Decimal("0.00000001")),
        (Decimal("0.00000101"), 50): (Decimal("0.000000"), Decimal("0.00000001")),
        (Decimal("0.000412"), 30): (Decimal("0.000001"), Decimal("0.00000124")),
        (Decimal("1234.5678"), 30): (Decimal("3.703703"), Decimal("3.70370340")),
        (Decimal("100"), 30): (Decimal("0.300000"), Decimal("0.30000000")),
        (Decimal("0.0000003333"), 30): (Decimal("0.000000"), Decimal("0")),
    }
    for (notional, fee_bps), (old, new) in table.items():
        assert bps_fee(notional, fee_bps) == new, (notional, fee_bps)
        legacy = (notional * Decimal(fee_bps) / Decimal(10_000)).quantize(Decimal("0.000001"))
        assert legacy == old, (notional, fee_bps)
    # At least one documented row is a real change above the micro-dollar, not dust.
    assert bps_fee(Decimal("0.000412"), 30) != Decimal("0.000001")


def test_preview_worst_case_out_is_exact_and_on_the_ledger_quantum() -> None:
    """``worst_case_out`` is 8-dp ledger money, computed exactly for long-tail amounts."""
    preview = _preview(amount_out=Decimal("100.0000001"))
    assert preview.worst_case_out == Decimal("99.50000010")
    assert preview.worst_case_out is not None
    assert require_money(preview.worst_case_out) == preview.worst_case_out

    tail = Decimal("1234567890123456789012.0000001")
    with localcontext() as context:
        context.prec = PRECISION
        expected = quantize_money(tail * (Decimal(10_000) - Decimal(50)) / Decimal(10_000))
    tail_preview = _preview(amount_out=tail)
    assert tail_preview.worst_case_out == expected
    assert tail_preview.worst_case_out is not None
    assert to_minor_units(tail_preview.worst_case_out) == to_minor_units(expected)


def test_minor_units_reject_floats_at_every_boundary() -> None:
    """A float never crosses the ledger boundary, not even as ``Decimal(str(x))``."""
    with pytest.raises(FloatMoneyError):
        quantize_money(0.1)
    with pytest.raises(FloatMoneyError):
        require_money(0.1)
    with pytest.raises(FloatMoneyError):
        to_minor_units(0.1)
    with pytest.raises(FloatMoneyError):
        bps_fee(10.0, 30)  # type: ignore[arg-type]
    with pytest.raises(FloatMoneyError):
        LedgerEntry(cash_account(), "USD", Decimal("1"), 0.1, "buy")  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        _preview(amount_out=1e-7)


def test_minor_units_reject_non_finite_values() -> None:
    with pytest.raises(ValueError):
        quantize_money(Decimal("NaN"))
    with pytest.raises(ValueError):
        quantize_money(Decimal("Infinity"))
    with pytest.raises(ValueError):
        to_minor_units(Decimal("Infinity"))


def test_sub_minor_values_fail_closed() -> None:
    with pytest.raises(SubQuantumError):
        require_money(Decimal("1.000000001"))
    with pytest.raises(SubQuantumError):
        LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1.000000001"), "buy")


def test_assert_balanced_is_exact_to_the_minor_unit() -> None:
    entries = (
        LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1"), "buy"),
        LedgerEntry("position:x:y", "Y", Decimal("1"), Decimal("-1.00000001"), "buy"),
    )
    with pytest.raises(ValueError):
        assert_balanced(entries)
    assert_balanced(
        (
            LedgerEntry(cash_account(), "USD", Decimal("1"), Decimal("1"), "buy"),
            LedgerEntry("position:x:y", "Y", Decimal("1"), Decimal("-1"), "buy"),
        )
    )


def test_funding_entries_are_the_only_credit_path() -> None:
    entries = funding_entries(Decimal("25.5"))
    assert_balanced(entries)
    assert [entry.account for entry in entries] == [cash_account(), FUNDING_ACCOUNT]
    assert to_minor_units(entries[0].usd_value) == to_minor_units(Decimal("25.5"))
    with pytest.raises(ValueError):
        funding_entries(Decimal("0"))
    with pytest.raises(SubQuantumError):
        funding_entries(Decimal("1.000000001"))


def test_replay_balances_is_canonical_and_deterministic() -> None:
    tape = list(genesis_entries(GENESIS)) + list(_buy(Decimal("200"), Decimal("2000"), 30))
    first = replay_balances(tape)
    second = replay_balances(list(tape))
    assert first == second
    by_account = {row.account: row for row in first}
    assert by_account[cash_account()].usd_minor == to_minor_units(Decimal("9799.4"))
    assert by_account[cash_account()].amount == Decimal("9799.4")


def test_seal_payload_is_unambiguous_across_field_boundaries() -> None:
    """Two distinct entries that collided under v1's ``|`` join now hash differently.

    The v1 link was ``"|".join((schema, head, index, account, asset, amount, usd_value,
    kind))``. That payload is forged here for both entries to prove the collision was
    real — one head covered two different tapes — and then the v2 seal is shown to split
    them.
    """
    left = LedgerEntry(
        "position:base:0xa|b",
        "T",
        Decimal("1"),
        Decimal("1"),
        "buy",
        group_id="g",
        created_at=START,
    )
    right = LedgerEntry(
        "position:base:0xa",
        "b|T",
        Decimal("1"),
        Decimal("1"),
        "buy",
        group_id="g",
        created_at=START,
    )
    assert (left.account, left.asset) != (right.account, right.asset)

    def v1_digest(entry: LedgerEntry) -> str:
        payload = "|".join(
            (
                "ezchain.ledger/v1",
                LEDGER_GENESIS_DIGEST,
                "1",
                entry.account,
                entry.asset,
                str(entry.amount),
                str(entry.usd_value),
                entry.kind,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # The forgery: the old scheme gives one head for two different entries.
    assert v1_digest(left) == v1_digest(right)
    # The shipped scheme does not: no field value can absorb a neighbour's text.
    assert ledger_digest([left]) != ledger_digest([right])
    assert seal_entries([left]).head != seal_entries([right]).head


def test_seal_covers_group_time_wallet_and_actor() -> None:
    """A tape whose attribution changed is a different tape, not the same head."""
    base = _stamp(
        _buy(Decimal("200"), Decimal("2000"), 30),
        group="order-1",
        at=START,
        wallet="paper-wallet:settlement",
    )
    reference = seal_entries(base)
    assert reference.head == ledger_digest(base)
    variations = (
        {"group_id": "order-2"},
        {"created_at": START + timedelta(seconds=1)},
        {"wallet_id": "paper-wallet:base"},
        {"bucket_id": "agent:key-1"},
        {"actor_kind": "agent"},
        {"actor_id": "human:someone-else"},
    )
    for change in variations:
        changed = [replace(entry, **change) for entry in base]
        assert ledger_digest(changed) != reference.head, change
        assert seal_entries(changed).head != reference.head, change


def test_entry_round_trips_from_a_persisted_row_into_the_seal() -> None:
    """The seal is taken over the projection of the row, attribution included."""

    class Row:
        group_id = "order-9"
        account = cash_account()
        asset = "USD"
        amount = Decimal("-200")
        usd_value = Decimal("-200")
        kind = "buy"
        wallet_id = "paper-wallet:settlement"
        bucket_id = "agent:key-1"
        actor_kind = "human"
        actor_id = "human:test"
        created_at = START.replace(tzinfo=None)  # SQLite returns a naive UTC datetime

    projected = entry_from_row(Row())
    assert projected.group_id == "order-9"
    assert projected.created_at == START
    assert projected.wallet_id == "paper-wallet:settlement"
    assert projected.bucket_id == "agent:key-1"
    assert projected.actor_kind == "human"
    assert projected.actor_id == "human:test"
    assert ledger_digest([projected]) != ledger_digest([replace(projected, actor_id="agent:1")])
    # The bucket an agent spends from is attribution too: moving a cash leg to another
    # bucket is a different tape, not the same journal with a different label.
    assert ledger_digest([projected]) != ledger_digest(
        [replace(projected, bucket_id="agent:key-2")]
    )


def test_a_naive_timestamp_is_refused_by_a_sealed_entry() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        LedgerEntry(
            cash_account(),
            "USD",
            Decimal("1"),
            Decimal("1"),
            "buy",
            created_at=datetime(2026, 11, 14, 12, 0),
        )


def test_immutability_seal_is_stable_and_detects_a_changed_entry() -> None:
    tape = list(genesis_entries(GENESIS)) + list(_buy(Decimal("200"), Decimal("2000"), 30))
    seal = seal_entries(tape)
    assert seal.count == len(tape)
    assert seal.head == ledger_digest(tape)
    assert seal_entries(list(tape)) == seal
    verify_seal(tape, seal)

    tampered = list(tape)
    original = tampered[-1]
    tampered[-1] = LedgerEntry(
        original.account,
        original.asset,
        original.amount + Decimal("1"),
        original.usd_value,
        original.kind,
    )
    assert ledger_digest(tampered) != seal.head
    with pytest.raises(LedgerTamperedError):
        verify_seal(tampered, seal)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_zero_drift_across_mixed_random_tapes(seed: int) -> None:
    """A mixed tape balances to exactly zero minor units after every operation.

    The mix is real: funding top-ups, buys with an explicit quote notional, buys whose
    notional the journal derives from ``quantity * price``, full exits, and partial sells
    whose proceeds *and* cost basis are derived. There is no withdrawal leg because this
    slice has none: paper cash leaves the book only as a fill's notional and fee.

    The accumulators below are per-group integer sums of the tape itself, so a replay that
    drops, duplicates or re-rounds a row cannot agree with them.
    """
    rng = random.Random(seed)
    tape: list[LedgerEntry] = list(genesis_entries(GENESIS))
    price = Decimal("2")
    position_amounts: list[Decimal] = []
    cash_minor = to_minor_units(GENESIS)
    cost_minor = 0
    funded_minor = 0
    seen = {
        "funding": 0,
        "derived_notional": 0,
        "explicit_notional": 0,
        "full_exit": 0,
        "partial_derived": 0,
    }

    for step in range(250):
        price = max(
            MONEY_QUANTUM,
            price + from_minor_units(rng.randrange(-10_000_000, 10_000_000)),
        )
        step_rng = random.Random(f"{seed}:{step}")
        fee_bps = step_rng.choice([0, 10, 30, 50])
        group = f"{seed}:{step}"
        at = START + timedelta(seconds=step)
        wallet = "paper-wallet:settlement" if step % 3 else "paper-wallet:base"
        roll = step_rng.random()
        entries: list[LedgerEntry]
        # The generator reads its held quantity off the tape it has built, exactly, so a
        # division's decimal expansion never rounds the generator away from the journal.
        quantity = exact_sum(position_amounts)

        if roll < 0.12:
            # A no-risk top-up: the only credit path after genesis.
            amount = from_minor_units(step_rng.randrange(1, 5_000_000))
            entries = _stamp(funding_entries(amount), group=group, at=at, wallet=wallet)
            seen["funding"] += 1

        elif quantity <= 0 or roll < 0.62:
            if step_rng.random() < 0.5:
                # The derived path: the journal computes quantity * price itself.
                notional = quantize_money(
                    from_minor_units(step_rng.randrange(100, 50_000_000)) * price
                )
                if notional <= 0:
                    continue
                bought = notional / price
                entries = _stamp(
                    buy_entries(
                        chain=CHAIN,
                        token_address=TOKEN,
                        quantity=bought,
                        price_usd=price,
                        fee_bps=fee_bps,
                    ),
                    group=group,
                    at=at,
                    wallet=wallet,
                )
                seen["derived_notional"] += 1
            else:
                notional = from_minor_units(step_rng.randrange(100, 50_000_000))
                bought = notional / price
                entries = _stamp(_buy(notional, price, fee_bps), group=group, at=at, wallet=wallet)
                seen["explicit_notional"] += 1

        else:
            fraction = (
                Decimal(100) if step_rng.random() < 0.3 else Decimal(step_rng.randrange(1, 100))
            )
            with localcontext() as context:
                # The walk's own quantity and cost division must run at ledger precision:
                # at the ambient 28 digits `quantity * fraction / 100` can round *up* past
                # the held quantity and turn a full exit into a short.
                context.prec = PRECISION
                sold = min(quantity, quantity * fraction / 100)
                average = from_minor_units(cost_minor) / quantity
                basis = quantize_money(sold * average)
            if sold <= 0 or quantize_money(sold * price) <= 0:
                continue
            # A full exit, or a partial exit whose rounded basis would overshoot what is
            # held, books the cost actually held so the position closes exactly.
            full_exit = sold >= quantity or basis >= from_minor_units(cost_minor)
            if full_exit:
                sold = quantity
                basis = from_minor_units(cost_minor)
                seen["full_exit"] += 1
            else:
                seen["partial_derived"] += 1
            # Proceeds always come from quantity * price inside the factory; a partial
            # exit derives its cost basis the same way.
            entries = _stamp(
                sell_entries(
                    chain=CHAIN,
                    token_address=TOKEN,
                    quantity=sold,
                    price_usd=price,
                    avg_cost_usd=average,
                    fee_bps=fee_bps,
                    cost_basis_usd=basis if full_exit else None,
                ),
                group=group,
                at=at,
                wallet=wallet,
            )

        # The factory invariant: the group is exactly balanced in integer minor units.
        assert sum(to_minor_units(entry.usd_value) for entry in entries) == 0
        # Summing Decimals and summing integer minor units are the same number; the
        # Decimal sum runs at a precision derived from its operands, never the ambient one.
        assert to_minor_units(exact_sum([e.usd_value for e in entries])) == 0
        # Every written value is a whole number of minor units.
        assert all(require_money(entry.usd_value) == entry.usd_value for entry in entries)

        tape.extend(entries)
        position_amounts.extend(entry.amount for entry in entries if entry.account == POSITION)
        cash_minor += sum(
            to_minor_units(entry.usd_value) for entry in entries if entry.account == cash_account()
        )
        cost_minor += sum(
            to_minor_units(entry.usd_value) for entry in entries if entry.account == POSITION
        )
        funded_minor += -sum(
            to_minor_units(entry.usd_value) for entry in entries if entry.account == FUNDING_ACCOUNT
        )

        # Replay is deterministic: same tape, same balances, same evidence.
        balances = replay_balances(tape)
        assert replay_balances(list(tape)) == balances
        if step % 25 == 0:
            seal = seal_entries(tape)
            assert seal == seal_entries(list(tape))
            verify_seal(tape, seal)

        # Replay reproduces every account's integer total exactly, and funded capital is
        # its own account rather than being folded into cash or PnL.
        by_account = {row.account: row for row in balances}
        assert by_account[cash_account()].usd_minor == cash_minor, (
            f"cash drift at seed {seed} step {step}"
        )
        if funded_minor:
            assert by_account[FUNDING_ACCOUNT].usd_minor == -funded_minor
        if cost_minor or POSITION in by_account:
            assert by_account[POSITION].usd_minor == cost_minor

        replayed_quantity, replayed_cost = replay_position(
            entry for entry in tape if entry.account == POSITION
        )
        with localcontext() as context:
            context.prec = PRECISION
            exact_quantity = sum(
                (entry.amount for entry in tape if entry.account == POSITION), Decimal(0)
            )
        assert replayed_quantity == exact_quantity
        assert to_minor_units(replayed_cost) == cost_minor

    # A long tape leaves no residual at all: the whole ledger sums to zero.
    assert sum(to_minor_units(entry.usd_value) for entry in tape) == 0
    assert len(tape) > 500
    assert seen["funding"] > 0
    assert seen["derived_notional"] > 0
    assert seen["explicit_notional"] > 0
    assert seen["full_exit"] > 0
    assert seen["partial_derived"] > 0
