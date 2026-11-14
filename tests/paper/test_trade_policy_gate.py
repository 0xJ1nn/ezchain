"""The order gate resolves one profile: the strategy row bounds a per-order request.

Precedence, decided once: a strategy's pinned profile is the floor on risk for its orders,
a per-order request may only tighten it, and the deployment profile is the default when
neither names one. These tests pin the gate to the row, so a direct ``place_order`` call
cannot name a looser profile than the strategy allows.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.actor import ACTOR_HUMAN
from ezchain_api.models import PaperFillRow, StrategyRow
from ezchain_core import OrderRequest

from .conftest import CHAIN, make_request, make_settings, place

STRATEGY_TOKEN = "0x0000000000000000000000000000000000000abc"


def _strategy(
    session,
    strategy_id: str,
    *,
    risk_profile: str | None,
    created_by_kind: str | None = ACTOR_HUMAN,
) -> None:
    session.add(
        StrategyRow(
            id=strategy_id,
            name=strategy_id,
            chain=CHAIN,
            token_address=STRATEGY_TOKEN,
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": 40, "interval_minutes": 60},
            status="active",
            risk_profile=risk_profile,
            created_by_kind=created_by_kind,
            created_by_id="deployment" if created_by_kind is not None else None,
            created_by_owner="deployment" if created_by_kind is not None else None,
        )
    )
    session.commit()


def _order(
    *, risk_profile: str | None, amount_usd: str, key: str, strategy_id: str
) -> OrderRequest:
    return OrderRequest(
        chain=CHAIN,
        token_address=STRATEGY_TOKEN,
        side="buy",
        amount_usd=Decimal(amount_usd),
        idempotency_key=key,
        strategy_id=strategy_id,
        risk_profile=risk_profile,
    )


def test_a_strategy_pinned_to_low_refuses_a_looser_order_profile(
    session, settings, dex, jupiter
) -> None:
    """The pin is read from the strategy row, so the request cannot override it."""
    _strategy(session, "pinned-low", risk_profile="low")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        _order(risk_profile="high", amount_usd="40", key="pin-low-high", strategy_id="pinned-low"),
    )

    assert order.status == "rejected", order
    assert order.reason == "PROFILE_LOOSENS_STRATEGY"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_a_tighter_order_profile_inside_the_pin_still_fills(
    session, settings, dex, jupiter
) -> None:
    """Tightening is the only thing a per-order profile may do to a strategy's pin."""
    _strategy(session, "pinned-high", risk_profile="high")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        _order(risk_profile="low", amount_usd="40", key="pin-high-low", strategy_id="pinned-high"),
    )

    assert order.status == "filled", order
    assert order.reason is None


def test_an_order_profile_equal_to_the_pin_is_accepted(session, settings, dex, jupiter) -> None:
    """The engine sends the strategy's own profile; equality is never a refusal."""
    _strategy(session, "pinned-low-2", risk_profile="low")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        _order(risk_profile="low", amount_usd="40", key="pin-low-low", strategy_id="pinned-low-2"),
    )

    assert order.status == "filled", order


def test_an_order_with_no_profile_inherits_the_strategy_pin(
    session, settings, dex, jupiter
) -> None:
    """No name in the request means the strategy's profile, never the deployment default."""
    _strategy(session, "pinned-tight", risk_profile="low")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="60", key="pin-inherit", strategy_id="pinned-tight"),
    )

    assert order.status == "rejected", order
    assert order.reason == "SIZE_ABOVE_MAX"


def test_a_strategy_without_a_profile_follows_the_deployment_settings(
    session, dex, jupiter
) -> None:
    """Null on the row inherits the deployment default, exactly as before."""
    settings = make_settings(trade_risk_profile="low")
    _strategy(session, "no-pin", risk_profile=None)

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="60", key="no-pin-default", strategy_id="no-pin"),
    )

    assert order.status == "rejected", order
    assert order.reason == "SIZE_ABOVE_MAX"


def test_a_sell_is_never_refused_by_the_strategy_pin(session, settings, dex, jupiter) -> None:
    """The pin bounds entry only: an exit must stay executable."""
    _strategy(session, "pinned-exit", risk_profile="high")
    bought = place(
        session,
        settings,
        dex,
        jupiter,
        _order(risk_profile="high", amount_usd="40", key="pin-exit-buy", strategy_id="pinned-exit"),
    )
    assert bought.status == "filled", bought
    pinned = session.get(StrategyRow, "pinned-exit")
    assert pinned is not None
    pinned.risk_profile = "low"
    session.commit()

    sold = place(
        session,
        settings,
        dex,
        jupiter,
        OrderRequest(
            chain=CHAIN,
            token_address=STRATEGY_TOKEN,
            side="sell",
            amount_usd=Decimal("40"),
            idempotency_key="pin-exit-sell",
            strategy_id="pinned-exit",
            quantity=Decimal("0.02"),
            reduce_only=True,
            risk_profile="high",
        ),
    )

    assert sold.status == "filled", sold


def test_an_unknown_order_profile_is_still_a_typed_rejection(
    session, settings, dex, jupiter
) -> None:
    _strategy(session, "pinned-typo", risk_profile="low")

    order = place(
        session,
        settings,
        dex,
        jupiter,
        _order(risk_profile="turbo", amount_usd="40", key="pin-typo", strategy_id="pinned-typo"),
    )

    assert order.status == "rejected"
    assert order.reason == "PROFILE_UNKNOWN"


def test_an_order_naming_no_strategy_keeps_the_deployment_profile(session, dex, jupiter) -> None:
    """A manual order has no row to read, so the deployment default is the policy."""
    settings = make_settings(trade_risk_profile="low")

    order = place(session, settings, dex, jupiter, make_request(amount_usd="60", key="no-row"))

    assert order.status == "rejected", order
    assert order.reason == "SIZE_ABOVE_MAX"
