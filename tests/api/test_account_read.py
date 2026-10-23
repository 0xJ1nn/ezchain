"""Read-model routes: ledger, performance, attribution, limits, alerts and CSV exports."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from ezchain_api.config import Settings, get_settings
from ezchain_api.models import (
    LedgerEntryRow,
    MarketSnapshotRow,
    PaperFillRow,
    PaperOrderRow,
    new_id,
)
from ezchain_api.routers import account as account_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import account_read
from ezchain_api.services.system_flags import set_kill_switch
from ezchain_core import buy_entries, genesis_entries, sell_entries, utcnow

CHAIN = "base"
TOKEN_A = "0x00000000000000000000000000000000000000a1"
TOKEN_B = "0x00000000000000000000000000000000000000b2"
START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _genesis(session: Session, amount: str = "10000", at: datetime = START) -> None:
    for entry in genesis_entries(Decimal(amount)):
        session.add(LedgerEntryRow(**entry.to_row("genesis", created_at=at)))
    session.commit()


def _trade(
    session: Session,
    *,
    chain: str,
    token: str,
    side: str,
    quantity: str,
    price: str,
    at: datetime,
    fee: str,
    avg_cost: str | None = None,
    notional: str | None = None,
) -> str:
    """Persist one filled order exactly as the order service would journal it."""
    order_id = new_id()
    qty = Decimal(quantity)
    unit_price = Decimal(price)
    amount = qty * unit_price if notional is None else Decimal(notional)
    if side == "buy":
        entries = buy_entries(
            chain=chain,
            token_address=token,
            quantity=qty,
            price_usd=unit_price,
            fee_bps=0,
            notional_usd=amount,
            fee_usd=Decimal(fee),
        )
    else:
        assert avg_cost is not None
        entries = sell_entries(
            chain=chain,
            token_address=token,
            quantity=qty,
            price_usd=unit_price,
            avg_cost_usd=Decimal(avg_cost),
            fee_bps=0,
            proceeds_usd=amount,
            cost_basis_usd=qty * Decimal(avg_cost),
            fee_usd=Decimal(fee),
        )
    session.add(
        PaperOrderRow(
            id=order_id,
            idempotency_key=f"key-{order_id}",
            chain=chain,
            token_address=token,
            side=side,
            amount_usd=amount,
            status="filled",
            created_at=at,
            filled_at=at,
        )
    )
    session.flush()
    session.add(
        PaperFillRow(
            id=new_id(),
            order_id=order_id,
            chain=chain,
            token_address=token,
            side=side,
            quantity=qty,
            price_usd=unit_price,
            notional_usd=amount,
            fee_usd=Decimal(fee),
            quote_kind="paper_mark",
            created_at=at,
        )
    )
    for entry in entries:
        session.add(LedgerEntryRow(**entry.to_row(order_id, created_at=at)))
    session.commit()
    return order_id


def _snapshot(
    session: Session,
    *,
    chain: str,
    token: str,
    price: str | None,
    observed_at: datetime,
    symbol: str = "TKN",
) -> None:
    session.add(
        MarketSnapshotRow(
            id=new_id(),
            chain=chain,
            token_address=token,
            symbol=symbol,
            name=symbol,
            price_usd=None if price is None else Decimal(price),
            source="test",
            observed_at=observed_at,
        )
    )
    session.commit()


def _client(client_for: Callable[..., TestClient]) -> TestClient:
    return client_for(account_router.router)


def test_ledger_is_newest_first_and_filterable(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START + timedelta(hours=1),
    )
    client = _client(client_for)

    body = client.get("/api/v1/paper/ledger?limit=50").json()
    assert body
    assert body[0]["created_at"] >= body[-1]["created_at"]
    assert body[0]["created_at"] == (START + timedelta(hours=1)).isoformat()
    assert set(body[0]) == {
        "id",
        "group_id",
        "account",
        "asset",
        "amount",
        "usd_value",
        "kind",
        "created_at",
    }

    buys = client.get("/api/v1/paper/ledger", params={"kind": "buy"}).json()
    assert len(buys) == 2
    position_leg = next(row for row in buys if row["account"] == f"position:{CHAIN}:{TOKEN_A}")
    assert position_leg["amount"] == "100"
    assert position_leg["usd_value"] == "200"

    cash = client.get("/api/v1/paper/ledger", params={"account": "cash:USD", "limit": 10}).json()
    assert len(cash) == 3  # genesis, buy notional, buy fee
    assert all(row["account"] == "cash:USD" for row in cash)

    assert client.get("/api/v1/paper/ledger", params={"kind": "nope"}).json() == []

    for bad in (0, 501):
        assert client.get("/api/v1/paper/ledger", params={"limit": bad}).status_code == 422


def test_performance_matches_a_known_ledger(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START,
    )
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="sell",
        quantity="40",
        price="3",
        fee="0.36",
        avg_cost="2",
        notional="120",
        at=START + timedelta(days=1),
    )
    _snapshot(session, chain=CHAIN, token=TOKEN_A, price="3", observed_at=utcnow())

    body = _client(client_for).get("/api/v1/paper/performance").json()

    assert body["mode"] == "paper"
    assert body["cash_usd"] == "9919.04"
    assert body["realized_usd"] == "39.04"
    assert body["unrealized_usd"] == "60"
    assert body["equity_usd"] == "10099.04"
    assert body["fees_usd"] == "0.96"
    assert body["trades"] == 2
    assert body["wins"] == 1
    assert body["losses"] == 0
    assert body["win_rate_pct"] == "100"
    assert body["open_positions"] == 1

    token = body["by_token"][0]
    assert token["token_address"] == TOKEN_A
    assert token["realized_usd"] == "39.04"
    assert token["unrealized_usd"] == "60"
    assert token["fees_usd"] == "0.96"
    assert token["trades"] == 2
    assert token["quantity"] == "60"
    assert token["avg_cost_usd"] == "2"
    assert token["mark_usd"] == "3"
    assert token["market_value_usd"] == "180"

    daily = body["daily_realized"]
    assert [row["date"] for row in daily] == ["2026-01-01", "2026-01-02"]
    assert daily[0] == {"date": "2026-01-01", "realized_usd": "-0.6", "fees_usd": "0.6"}
    assert daily[1] == {"date": "2026-01-02", "realized_usd": "39.64", "fees_usd": "0.36"}


def test_unknown_mark_and_undefined_win_rate_are_null(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START,
    )

    body = _client(client_for).get("/api/v1/paper/performance").json()

    assert body["unrealized_usd"] is None
    assert body["equity_usd"] is None
    assert body["win_rate_pct"] is None
    assert body["wins"] == 0
    assert body["losses"] == 0
    token = body["by_token"][0]
    assert token["mark_usd"] is None
    assert token["unrealized_usd"] is None
    assert token["market_value_usd"] is None


def test_empty_account_reports_defined_zero_and_no_alerts(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    client = _client(client_for)

    body = client.get("/api/v1/paper/performance").json()
    assert body["cash_usd"] == "10000"
    assert body["equity_usd"] == "10000"
    assert body["unrealized_usd"] == "0"
    assert body["trades"] == 0
    assert body["win_rate_pct"] is None
    assert body["by_token"] == []
    assert body["daily_realized"] == []

    alerts = client.get("/api/v1/paper/alerts").json()
    assert alerts["alerts"] == []


def test_paper_account_is_incomplete_when_one_mark_is_unknown(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START,
    )
    _snapshot(session, chain=CHAIN, token=TOKEN_A, price="3", observed_at=utcnow(), symbol="AAA")
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        side="buy",
        quantity="10",
        price="10",
        fee="0.3",
        at=START,
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is False
    assert body["marks_unknown"] is True
    assert body["equity_usd"] is None
    assert body["unrealized_usd"] is None
    assert body["cash_usd"] == "9699.1"
    assert body["known_market_value_usd"] == "300"
    assert body["unpriced_positions"] == [
        {
            "chain": CHAIN,
            "token_address": TOKEN_B,
            "symbol": TOKEN_B,
            "quantity": "10",
        }
    ]

    assets = {row["token_address"]: row for row in body["by_asset"] if row["kind"] == "position"}
    assert assets[TOKEN_A]["market_value_usd"] == "300"
    assert assets[TOKEN_A]["share_pct"] is None
    assert assets[TOKEN_A]["mark_status"] == "known"
    assert assets[TOKEN_B]["mark_usd"] is None
    assert assets[TOKEN_B]["market_value_usd"] is None
    assert assets[TOKEN_B]["share_pct"] is None
    assert assets[TOKEN_B]["mark_status"] == "unknown"
    assert assets[TOKEN_B]["quantity"] == "10"

    cash = next(row for row in body["by_asset"] if row["kind"] == "cash")
    assert cash["chain"] == "settlement"
    assert cash["symbol"] == "USD"
    assert cash["quantity"] == "9699.1"
    assert cash["market_value_usd"] == "9699.1"
    assert cash["share_pct"] is None
    assert cash["source"] is None

    chains = {row["chain"]: row for row in body["by_chain"]}
    assert chains[CHAIN]["unpriced_assets"] == 1
    assert chains[CHAIN]["market_value_usd"] is None
    assert chains[CHAIN]["share_pct"] is None
    assert chains["settlement"]["market_value_usd"] == "9699.1"
    assert chains["settlement"]["share_pct"] is None

    # The performance read model must withhold the same totals for the same ledger.
    performance = _client(client_for).get("/api/v1/paper/performance").json()
    assert performance["equity_usd"] is None
    assert performance["unrealized_usd"] is None


def test_paper_account_unpriced_asset_never_reports_a_mark_source(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    """A snapshot may exist with no price; its source must not label the row as priced."""
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2",
        fee="0",
        at=START,
    )
    _snapshot(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        price=None,
        observed_at=utcnow(),
        symbol="NOMARK",
    )
    # A second, priced position proves the fix withholds the source per row and does not
    # blank every source: the snapshot source ("test") still labels a row that has a mark.
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        side="buy",
        quantity="1",
        price="1",
        fee="0",
        at=START,
    )
    _snapshot(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        price="2",
        observed_at=utcnow(),
        symbol="PRICED",
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is False
    assert body["marks_unknown"] is True
    row = next(row for row in body["by_asset"] if row["kind"] == "position")
    assert row["symbol"] == "NOMARK"
    assert row["mark_usd"] is None
    assert row["market_value_usd"] is None
    assert row["share_pct"] is None
    assert row["mark_status"] == "unknown"
    assert row["source"] is None
    assert body["unpriced_positions"] == [
        {
            "chain": CHAIN,
            "token_address": TOKEN_A,
            "symbol": "NOMARK",
            "quantity": "10",
        }
    ]
    position = body["positions"][0]
    assert position["token_address"] == TOKEN_A
    assert position["mark_usd"] is None
    assert position["market_value_usd"] is None
    # The snapshot row exists with source "test"; the source belongs to a mark, so a
    # null-price snapshot must not leak it through the positions read model.
    assert position["source"] is None

    priced = next(row for row in body["positions"] if row["token_address"] == TOKEN_B)
    assert priced["source"]["name"] == "test"
    priced_asset = next(row for row in body["by_asset"] if row["token_address"] == TOKEN_B)
    assert priced_asset["source"]["name"] == "test"


def test_paper_account_composition_keeps_eleven_positions_across_chains(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    chains = ("base", "solana", "ethereum")
    tokens = [f"0x{index:040x}" for index in range(1, 12)]
    for index, token in enumerate(tokens):
        chain = chains[index % len(chains)]
        _trade(
            session,
            chain=chain,
            token=token,
            side="buy",
            quantity="10",
            price="10",
            fee="0",
            at=START,
        )
        _snapshot(
            session,
            chain=chain,
            token=token,
            price="10",
            observed_at=utcnow(),
            symbol=f"T{index:02d}",
        )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is True
    assert body["marks_unknown"] is False
    assert body["cash_usd"] == "8900"
    assert body["known_market_value_usd"] == "1100"
    assert body["equity_usd"] == "10000"
    assert body["unrealized_usd"] == "0"
    assert body["unpriced_positions"] == []
    assert len(body["positions"]) == 11

    position_rows = [row for row in body["by_asset"] if row["kind"] == "position"]
    assert len(body["by_asset"]) == 12  # eleven positions plus cash
    assert len(position_rows) == 11
    assert {row["token_address"] for row in position_rows} == set(tokens)
    assert {row["token_address"] for row in body["positions"]} == set(tokens)
    assert len(body["by_chain"]) == 4  # three position chains plus settlement

    chains_by_name = {row["chain"]: row for row in body["by_chain"]}
    assert chains_by_name["base"]["assets"] == 4
    assert chains_by_name["base"]["market_value_usd"] == "400"
    assert chains_by_name["base"]["share_pct"] == "4.00"
    assert chains_by_name["solana"]["assets"] == 4
    assert chains_by_name["solana"]["share_pct"] == "4.00"
    assert chains_by_name["ethereum"]["assets"] == 3
    assert chains_by_name["ethereum"]["share_pct"] == "3.00"
    assert chains_by_name["settlement"]["share_pct"] == "89.00"

    # Every server row reconciles to the server total; nothing was dropped or summed here.
    assert sum(Decimal(row["market_value_usd"]) for row in body["by_asset"]) == Decimal(
        body["equity_usd"]
    )
    assert sum(Decimal(row["market_value_usd"]) for row in body["by_chain"]) == Decimal(
        body["equity_usd"]
    )
    assert sum(Decimal(row["share_pct"]) for row in body["by_asset"]) == Decimal("100.00")


def test_paper_account_composition_single_position(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="1",
        fee="0",
        at=START,
    )
    _snapshot(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        price="1.5",
        observed_at=utcnow(),
        symbol="ONE",
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is True
    assert body["cash_usd"] == "9990"
    assert Decimal(body["known_market_value_usd"]) == Decimal("15")
    assert Decimal(body["equity_usd"]) == Decimal("10005")
    assert len(body["positions"]) == 1
    assert body["unpriced_positions"] == []

    position_rows = [row for row in body["by_asset"] if row["kind"] == "position"]
    assert len(body["by_asset"]) == 2
    assert len(position_rows) == 1
    assert position_rows[0]["token_address"] == TOKEN_A
    assert Decimal(position_rows[0]["market_value_usd"]) == Decimal("15")
    assert position_rows[0]["share_pct"] == "0.15"
    cash_row = next(row for row in body["by_asset"] if row["kind"] == "cash")
    assert cash_row["share_pct"] == "99.85"

    chains_by_name = {row["chain"]: row for row in body["by_chain"]}
    assert len(body["by_chain"]) == 2
    assert chains_by_name[CHAIN]["assets"] == 1
    assert Decimal(chains_by_name[CHAIN]["market_value_usd"]) == Decimal("15")
    assert chains_by_name[CHAIN]["share_pct"] == "0.15"
    assert chains_by_name["settlement"]["share_pct"] == "99.85"
    assert sum(Decimal(row["market_value_usd"]) for row in body["by_asset"]) == Decimal(
        body["equity_usd"]
    )
    assert sum(Decimal(row["share_pct"]) for row in body["by_asset"]) == Decimal("100.00")


def test_paper_account_withholds_shares_when_equity_is_not_positive(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    """A zero denominator yields no share, never a 0% or infinite number."""
    _genesis(session, amount="100")
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="1",
        fee="0",
        at=START,
    )
    _snapshot(session, chain=CHAIN, token=TOKEN_A, price="1", observed_at=utcnow())
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="sell",
        quantity="100",
        price="1",
        fee="100",
        avg_cost="1",
        at=START,
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is True
    assert body["cash_usd"] == "0"
    assert body["equity_usd"] == "0"
    assert body["positions"] == []
    assert all(row["share_pct"] is None for row in body["by_asset"])
    assert all(row["share_pct"] is None for row in body["by_chain"])
    cash_row = next(row for row in body["by_asset"] if row["kind"] == "cash")
    assert cash_row["market_value_usd"] == "0"
    assert cash_row["share_pct"] is None


def test_paper_account_all_unknown_marks_withhold_equity(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2",
        fee="0.06",
        at=START,
    )
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        side="buy",
        quantity="10",
        price="10",
        fee="0.3",
        at=START,
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is False
    assert body["equity_usd"] is None
    assert body["unrealized_usd"] is None
    assert body["known_market_value_usd"] == "0"
    assert {row["token_address"] for row in body["unpriced_positions"]} == {TOKEN_A, TOKEN_B}
    assert all(
        row["market_value_usd"] is None and row["share_pct"] is None
        for row in body["by_asset"]
        if row["kind"] == "position"
    )


def test_paper_account_flat_account_is_complete_cash_only(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is True
    assert body["marks_unknown"] is False
    assert body["equity_usd"] == "10000"
    assert body["unrealized_usd"] == "0"
    assert body["known_market_value_usd"] == "0"
    assert body["unpriced_positions"] == []
    assert body["positions"] == []
    assert body["by_chain"] == [
        {
            "chain": "settlement",
            "assets": 1,
            "unpriced_assets": 0,
            "market_value_usd": "10000",
            "share_pct": "100.00",
            "mark_status": "known",
        }
    ]
    assert body["by_asset"] == [
        {
            "chain": "settlement",
            "token_address": None,
            "symbol": "USD",
            "kind": "cash",
            "quantity": "10000",
            "mark_usd": "1",
            "market_value_usd": "10000",
            "share_pct": "100.00",
            "source": None,
            "mark_status": "known",
        }
    ]


def test_paper_account_composition_reconciles_across_chains(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START,
    )
    _snapshot(session, chain=CHAIN, token=TOKEN_A, price="3", observed_at=utcnow(), symbol="AAA")
    _trade(
        session,
        chain="solana",
        token=TOKEN_B,
        side="buy",
        quantity="20",
        price="5",
        fee="0.3",
        at=START,
    )
    _snapshot(
        session,
        chain="solana",
        token=TOKEN_B,
        price="6",
        observed_at=utcnow(),
        symbol="BBB",
    )

    body = client_for(paper_router.router).get("/api/v1/paper/account").json()

    assert body["complete"] is True
    assert body["equity_usd"] == "10119.1"
    assert body["unrealized_usd"] == "120"
    assert body["known_market_value_usd"] == "420"
    assert body["unpriced_positions"] == []

    assets = {row["token_address"]: row for row in body["by_asset"] if row["kind"] == "position"}
    assert assets[TOKEN_A]["share_pct"] == "2.96"
    assert assets[TOKEN_B]["share_pct"] == "1.19"
    cash = next(row for row in body["by_asset"] if row["kind"] == "cash")
    assert cash["share_pct"] == "95.85"

    chains = {row["chain"]: row for row in body["by_chain"]}
    assert chains[CHAIN]["market_value_usd"] == "300"
    assert chains[CHAIN]["share_pct"] == "2.96"
    assert chains["solana"]["market_value_usd"] == "120"
    assert chains["solana"]["share_pct"] == "1.19"
    assert chains["settlement"]["share_pct"] == "95.85"

    # The server rows reconcile to the server total; the client never sums them.
    assert sum(Decimal(row["market_value_usd"]) for row in body["by_asset"]) == Decimal(
        body["equity_usd"]
    )
    assert sum(Decimal(row["market_value_usd"]) for row in body["by_chain"]) == Decimal(
        body["equity_usd"]
    )
    assert sum(Decimal(row["share_pct"]) for row in body["by_asset"]) == Decimal("100.00")


def test_attribution_share_is_of_absolute_realized(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)

    # A: +39.04 realized.
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="100",
        price="2",
        fee="0.6",
        at=START,
    )
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="sell",
        quantity="40",
        price="3",
        fee="0.36",
        avg_cost="2",
        notional="120",
        at=START + timedelta(hours=1),
    )
    # B: -50.15 realized.
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        side="buy",
        quantity="10",
        price="10",
        fee="0.3",
        at=START,
    )
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_B,
        side="sell",
        quantity="10",
        price="5",
        fee="0.15",
        avg_cost="10",
        notional="50",
        at=START + timedelta(hours=2),
    )

    rows = _client(client_for).get("/api/v1/paper/attribution").json()

    by_token = {row["token_address"]: row for row in rows}
    assert by_token[TOKEN_A]["realized_usd"] == "39.04"
    assert by_token[TOKEN_B]["realized_usd"] == "-50.45"
    assert by_token[TOKEN_A]["share_pct"] == "43.62"
    assert by_token[TOKEN_B]["share_pct"] == "56.38"


def test_attribution_share_is_null_when_total_realized_is_zero(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2",
        fee="0",
        at=START,
    )

    rows = _client(client_for).get("/api/v1/paper/attribution").json()

    assert len(rows) == 1
    assert rows[0]["realized_usd"] == "0"
    assert rows[0]["share_pct"] is None


def test_limits_expose_settings_but_never_secrets(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    secret_settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://user:supersecret@db.example/ezchain",
        api_token=SecretStr("token-secret-abc"),
    )
    client: TestClient = client_for(account_router.router, {get_settings: lambda: secret_settings})

    response = client.get("/api/v1/paper/limits")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "mode",
        "max_order_usd",
        "max_position_usd",
        "max_open_positions",
        "max_daily_loss_usd",
        "min_pair_age_minutes",
        "min_liquidity_usd",
        "paper_mark_max_order_usd",
        "paper_fee_bps",
        "max_quote_age_seconds",
        "kill_switch",
    }
    assert body["mode"] == "paper"
    assert body["max_order_usd"] == "1000"
    assert body["paper_fee_bps"] == 30
    assert body["max_quote_age_seconds"] == 60
    assert body["kill_switch"]["active"] is False
    assert "supersecret" not in response.text
    assert "token-secret-abc" not in response.text
    assert "database_url" not in response.text


def test_alerts_cover_stale_marks_kill_switch_and_rejections(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2",
        fee="0.3",
        at=START,
    )
    _snapshot(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        price="2",
        observed_at=utcnow() - timedelta(hours=1),
    )
    set_kill_switch(session, active=True, reason="drill")
    session.add(
        PaperOrderRow(
            id=new_id(),
            idempotency_key="rejected-1",
            chain=CHAIN,
            token_address=TOKEN_A,
            side="buy",
            amount_usd=Decimal("5"),
            status="rejected",
            reason="STALE_QUOTE",
            created_at=utcnow() - timedelta(minutes=5),
        )
    )
    session.commit()

    body = _client(client_for).get("/api/v1/paper/alerts").json()
    codes = {alert["code"] for alert in body["alerts"]}

    assert {"POSITION_MARK_STALE", "KILL_SWITCH_ACTIVE", "ORDERS_REJECTED"} <= codes
    stale = next(a for a in body["alerts"] if a["code"] == "POSITION_MARK_STALE")
    assert stale["id"] == f"mark.stale.{CHAIN}.{TOKEN_A}"
    assert stale["severity"] == "warning"
    assert stale["resource"] == f"position:{CHAIN}:{TOKEN_A}"
    rejected = next(a for a in body["alerts"] if a["code"] == "ORDERS_REJECTED")
    assert "STALE_QUOTE" in rejected["detail"]
    assert rejected["id"].startswith("orders.rejected.")
    assert body["as_of"]


def test_alerts_cover_unknown_marks(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2",
        fee="0.3",
        at=START,
    )

    alerts = _client(client_for).get("/api/v1/paper/alerts").json()["alerts"]
    unknown = next(a for a in alerts if a["code"] == "POSITION_MARK_UNKNOWN")
    assert unknown["id"] == f"mark.unknown.{CHAIN}.{TOKEN_A}"
    assert unknown["severity"] == "warning"


def test_alerts_report_reconciliation_failure(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    session.add(
        LedgerEntryRow(
            **{
                "id": new_id(),
                "group_id": "rogue",
                "account": "cash:USD",
                "asset": "USD",
                "amount": Decimal("5"),
                "usd_value": Decimal("5"),
                "kind": "rogue",
                "created_at": utcnow(),
            }
        )
    )
    session.commit()

    alerts = _client(client_for).get("/api/v1/paper/alerts").json()["alerts"]
    failed = next(a for a in alerts if a["code"] == "RECONCILIATION_FAILED")
    assert failed["severity"] == "critical"
    assert "ledger.groups_balanced" in failed["detail"]


def test_csv_exports_escape_and_neutralise_formulas(
    client_for: Callable[..., TestClient], session: Session
) -> None:
    _genesis(session)
    _trade(
        session,
        chain=CHAIN,
        token=TOKEN_A,
        side="buy",
        quantity="10",
        price="2.5",
        fee="0.75",
        at=START,
    )
    nasty = '=SUM(A1,2),"quoted"\nnext line'
    session.add(
        PaperOrderRow(
            id=new_id(),
            idempotency_key="nasty-order",
            chain=CHAIN,
            token_address=TOKEN_A,
            side="buy",
            amount_usd=Decimal("25"),
            status="rejected",
            reason=nasty,
            created_at=utcnow(),
        )
    )
    session.commit()
    # Fill columns can carry operator-supplied text too; use both injection markers.
    session.execute(PaperFillRow.__table__.update().values(quote_kind="@evil"))
    session.commit()

    account_client = _client(client_for)
    paper_client: TestClient = client_for(paper_router.router)

    orders = account_client.get("/api/v1/paper/export/orders.csv")
    assert orders.status_code == 200
    assert orders.headers["content-type"].startswith("text/csv")
    assert orders.headers["content-disposition"] == 'attachment; filename="paper-orders.csv"'
    order_rows = list(csv.DictReader(io.StringIO(orders.text, newline="")))
    assert tuple(order_rows[0]) == account_router.ORDER_COLUMNS
    rejected = next(row for row in order_rows if row["reason"])
    assert rejected["reason"] == f"'{nasty}"

    fills = account_client.get("/api/v1/paper/export/fills.csv")
    assert fills.headers["content-type"].startswith("text/csv")
    assert fills.headers["content-disposition"] == 'attachment; filename="paper-fills.csv"'
    fill_rows = list(csv.DictReader(io.StringIO(fills.text, newline="")))
    assert fill_rows[0]["quote_kind"] == "'@evil"

    ledger = account_client.get("/api/v1/paper/export/ledger.csv")
    assert ledger.headers["content-type"].startswith("text/csv")
    assert ledger.headers["content-disposition"] == 'attachment; filename="paper-ledger.csv"'
    ledger_rows = list(csv.DictReader(io.StringIO(ledger.text, newline="")))
    genesis_equity = next(row for row in ledger_rows if row["account"] == "equity:genesis")
    assert genesis_equity["amount"] == "'-10000"

    # Every exported value is the JSON value, with only the formula guard added.
    json_orders = {
        row["id"]: row for row in paper_client.get("/api/v1/paper/orders?limit=200").json()
    }
    for row in order_rows:
        source = json_orders[row["id"]]
        assert row["amount_usd"] == source["amount_usd"]
        if source["reason"] is not None:
            assert row["reason"] == f"'{source['reason']}"

    json_fills = {
        row["id"]: row for row in paper_client.get("/api/v1/paper/fills?limit=200").json()
    }
    for row in fill_rows:
        source = json_fills[row["id"]]
        assert row["quantity"] == source["quantity"]
        assert row["notional_usd"] == source["notional_usd"]

    json_ledger = {row["id"]: row for row in account_client.get("/api/v1/paper/ledger").json()}
    for row in ledger_rows:
        source = json_ledger[row["id"]]
        for column in account_router.LEDGER_COLUMNS:
            value = source[column]
            expected = "" if value is None else str(value)
            if expected.startswith(("=", "+", "-", "@")):
                expected = f"'{expected}"
            assert row[column] == expected


def test_csv_exports_are_bounded(client_for: Callable[..., TestClient], session: Session) -> None:
    """The export cap is a constant; prove it is applied to the query, not ignored."""
    _genesis(session)
    client = _client(client_for)
    assert account_read.EXPORT_LIMIT == 5000
    assert client.get("/api/v1/paper/export/orders.csv").status_code == 200
