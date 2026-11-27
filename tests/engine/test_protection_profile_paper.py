"""Read-only paper seam: whole-profile decisions from one mark and a ledger replay.

The engine already evaluates protection on scheduler ticks through one market read
and one ledger replay. This seam reuses those inputs for the combined profile
kernel without adding an order, arm write, event or ledger write.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import ezchain_api.models  # noqa: F401
from ezchain_api.models import LedgerEntryRow, ProtectionArmRow, ProtectionEventRow
from ezchain_core.ledger import position_account
from ezchain_core.protection import ProtectionProfile, ProtectionValidationError
from ezchain_engine.protection import evaluate_profile_paper


class FakeSource:
    def __init__(self, *, price: str | None = "91.99", stale: bool = False) -> None:
        self.snapshot = make_snapshot(price=price, stale=stale)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.failure: Exception | None = None

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> Any:
        self.calls.append((chain, address, {"max_age_seconds": max_age_seconds}))
        if self.failure is not None:
            raise self.failure
        return self.snapshot


def make_profile(**thresholds: Any) -> ProtectionProfile:
    values: dict[str, Any] = {
        "stop_loss_pct": None,
        "trailing_pct": None,
        "take_profit_pct": None,
    }
    values.update(thresholds)
    return ProtectionProfile(
        id="profile-paper",
        name="guarded",
        chain=None,
        kind="manual",
        rules=(),
        created_at=None,
        updated_at=None,
        **values,
    )


def add_position(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.add(
            LedgerEntryRow(
                group_id="g1",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal(100),
                usd_value=Decimal(10000),
                kind="buy",
            )
        )
        session.commit()


async def test_paper_evaluation_decides_a_stop_without_any_writes(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource()
    with session_factory() as session:
        result = await evaluate_profile_paper(
            session,
            profile=make_profile(stop_loss_pct=Decimal(8)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=None,
            token_source=source,
        )
    assert result.profile_id == "profile-paper"
    assert result.decision.action == "exit_stop"
    assert result.decision.trigger_price_usd == Decimal(92)
    assert result.entry_usd == Decimal(100)
    assert result.quantity == Decimal(100)
    assert result.mark_usd == Decimal("91.99")
    assert result.peak_usd is None
    with session_factory() as session:
        assert session.scalars(select(ProtectionArmRow)).all() == []
        assert session.scalars(select(ProtectionEventRow)).all() == []
        assert len(session.scalars(select(LedgerEntryRow)).all()) == 1


async def test_paper_evaluation_reuses_the_one_mark_read_with_age(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource(price="100")
    with session_factory() as session:
        result = await evaluate_profile_paper(
            session,
            profile=make_profile(take_profit_pct=Decimal(20)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=Decimal(110),
            token_source=source,
            max_mark_age_seconds=30,
        )
    assert result.decision.action == "hold"
    assert result.decision.peak_usd == Decimal(110)
    assert source.calls == [(CHAIN, TOKEN, {"max_age_seconds": 30})]


async def test_paper_evaluation_ratchets_the_caller_peak(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource(price="120")
    with session_factory() as session:
        result = await evaluate_profile_paper(
            session,
            profile=make_profile(trailing_pct=Decimal(5)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=Decimal(105),
            token_source=source,
        )
    assert result.decision.action == "hold"
    assert result.decision.peak_usd == Decimal(120)
    assert result.decision.inputs["trailing_trigger_usd"] == str(Decimal("114.00"))


async def test_paper_evaluation_refuses_without_a_position(
    session_factory: sessionmaker[Session],
) -> None:
    source = FakeSource()
    with session_factory() as session, pytest.raises(ProtectionValidationError) as caught:
        await evaluate_profile_paper(
            session,
            profile=make_profile(stop_loss_pct=Decimal(8)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=None,
            token_source=source,
        )
    assert caught.value.code == "NO_POSITION"


async def test_paper_evaluation_refuses_an_unknown_mark(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource(price=None)
    with session_factory() as session, pytest.raises(ProtectionValidationError) as caught:
        await evaluate_profile_paper(
            session,
            profile=make_profile(stop_loss_pct=Decimal(8)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=None,
            token_source=source,
        )
    assert caught.value.code == "PROFILE_PRICE_INVALID"


async def test_paper_evaluation_refuses_a_stale_mark(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource(stale=True)
    with session_factory() as session, pytest.raises(ProtectionValidationError) as caught:
        await evaluate_profile_paper(
            session,
            profile=make_profile(stop_loss_pct=Decimal(8)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=None,
            token_source=source,
        )
    assert caught.value.code == "MARK_STALE"


async def test_market_failure_raises_and_writes_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    add_position(session_factory)
    source = FakeSource()
    source.failure = RuntimeError("venue down")
    with session_factory() as session, pytest.raises(RuntimeError, match="venue down"):
        await evaluate_profile_paper(
            session,
            profile=make_profile(stop_loss_pct=Decimal(8)),
            chain=CHAIN,
            token_address=TOKEN,
            peak_usd=None,
            token_source=source,
        )
    with session_factory() as session:
        assert session.scalars(select(ProtectionEventRow)).all() == []
        assert len(session.scalars(select(LedgerEntryRow)).all()) == 1
