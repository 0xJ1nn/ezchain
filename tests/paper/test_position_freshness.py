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

from ezchain_api.models import LedgerEntryRow, MarketSnapshotRow
from ezchain_api.services import paper
from ezchain_api.services.market_snapshots import record_snapshot
from ezchain_core import utcnow
from ezchain_core.ledger import buy_entries, cash_account

from .conftest import CHAIN, TOKEN, make_snapshot

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
