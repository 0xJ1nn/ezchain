"""The risk gate sizes against the same marks the account read reports (#PERF-RISK).

``place_order`` sums every held position's mark into the equity that the percent-of-equity
and per-token caps size against. Before this test that sum read the raw stored price, so a
mark past its freshness budget still priced the equity while ``account_snapshot`` reported
the account incomplete. The risk gate now reaches the same classifier, so a stale or
unknown mark on one held token withholds the equity instead of feeding it an old number,
and an exit on another token is still never blocked by an entry gate.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow
from ezchain_api.services import paper
from ezchain_api.services.market_snapshots import record_snapshot
from ezchain_core import utcnow
from ezchain_core.ledger import buy_entries, cash_account

from .conftest import (
    CHAIN,
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)

STARTING_CASH = Decimal("10000")
FRESH = "0x00000000000000000000000000000000000000f1"
OTHER = "0x00000000000000000000000000000000000000e2"
NEW = "0x00000000000000000000000000000000000000d3"


def _seed_genesis(session: Session) -> None:
    paper.ensure_genesis(session, STARTING_CASH)


def _seed_held(session: Session, token: str, quantity: Decimal, cost: Decimal, group: str) -> None:
    """A held position written straight to the journal, with no order path involved."""
    for entry in buy_entries(
        chain=CHAIN,
        token_address=token,
        quantity=quantity,
        price_usd=cost,
        fee_bps=0,
    ):
        row = entry.to_row(group)
        if row["account"] == cash_account():
            row["wallet_id"] = f"{paper.WALLET_PREFIX}{CHAIN}"
        session.add(LedgerEntryRow(**row))
    session.commit()


def _two_held_tokens(session: Session) -> paper.Replay:
    """A fresh-marked position and a second held token whose mark is aged past the budget."""
    _seed_genesis(session)
    _seed_held(session, FRESH, Decimal("10"), Decimal("2"), "seed-fresh")
    _seed_held(session, OTHER, Decimal("1000"), Decimal("1"), "seed-other")
    record_snapshot(session, make_snapshot(price=Decimal("2"), address=FRESH, symbol="FRESH"))
    record_snapshot(
        session,
        make_snapshot(
            price=Decimal("1000"),
            address=OTHER,
            symbol="OTHER",
            observed_at=utcnow() - timedelta(seconds=120),
        ),
    )
    session.commit()
    return paper._replay(paper._ledger_rows(session))


def test_the_risk_equity_withholds_a_stale_held_mark(session: Session) -> None:
    """The sum prices only the fresh mark: the stale one contributes no number at all."""
    replay = _two_held_tokens(session)
    snapshots = paper._snapshot_index(session)

    assert paper._risk_equity(replay, snapshots, max_age_seconds=60) is None

    # Cash after both seeded buys is 10000 - 20 - 1000; the fresh mark adds 10 * 2.
    record_snapshot(
        session,
        make_snapshot(price=Decimal("1000"), address=OTHER, symbol="OTHER", observed_at=utcnow()),
    )
    session.commit()
    replay = paper._replay(paper._ledger_rows(session))
    equity = paper._risk_equity(replay, paper._snapshot_index(session), max_age_seconds=60)
    assert equity == Decimal("8980") + Decimal("20") + Decimal("1000000")


def test_the_risk_equity_withholds_an_unknown_held_mark(session: Session) -> None:
    """A held token with no observation at all is unknown, never a zero-valued holding."""
    _seed_genesis(session)
    _seed_held(session, FRESH, Decimal("10"), Decimal("2"), "seed-fresh")
    _seed_held(session, OTHER, Decimal("10"), Decimal("1"), "seed-other")
    record_snapshot(session, make_snapshot(price=Decimal("2"), address=FRESH, symbol="FRESH"))
    session.commit()
    replay = paper._replay(paper._ledger_rows(session))

    assert paper._risk_equity(replay, paper._snapshot_index(session), max_age_seconds=60) is None


@pytest.mark.parametrize("held_mark", ["stale", "missing"])
def test_a_stale_held_mark_refuses_an_entry_that_would_size_from_it(
    session: Session,
    dex: FakeDexScreener,
    jupiter: FakeJupiter,
    held_mark: str,
) -> None:
    """An entry is sized against equity: an incomplete equity fails closed, never inflates it.

    The other token's mark is huge and old. Summing it raw makes the account look large
    enough to admit the buy; withholding it makes the account incomplete, so the risk gate
    refuses the entry with ``EQUITY_UNKNOWN`` instead of sizing from an old number.
    """
    settings = make_settings(trade_risk_profile="mid")
    _seed_genesis(session)
    _seed_held(session, FRESH, Decimal("10"), Decimal("2"), "seed-fresh")
    _seed_held(session, OTHER, Decimal("1000"), Decimal("1"), "seed-other")
    record_snapshot(session, make_snapshot(price=Decimal("2"), address=FRESH, symbol="FRESH"))
    if held_mark == "stale":
        record_snapshot(
            session,
            make_snapshot(
                price=Decimal("1000"),
                address=OTHER,
                symbol="OTHER",
                observed_at=utcnow() - timedelta(seconds=120),
            ),
        )
    session.commit()

    dex.set(make_snapshot(price=Decimal("2"), address=NEW, symbol="NEW"))
    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(amount_usd="100", key=f"held-{held_mark}-entry", token=NEW),
    )

    assert order.status == "rejected"
    assert order.reason == "EQUITY_UNKNOWN"

    account = paper.account_snapshot(session)
    assert account["complete"] is False
    assert account["equity_usd"] is None
    assert OTHER in [row["token_address"] for row in account["unpriced_positions"]]


def test_a_stale_held_mark_never_blocks_an_exit_on_another_token(
    session: Session,
    dex: FakeDexScreener,
    jupiter: FakeJupiter,
) -> None:
    """The incompleteness is information, not a blanket refusal: exits still settle."""
    settings = make_settings(trade_risk_profile="mid")
    _two_held_tokens(session)
    dex.set(make_snapshot(price=Decimal("2"), address=FRESH, symbol="FRESH"))

    order = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="20",
            quantity="10",
            reduce_only=True,
            key="stale-other-exit",
            token=FRESH,
        ),
    )

    assert order.status == "filled"
