"""Reduce-only exits: entry gates cannot trap a position, exit checks still fail closed.

A sell can only reduce risk, so a reduce-only intent (or a sell sized to the whole
position) must reach an executable quote even when the market feed has no mark, no
liquidity or no pair. The exit still needs a held position, a fresh quote, a route and a
quantity no larger than the position. A partial sell that is not flagged remains an
ordinary sell and keeps the entry gates.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.models import PaperFillRow
from ezchain_core import utcnow

from .conftest import (
    CHAIN,
    TOKEN,
    FakeJupiter,
    make_request,
    make_snapshot,
    place,
)

SOLANA = "solana"
MINT = "So11111111111111111111111111111111111111112"


def _open_solana_position(session, settings, dex, jupiter, key: str = "exit-open") -> None:
    dex.set(
        make_snapshot(price=Decimal("2"), liquidity=Decimal("500000"), chain=SOLANA, address=MINT)
    )
    opened = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="buy",
            amount_usd="100",
            key=f"{key}-buy",
            chain=SOLANA,
            token=MINT,
        ),
    )
    assert opened.status == "filled"


def test_entry_gate_does_not_block_a_reduce_only_exit(session, settings, dex, jupiter) -> None:
    """Liquidity sizes new risk; an exit with a fresh venue quote is still executable."""
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    # The market feed now cannot value the position, but Jupiter still quotes the exit.
    dex.set(make_snapshot(price=Decimal("2"), liquidity=None, chain=SOLANA, address=MINT))
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="50",
            key="exit-partial",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("25")
    assert fill.quote_kind == "jupiter"


def test_reduce_only_exit_cannot_exceed_the_position(session, settings, dex, jupiter) -> None:
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="1000",
            key="exit-oversized",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")
    assert fill.notional_usd == Decimal("100")
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 2


def test_reduce_only_with_an_unknown_mark_and_no_route_fails_closed(
    session, settings, dex, jupiter
) -> None:
    """A mark-only chain cannot price the exit, so the rejection is typed and recorded."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    opened = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(side="buy", amount_usd="25", key="unknown-mark-buy"),
    )
    assert opened.status == "filled"

    dex.set(make_snapshot(price=None, liquidity=Decimal("500000")))
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="25",
            key="unknown-mark-sell",
            reduce_only=True,
        ),
    )

    assert sell.status == "rejected"
    assert sell.reason == "EXIT_NO_ROUTE"
    assert sell.quote_json is not None
    assert sell.quote_json["reduce_only"] is True
    assert sell.quote_json["error"]["code"] == "NO_SELL_ROUTE"
    sells = session.scalar(
        select(func.count()).select_from(PaperFillRow).where(PaperFillRow.side == "sell")
    )
    assert sells == 0


def test_reduce_only_exits_on_a_venue_quote_when_the_market_has_no_mark(
    session, settings, dex, jupiter
) -> None:
    """DexScreener cannot mark the token; Jupiter can still sell it."""
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    dex.set(make_snapshot(price=None, liquidity=None, chain=SOLANA, address=MINT))
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="100",
            key="venue-mark-sell",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")
    assert fill.price_usd == Decimal("2")
    assert fill.quote_kind == "jupiter"


def test_a_sell_sized_to_the_whole_position_is_an_exit_without_the_flag(
    session, settings, dex, jupiter
) -> None:
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("1"), chain=SOLANA, address=MINT))

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="100",
            key="full-close-sell",
            chain=SOLANA,
            token=MINT,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")


def test_a_partial_sell_without_the_flag_is_still_gated(session, settings, dex, jupiter) -> None:
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)
    dex.set(make_snapshot(price=Decimal("2"), liquidity=None, chain=SOLANA, address=MINT))

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="50",
            key="normal-partial-sell",
            chain=SOLANA,
            token=MINT,
        ),
    )

    assert sell.status == "rejected"
    assert sell.reason == "LIQUIDITY_UNKNOWN"


def test_reduce_only_exit_still_rejects_a_stale_quote(session, settings, dex, jupiter) -> None:
    """The venue quote is the exit's mark, so its freshness is never waived."""
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    dex.set(make_snapshot(price=Decimal("2"), liquidity=None, chain=SOLANA, address=MINT))
    jupiter.source_observed_at = utcnow() - timedelta(seconds=120)
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="100",
            key="stale-exit",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    assert sell.status == "rejected"
    assert sell.reason is not None and "QUOTE_STALE" in sell.reason
    fills = session.scalar(
        select(func.count()).select_from(PaperFillRow).where(PaperFillRow.side == "sell")
    )
    assert fills == 0


def test_reduce_only_sell_without_a_position_is_rejected(session, settings, dex, jupiter) -> None:
    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="25",
            key="exit-no-position",
            chain=CHAIN,
            token=TOKEN,
            reduce_only=True,
        ),
    )

    assert sell.status == "rejected"
    assert sell.reason == "INSUFFICIENT_POSITION"


def test_a_reduce_only_buy_cannot_use_the_exit_path(session, settings, dex, jupiter) -> None:
    """The flag is a sell concept; a buy still needs an entry-ready snapshot."""
    dex.set(make_snapshot(price=Decimal("2"), liquidity=Decimal("500000")))
    buy = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="buy",
            amount_usd="100",
            key="flagged-buy",
            reduce_only=True,
        ),
    )

    assert buy.status == "filled"
    # A second, liquidity-unknown buy is still denied even with the flag set.
    dex.set(make_snapshot(price=Decimal("2"), liquidity=None))
    blocked = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="buy",
            amount_usd="100",
            key="flagged-buy-2",
            reduce_only=True,
        ),
    )
    assert blocked.status == "rejected"
    assert blocked.reason == "LIQUIDITY_UNKNOWN"


def test_the_flag_reaches_the_quote_as_a_quantity_first_sell(
    session, settings, dex, jupiter
) -> None:
    """With no mark the router is asked for the held quantity, never a division by None."""
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)
    dex.set(make_snapshot(price=None, liquidity=None, chain=SOLANA, address=MINT))

    place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="1",
            key="quantity-first",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    sell_call = [call for call in jupiter.calls if call[0] == "sell"][-1]
    assert sell_call[1] == Decimal("50")


def test_an_explicit_quantity_is_capped_at_the_position(session, settings, dex, jupiter) -> None:
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="50",
            key="quantity-capped",
            chain=SOLANA,
            token=MINT,
            quantity="500",
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    assert fill.quantity == Decimal("50")


def test_a_stale_market_mark_never_sizes_a_reduce_only_exit(
    session, settings, dex, jupiter
) -> None:
    """An explicit quantity keeps a partial exit exact when the mark is stale."""
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)
    dex.set(
        make_snapshot(price=Decimal("2"), liquidity=None, stale=True, chain=SOLANA, address=MINT)
    )

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="50",
            key="stale-mark-partial",
            chain=SOLANA,
            token=MINT,
            quantity="25",
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    fill = session.scalar(select(PaperFillRow).where(PaperFillRow.order_id == sell.id))
    assert fill is not None
    # The stale mark is never used to choose the exit size; the arm's quantity is.
    assert fill.quantity == Decimal("25")


def test_quote_evidence_records_the_explicit_flag(session, settings, dex, jupiter) -> None:
    jupiter.price = Decimal("2")
    _open_solana_position(session, settings, dex, jupiter)

    sell = place(
        session,
        settings,
        dex,
        jupiter,
        make_request(
            side="sell",
            amount_usd="100",
            key="evidence-flag",
            chain=SOLANA,
            token=MINT,
            reduce_only=True,
        ),
    )

    assert sell.status == "filled"
    assert sell.quote_json is not None
    assert sell.quote_json["reduce_only"] is True
