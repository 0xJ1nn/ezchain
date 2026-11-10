"""Position protection: arm lifecycle, fairness, cancel and profile resolution.

Test-first for issue #103. These tests pin the small vertical slice: a PAPER
arm is created against an open position, evaluated on an engine tick, and a
trigger fires through the same OrderExecutor the scheduler uses, so the paper
service journals it exactly like a manual fill (CONTRACT I1/I2). Trailing
fairness: a stop that never went above water never triggers. Cancelled and
expired arms never fire; unknown/stale marks fail closed without an order.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import ezchain_api.models  # noqa: F401
from ezchain_api.models import LedgerEntryRow, ProtectionArmRow, ProtectionEventRow
from ezchain_core.protection import (
    ProtectionArm,
    ProtectionProfile,
    evaluate_arm,
    resolve_profile,
    validate_arm,
)
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine.protection import (
    evaluate_all_arms,
    evaluate_arm_row,
)
from ezchain_engine.scheduler import OrderRejected

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def make_arm(**overrides: Any) -> ProtectionArm:
    defaults: dict[str, Any] = {
        "id": "arm-1",
        "chain": CHAIN,
        "token_address": TOKEN,
        "token_symbol": "TEST",
        "kind": "stop_loss",
        "status": "armed",
        "params": validate_arm("stop_loss", {"price_usd": "0.90"}),
        "peak_usd": None,
        "triggered_level_indexes": (),
        "triggered_at": None,
        "order_id": None,
        "expires_at": None,
        "profile_id": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return ProtectionArm(**defaults)


def make_profile(
    name: str, chain: str | None, *, kind: str = "manual", **defaults: Any
) -> ProtectionProfile:
    return ProtectionProfile(
        id=f"profile-{name}",
        name=name,
        chain=chain,
        kind=kind,  # type: ignore[arg-type]
        stop_loss_pct=defaults.get("stop_loss_pct"),
        trailing_pct=defaults.get("trailing_pct"),
        take_profit_pct=defaults.get("take_profit_pct"),
        rules=defaults.get("rules", ()),
        created_at=NOW,
        updated_at=NOW,
    )


class FakeSource:
    def __init__(self, snapshot: TokenSnapshot) -> None:
        self.snapshot = snapshot

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return self.snapshot


class FakeExecutor:
    def __init__(self, order_id: str = "ord-prot-1") -> None:
        self.order_id = order_id
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        return self.order_id


def add_position(session_factory: sessionmaker[Session], *, quantity="100", cost="1.00") -> None:
    from ezchain_core.ledger import position_account

    with session_factory() as session:
        session.add(
            LedgerEntryRow(
                group_id="g1",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal(quantity),
                usd_value=Decimal(cost) * Decimal(quantity),
                kind="buy",
            )
        )
        session.commit()


def add_arm(session_factory: sessionmaker[Session], **overrides: Any) -> str:
    defaults: dict[str, Any] = {
        "id": "arm-1",
        "chain": CHAIN,
        "token_address": TOKEN,
        "token_symbol": "TEST",
        "kind": "stop_loss",
        "status": "armed",
        "params": {"price_usd": "0.90"},
        "peak_usd": None,
        "triggered_levels": [],
        "profile_id": None,
    }
    defaults.update(overrides)
    with session_factory() as session:
        row = ProtectionArmRow(**defaults)
        session.add(row)
        session.commit()
        return row.id


async def test_stop_loss_triggers_on_the_same_order_path(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(session_factory)
    source = FakeSource(make_snapshot(price="0.85", change_h1="0"))
    executor = FakeExecutor()

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, executor
        )

    assert result.action == "trigger"
    assert result.reason == "STOP_LOSS"
    assert len(executor.requests) == 1
    assert executor.requests[0].side == "sell"
    assert executor.requests[0].reduce_only is True
    assert executor.requests[0].quantity == Decimal("100")
    # 100 held at 0.85 = 85 USD of value, one full-close order.
    assert executor.requests[0].amount_usd == Decimal("85")
    with session_factory() as session:
        row = session.get(ProtectionArmRow, "arm-1")
        assert row is not None
        assert row.status == "triggered"
        assert row.order_id == "ord-prot-1"


async def test_cancelled_arm_never_fires(session_factory: sessionmaker[Session]) -> None:
    add_position(session_factory)
    add_arm(session_factory, status="cancelled")
    source = FakeSource(make_snapshot(price="0.50", change_h1="0"))
    executor = FakeExecutor()

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, executor
        )

    assert result.action == "hold"
    assert result.reason == "NOT_ARMED"
    assert executor.requests == []


def test_trailing_stop_never_above_water_does_not_trigger() -> None:
    arm = make_arm(
        kind="trailing_stop",
        params=validate_arm("trailing_stop", {"trailing_pct": "5"}),
    )
    # Entry 10, price only ever below water (9 -> 8.5): no peak, no trigger.
    held = evaluate_arm(
        arm,
        price_usd=Decimal("9"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("10"),
    )
    assert held.action == "hold"
    assert held.peak_usd is None
    lower = evaluate_arm(
        arm,
        price_usd=Decimal("8.5"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("10"),
    )
    assert lower.action == "hold"
    assert lower.peak_usd is None

    # Above water: peak tracks 12, then a 5% dip to 10 triggers at 11.40.
    above = evaluate_arm(
        arm,
        price_usd=Decimal("12"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("10"),
    )
    assert above.peak_usd == Decimal("12")
    tripped = evaluate_arm(
        replace(arm, peak_usd=above.peak_usd),
        price_usd=Decimal("10"),
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("10"),
    )
    assert tripped.action == "trigger"
    assert tripped.reason == "TRAILING_STOP"


async def test_take_profit_ladder_sells_one_level_at_a_time(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(
        session_factory,
        kind="take_profit",
        params={
            "levels": [
                {"gain_pct": "20", "fraction": "0.5"},
                {"gain_pct": "40", "fraction": "0.5"},
            ]
        },
    )
    source = FakeSource(make_snapshot(price="1.25", change_h1="0"))
    executor = FakeExecutor()

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, executor
        )

    assert result.action == "trigger"
    assert result.reason == "TAKE_PROFIT_LEVEL"
    assert result.level_index == 0
    # Half of 100 x 1.25 = 62.50 USD, not the whole position.
    assert executor.requests[0].amount_usd == Decimal("62.50")
    assert executor.requests[0].quantity == Decimal("50")
    with session_factory() as session:
        row = session.get(ProtectionArmRow, "arm-1")
        assert row is not None
        assert row.status == "armed"
        assert row.triggered_levels == [0]


async def test_expired_and_unknown_price_fail_closed(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(session_factory, expires_at=NOW - timedelta(seconds=1))
    source = FakeSource(make_snapshot(price="0.50", change_h1="0"))
    executor = FakeExecutor()

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, executor, now=NOW
        )

    assert result.reason == "EXPIRED"
    assert executor.requests == []

    arm = make_arm()
    unknown = evaluate_arm(
        arm,
        price_usd=None,
        position_quantity=Decimal("100"),
        position_avg_cost_usd=Decimal("1"),
    )
    assert unknown.action == "hold"
    assert unknown.reason == "PRICE_UNKNOWN"


def test_profile_resolution_prefers_the_per_chain_profile() -> None:
    profiles = [
        make_profile("global", None, stop_loss_pct=Decimal("8")),
        make_profile("solana", CHAIN, stop_loss_pct=Decimal("5")),
    ]
    resolved = resolve_profile(profiles, CHAIN)
    assert resolved is not None
    assert resolved.stop_loss_pct == Decimal("5")
    other = resolve_profile(profiles, "base")
    assert other is not None
    # No per-chain profile for base: the global one applies.
    assert other.name == "global"
    assert resolve_profile([], CHAIN) is None


class PaperDexSource:
    """A DexScreener stand-in returning the live price for the paper journal test."""

    def __init__(self, price: Decimal) -> None:
        self.price = price

    def snapshot(self) -> TokenSnapshot:
        from dataclasses import replace

        from ezchain_core.types import PairInfo

        base = make_snapshot(price=str(self.price), change_h1="0")
        return replace(
            base,
            pairs=(
                PairInfo(
                    pair_address="0xpair",
                    dex="jupiter",
                    quote_token="USDC",
                    price_usd=self.price,
                    liquidity_usd=Decimal("500000"),
                    volume_24h_usd=Decimal("1000000"),
                    url=None,
                ),
            ),
        )

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        return self.snapshot()


class FakeJupiterFake:
    """One-rung Jupiter quote with the dex price, like the paper conftest fake."""

    kind = "jupiter"

    def __init__(self, price: Decimal = Decimal("1.00")) -> None:
        self.price = price

    def supports(self, chain: Any) -> bool:
        return chain.id == CHAIN

    async def quote(self, request: Any) -> Any:
        from ezchain_core.types import Quote, SourceRef, utcnow

        price = self.price
        if request.side == "buy":
            amount_in = request.amount_usd
            amount_out = amount_in / price
            token_in, token_out = "USDC", request.token_address
        else:
            amount_in = request.quantity
            amount_out = amount_in * price
            token_in, token_out = request.token_address, "USDC"
        return Quote(
            chain=CHAIN,
            token_in=token_in,
            token_out=token_out,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=price,
            price_impact_pct=Decimal("0.01"),
            fee_usd=Decimal(0),
            kind="jupiter",
            route="Jupiter",
            source=SourceRef("jupiter", "https://jup.ag", utcnow()),
        )


async def test_arm_trigger_journals_like_any_other_fill(
    session_factory: sessionmaker[Session],
) -> None:
    """The full money path: paper buy, arm trip, ledger journal.

    The trade result must journal through paper.place_order groups, each
    group balanced, with the same sell entries a manual sell writes.
    """
    from ezchain_adapters import QuoteRouter
    from ezchain_api.config import Settings
    from ezchain_api.services import paper
    from ezchain_api.services.executor import PaperOrderExecutor
    from ezchain_core.types import OrderRequest as PaperOrderRequest

    async def _place(sess: Session, req: PaperOrderRequest, dex: PaperDexSource) -> str:
        router = QuoteRouter(providers=[FakeJupiterFake(price=dex.price)], dexscreener=dex)  # type: ignore[arg-type]
        row = await paper.place_order(sess, req, settings, dex, router)  # type: ignore[arg-type]
        return row.status

    # Buy 100 USD at 1.00 through the real paper order service so the engine
    # journal test starts from ledger rows no test invented.
    settings = Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
        paper_starting_cash_usd=Decimal("10000"),
        max_order_usd=Decimal("1000"),
        max_position_usd=Decimal("2500"),
        max_open_positions=10,
        max_daily_loss_usd=Decimal("500"),
        min_liquidity_usd=Decimal("10000"),
        paper_mark_max_order_usd=Decimal("250"),
    )
    dex = PaperDexSource(Decimal("1.00"))
    with session_factory() as session:
        buy_status = await _place(
            session,
            PaperOrderRequest(
                chain=CHAIN,
                token_address=TOKEN,
                side="buy",
                amount_usd=Decimal("100"),
                idempotency_key="prot-buy-1",
            ),
            dex,
        )
        assert buy_status == "filled"

    # Drop the mark to 0.85 and trip the armed stop at 0.90.
    dex.price = Decimal("0.85")
    add_arm(session_factory)
    dex2 = PaperDexSource(Decimal("0.85"))
    router = QuoteRouter(providers=[FakeJupiterFake(price=Decimal("0.85"))], dexscreener=dex2)  # type: ignore[arg-type]
    executor = PaperOrderExecutor(session_factory, settings, dex2, router)

    results = await evaluate_all_arms(session_factory, dex2, executor, now=NOW)
    triggered = [r for r in results if r.action == "trigger"]
    assert len(triggered) == 1
    assert triggered[0].order_id is not None

    with session_factory() as session:
        rows = list(session.scalars(select(LedgerEntryRow)).all())
        groups: dict[str, Decimal] = {}
        for row in rows:
            groups[row.group_id] = groups.get(row.group_id, Decimal(0)) + row.usd_value
        assert all(total == 0 for total in groups.values())
        assert len(groups) == 3  # genesis + buy + arm sell
        sold = session.scalar(select(LedgerEntryRow).where(LedgerEntryRow.kind == "sell"))
        assert sold is not None
        from ezchain_api.models import PaperFillRow

        fill = session.scalar(select(PaperFillRow).where(PaperFillRow.side == "sell"))
        assert fill is not None
        assert fill.order_id == triggered[0].order_id


class RejectingExecutor:
    """A risk gate saying no: no order id comes back, nothing journals."""

    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        raise RuntimeError("order rejected by the risk gate")


def _trail(session_factory: sessionmaker[Session]) -> list[ProtectionEventRow]:
    with session_factory() as session:
        return list(
            session.scalars(select(ProtectionEventRow).order_by(ProtectionEventRow.seq)).all()
        )


async def test_rejected_order_keeps_the_arm_armed_and_the_event_trail(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(session_factory)
    source = FakeSource(make_snapshot(price="0.85", change_h1="0"))
    rejecting = RejectingExecutor()

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, rejecting, now=NOW
        )

    assert result.action == "hold"
    assert result.reason == "ORDER_FAILED"
    assert len(rejecting.requests) == 1
    with session_factory() as session:
        arm = session.get(ProtectionArmRow, "arm-1")
        assert arm is not None
        assert arm.status == "armed"
        assert arm.version == 1
    rejected = _trail(session_factory)
    assert [(event.action, event.reason) for event in rejected] == [("rejected", "ORDER_FAILED")]
    assert rejected[0].seq == 1
    assert rejected[0].mark == Decimal("0.85")
    assert rejected[0].order_id is None
    assert rejected[0].rule_snapshot == {"price_usd": "0.90"}
    assert rejected[0].observed_at.replace(tzinfo=UTC) == NOW

    # A later successful tick triggers; the rejected attempt is still on record.
    with session_factory() as session:
        second = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, FakeExecutor(), now=NOW
        )
    assert second.action == "trigger"
    trail = _trail(session_factory)
    assert [(event.action, event.reason) for event in trail] == [
        ("rejected", "ORDER_FAILED"),
        ("triggered", "STOP_LOSS"),
    ]
    assert [event.seq for event in trail] == [1, 2]
    assert trail[1].order_id == "ord-prot-1"
    assert trail[1].mark == Decimal("0.85")


async def test_repeated_hold_is_audited_once_per_reason(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(session_factory)
    source = FakeSource(make_snapshot(price="0.95", change_h1="0"))

    for _ in range(3):
        with session_factory() as session:
            result = await evaluate_arm_row(
                session, session.get(ProtectionArmRow, "arm-1"), source, FakeExecutor(), now=NOW
            )
            assert result.reason == "ARMED"

    trail = _trail(session_factory)
    # One observation of the same transition, not one row per tick.
    assert [(event.action, event.reason) for event in trail] == [("held", "ARMED")]
    assert trail[0].mark == Decimal("0.95")


async def test_a_hold_at_a_new_mark_appends_with_the_newer_observation(
    session_factory: sessionmaker[Session],
) -> None:
    """Dedupe keeps a stale mark only if the observation itself did not move."""
    add_position(session_factory)
    add_arm(session_factory)
    later = NOW + timedelta(minutes=1)

    with session_factory() as session:
        await evaluate_arm_row(
            session,
            session.get(ProtectionArmRow, "arm-1"),
            FakeSource(make_snapshot(price="0.95", change_h1="0")),
            FakeExecutor(),
            now=NOW,
        )
    with session_factory() as session:
        await evaluate_arm_row(
            session,
            session.get(ProtectionArmRow, "arm-1"),
            FakeSource(make_snapshot(price="0.94", change_h1="0")),
            FakeExecutor(),
            now=later,
        )

    trail = _trail(session_factory)
    assert [(event.action, event.reason) for event in trail] == [
        ("held", "ARMED"),
        ("held", "ARMED"),
    ]
    assert [event.mark for event in trail] == [Decimal("0.95"), Decimal("0.94")]
    assert trail[0].observed_at.replace(tzinfo=UTC) == NOW
    assert trail[1].observed_at.replace(tzinfo=UTC) == later


class TypedRejectingExecutor:
    """A risk gate rejection carries its reason code and the rejected order id."""

    def __init__(self, reason: str, order_id: str) -> None:
        self.reason = reason
        self.order_id = order_id

    async def place_order(self, request: OrderRequest) -> str:
        raise OrderRejected(self.reason, order_id=self.order_id)


async def test_a_typed_rejection_keeps_its_reason_code(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    add_arm(session_factory)
    source = FakeSource(make_snapshot(price="0.85", change_h1="0"))
    rejecting = TypedRejectingExecutor("MAX_POSITION_USD", "ord-rejected-1")

    with session_factory() as session:
        result = await evaluate_arm_row(
            session, session.get(ProtectionArmRow, "arm-1"), source, rejecting, now=NOW
        )

    assert result.action == "hold"
    assert result.reason == "MAX_POSITION_USD"
    rejected = _trail(session_factory)
    assert [(event.action, event.reason) for event in rejected] == [
        ("rejected", "MAX_POSITION_USD")
    ]
    assert rejected[0].order_id == "ord-rejected-1"
