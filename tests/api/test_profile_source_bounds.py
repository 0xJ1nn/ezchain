"""One profile bounds an order from every source at the one order gate (GH-146).

Manual, automation and plugin orders all reach ``paper.place_order``; there is no second
path that can skip it. The profile's ceiling is therefore one bound for all three, whatever
configured size the source asked for. The plugin-side clamp of a strategy's own
``order_usd`` lives in the engine and is outside this field; what is proved here is the
authoritative bound at the gate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from tests.paper.conftest import (
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)

from ezchain_api.models import StrategyRow

SOURCES = ("manual", "automation", "plugin")


def _strategy(session: Session, source: str) -> str | None:
    if source == "manual":
        return None
    row = StrategyRow(
        name=f"profile-{source}",
        chain="solana",
        token_address="So11111111111111111111111111111111111111112",
        token_symbol="TEST",
        kind="momentum",
        # A plugin advertises its own size above the profile maximum.
        params={"order_usd": "500"} if source == "plugin" else {},
        status="active",
        risk_profile="low",  # maximum $50
        created_by_kind="human" if source == "automation" else "agent",
        created_by_id="owner",
    )
    session.add(row)
    session.commit()
    return row.id


@pytest.mark.parametrize("source", SOURCES)
def test_a_profile_bounds_oversize_intents_from_every_source(session, source: str) -> None:
    dex = FakeDexScreener(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter()
    settings = make_settings(paper_starting_cash_usd=Decimal("10000"), trade_risk_profile="low")
    strategy_id = _strategy(session, source)

    over = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="60", key=f"profile-bound-{source}", strategy_id=strategy_id),
    )
    within = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="40", key=f"profile-ok-{source}", strategy_id=strategy_id),
    )

    assert over.status == "rejected"
    assert over.reason == "SIZE_ABOVE_MAX"
    assert over.policy_json is not None
    assert over.policy_json["profile"] == "low"
    # An agent-created strategy is held for a person under the default automation tier;
    # either way the within-cap intent is not refused for size.
    assert within.reason != "SIZE_ABOVE_MAX"
