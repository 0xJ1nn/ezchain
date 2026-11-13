"""The order service records the placing principal on the order and its journal group."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select

from ezchain_api.actor import ACTOR_AGENT, ACTOR_STRATEGY, ACTOR_UNKNOWN, OrderActor
from ezchain_api.models import LedgerEntryRow
from ezchain_api.services import paper

from .conftest import make_request, make_router, make_settings, place


def _place_with(session, settings, dex, jupiter, request, actor):
    return asyncio.run(
        paper.place_order(session, request, settings, dex, make_router(dex, jupiter), actor=actor)
    )


def test_a_strategy_order_is_attributed_to_the_strategy(session, settings, dex, jupiter) -> None:
    """The engine is the only caller that sets ``strategy_id``, so it names the actor."""
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(key="actor-strategy-1", strategy_id="strategy-42"),
    )

    assert order.status == "filled"
    assert (order.actor_kind, order.actor_id) == (ACTOR_STRATEGY, "strategy-42")


def test_strategy_attribution_wins_over_a_supplied_actor(session, settings, dex, jupiter) -> None:
    """A caller cannot claim to be the agent that placed a strategy's order."""
    order = _place_with(
        session,
        settings,
        dex,
        jupiter,
        make_request(key="actor-strategy-2", strategy_id="strategy-7"),
        OrderActor(ACTOR_AGENT, "key-1"),
    )

    assert (order.actor_kind, order.actor_id) == (ACTOR_STRATEGY, "strategy-7")


def test_an_api_actor_is_recorded_on_a_manual_order(session, settings, dex, jupiter) -> None:
    order = _place_with(
        session,
        settings,
        dex,
        jupiter,
        make_request(key="actor-agent-3"),
        OrderActor(ACTOR_AGENT, "key-abc"),
    )

    assert (order.actor_kind, order.actor_id) == (ACTOR_AGENT, "key-abc")


def test_a_direct_service_call_without_an_actor_records_unknown(
    session, settings, dex, jupiter
) -> None:
    """There is no caller to name, so the order says unknown instead of guessing one."""
    order = place(session, settings, dex, jupiter, make_request(key="actor-none-1"))

    assert order.status == "filled"
    assert (order.actor_kind, order.actor_id) == (ACTOR_UNKNOWN, None)


def test_a_rejected_order_still_records_the_actor(session, dex, jupiter) -> None:
    """A refusal is a stored attempt, so it carries who attempted it."""
    poor = make_settings(paper_starting_cash_usd=Decimal("50"))
    order = _place_with(
        session,
        poor,
        dex,
        jupiter,
        make_request(key="actor-rejected-1", amount_usd="50"),
        OrderActor(ACTOR_AGENT, "key-abc"),
    )

    assert order.status == "rejected"
    assert order.reason == "INSUFFICIENT_CASH"
    assert (order.actor_kind, order.actor_id) == (ACTOR_AGENT, "key-abc")


def test_the_journal_group_of_a_strategy_order_names_the_strategy(
    session, settings, dex, jupiter
) -> None:
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(key="actor-journal-strategy-1", strategy_id="strategy-9"),
    )

    rows = list(session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)))
    assert rows
    assert {(row.actor_kind, row.actor_id) for row in rows} == {(ACTOR_STRATEGY, "strategy-9")}


def test_the_order_and_its_group_carry_the_same_actor(session, settings, dex, jupiter) -> None:
    """Read models cannot disagree: the order row and its journal legs say one thing."""
    order = place(session, settings, dex, jupiter, make_request(key="actor-consistency-1"))
    legs = list(session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.group_id == order.id)))

    assert legs
    assert {(row.actor_kind, row.actor_id) for row in legs} == {(order.actor_kind, order.actor_id)}
