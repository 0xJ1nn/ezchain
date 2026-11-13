"""The position view carries the server's own mark freshness (#115).

A client must never re-derive staleness from a timestamp and a threshold of its own: the
API sends `mark_status` and `mark_age_seconds`, and these tests pin the rule at the
boundaries (inside the window, past it, no observation, an observation dated ahead of the
clock).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from ezchain_api.config import Settings
from ezchain_api.models import LedgerEntryRow, MarketSnapshotRow
from ezchain_api.services import paper
from ezchain_api.services.market_snapshots import record_snapshot
from ezchain_core import utcnow
from ezchain_core.ledger import buy_entries, cash_account

from .conftest import (
    CHAIN,
    TOKEN,
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_snapshot,
    place,
)

STARTING_CASH = Decimal("10000")


def _seed_position(session: Session) -> None:
    """A held position written straight to the journal, with no order path involved."""
    paper.ensure_genesis(session, STARTING_CASH)
    for entry in buy_entries(
        chain=CHAIN,
        token_address=TOKEN,
        quantity=Decimal("50"),
        price_usd=Decimal("2"),
        fee_bps=30,
    ):
        row = entry.to_row("seeded-position")
        if row["account"] == cash_account():
            row["wallet_id"] = f"{paper.WALLET_PREFIX}{CHAIN}"
        session.add(LedgerEntryRow(**row))
    session.commit()


def _observed(session: Session, seconds_ago: int) -> dict[str, object]:
    record_snapshot(
        session,
        make_snapshot(observed_at=utcnow() - timedelta(seconds=seconds_ago)),
    )
    return paper.positions(session)[0]


@pytest.mark.parametrize(
    ("seconds_ago", "status"),
    [(2, "fresh"), (61, "stale"), (91, "stale")],
)
def test_the_view_reports_the_servers_freshness_and_age(
    session: Session, seconds_ago: int, status: str
) -> None:
    _seed_position(session)
    row = _observed(session, seconds_ago)

    assert row["mark_status"] == status
    assert row["mark_age_seconds"] == pytest.approx(seconds_ago, abs=2)


def test_no_observation_reports_unknown_with_no_age(session: Session) -> None:
    _seed_position(session)
    row = paper.positions(session)[0]

    assert row["mark_usd"] is None
    assert row["mark_status"] == "unknown"
    # A number here would let a client print a fresh-looking mark that is not one.
    assert row["mark_age_seconds"] is None


def test_an_observation_ahead_within_the_skew_is_future_not_fresh(session: Session) -> None:
    """A modestly future-dated observation is tolerated clock skew (``future``)."""
    _seed_position(session)
    record_snapshot(session, make_snapshot())
    # Rewrite the stored timestamp into the shape a row written before the guard can have.
    session.execute(update(MarketSnapshotRow).values(observed_at=utcnow() + timedelta(seconds=2)))
    session.commit()
    row = paper.positions(session)[0]

    assert row["mark_status"] == "future"
    assert row["mark_age_seconds"] == 0


def test_an_observation_far_in_the_future_reports_unknown(session: Session) -> None:
    """A far-future observation cannot freeze a mark: it reports ``unknown``."""
    _seed_position(session)
    record_snapshot(session, make_snapshot())
    # Rewrite the stored timestamp into the shape a row written before the guard can have.
    session.execute(update(MarketSnapshotRow).values(observed_at=utcnow() + timedelta(seconds=30)))
    session.commit()
    row = paper.positions(session)[0]

    assert row["mark_status"] == "unknown"
    assert row["mark_age_seconds"] is None


def test_an_unknown_mark_never_prices_the_position_or_the_account(session: Session) -> None:
    """An unknown status fails closed: no money number is derived from its price.

    The read model's own rule (``unknown`` is a distinct state from priced) would be a
    lie if the row still carried a mark, a market value and an unrealized PnL; the
    account would then report an equity built from a frozen price.
    """
    _seed_position(session)
    record_snapshot(session, make_snapshot())
    # A legacy row frozen in the future, priced far above the real mark.
    session.execute(
        update(MarketSnapshotRow).values(
            observed_at=utcnow() + timedelta(days=365), price_usd=Decimal("9999")
        )
    )
    session.commit()

    row = paper.positions(session)[0]
    assert row["mark_status"] == "unknown"
    assert row["mark_usd"] is None
    assert row["market_value_usd"] is None
    assert row["unrealized_usd"] is None
    assert row["mark_age_seconds"] is None

    account = paper.account_snapshot(session)
    assert account["complete"] is False
    assert account["marks_unknown"] is True
    assert account["equity_usd"] is None
    assert account["unrealized_usd"] is None
    # The frozen 9999 mark contributes nothing, not even to the partial sum.
    assert Decimal(str(account["known_market_value_usd"])) == 0
    assert [position["token_address"] for position in account["unpriced_positions"]] == [TOKEN]

    asset = next(entry for entry in account["by_asset"] if entry["kind"] == "position")
    assert asset["mark_usd"] is None
    assert asset["market_value_usd"] is None
    assert asset["source"] is None
    assert asset["mark_status"] == "unknown"
    chain = next(entry for entry in account["by_chain"] if entry["chain"] == CHAIN)
    assert chain["market_value_usd"] is None
    assert chain["mark_status"] == "unknown"


def test_a_corrected_observation_replaces_a_frozen_far_future_mark(session: Session) -> None:
    """A legacy far-future row must not reject every later correct write.

    A row already frozen in the future is not evidence of a newer observation, so the
    write path may replace it; otherwise the bogus mark is permanent.
    """
    _seed_position(session)
    record_snapshot(session, make_snapshot(price=Decimal("2")))
    session.execute(
        update(MarketSnapshotRow).values(
            observed_at=utcnow() + timedelta(days=365), price_usd=Decimal("9999")
        )
    )
    session.commit()
    assert paper.positions(session)[0]["mark_status"] == "unknown"

    stored = record_snapshot(session, make_snapshot(price=Decimal("2"), observed_at=utcnow()))
    session.commit()

    assert stored is not None
    assert stored.price_usd == Decimal("2")
    row = paper.positions(session)[0]
    assert row["mark_status"] == "fresh"
    assert Decimal(str(row["mark_usd"])) == Decimal("2")


def test_an_order_priced_off_a_far_future_mark_is_rejected(
    session: Session, settings: Settings
) -> None:
    """A future-dated mark fails closed: it can never price a new entry."""
    future = utcnow() + timedelta(hours=1)
    dex = FakeDexScreener(make_snapshot(observed_at=future))
    order = place(
        session,
        settings,
        dex,
        FakeJupiter(),
        make_request(amount_usd="100", key="future-mark-order"),
    )

    assert order.status == "rejected"
    assert "MARK_TIMESTAMP_FUTURE" in (order.reason or "")
    codes = {reason["code"] for reason in (order.reasons or [])}
    assert "MARK_TIMESTAMP_FUTURE" in codes
