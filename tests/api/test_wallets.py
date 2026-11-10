"""Wallet read routes and the wallet composition of the paper account."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import MarketSnapshotRow, PaperWalletRow
from ezchain_api.routers import account as account_router
from ezchain_api.routers import paper as paper_router
from ezchain_api.services import paper
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    def __init__(self, price: Decimal = Decimal("2")) -> None:
        self.price = price

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        observed = datetime.now(UTC)
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=self.price,
            liquidity_usd=Decimal("500000"),
            volume_24h_usd=Decimal("1000000"),
            price_change_m5=Decimal("0"),
            price_change_h1=Decimal("1"),
            price_change_h6=Decimal("0"),
            price_change_h24=Decimal("0"),
            pairs=(
                PairInfo(
                    "0xpair",
                    "uniswap",
                    "USDC",
                    self.price,
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def _client(client_for, dex: FakeDex) -> TestClient:
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    return client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )


def test_wallets_route_lists_the_settlement_wallet_first(client_for, session: Session) -> None:
    paper.ensure_genesis(session, Decimal("10000"))
    client = _client(client_for, FakeDex())

    response = client.get("/api/v1/paper/wallets")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["chain"] == "settlement"
    assert body[0]["asset"] == "USD"
    assert body[0]["balance_usd"] == "10000"
    assert body[0]["mode"] == "paper"
    chains = {row["chain"] for row in body}
    assert CHAIN in chains
    assert all(row["balance_usd"] == "0" for row in body if row["chain"] != "settlement")


def test_wallets_route_never_writes(client_for, session: Session) -> None:
    """A read reports the declared wallets; it never seeds, commits or re-creates one."""
    paper.ensure_genesis(session, Decimal("10000"))
    session.execute(  # a GET must leave this deletion alone
        PaperWalletRow.__table__.delete().where(PaperWalletRow.chain == CHAIN)
    )
    session.commit()
    before = session.scalar(select(func.count()).select_from(PaperWalletRow))
    client = _client(client_for, FakeDex())

    response = client.get("/api/v1/paper/wallets")

    assert response.status_code == 200
    assert CHAIN not in {row["chain"] for row in response.json()}
    assert session.scalar(select(func.count()).select_from(PaperWalletRow)) == before


def test_account_get_reports_a_deleted_wallet_without_writing(
    client_for, session: Session, engine
) -> None:
    """A GET must not heal the state its own read model is meant to expose.

    The wallet is deleted after a buy left cash attributed to it. Reading the account must
    perform no INSERT, leave the row deleted, and report the cash under ``by_wallet``'s
    unallocated row instead of quietly re-creating the wallet it is missing.
    """
    paper.ensure_genesis(session, Decimal("10000"))
    client = _client(client_for, FakeDex(price=Decimal("2")))
    assert (
        client.post(
            "/api/v1/paper/orders",
            json={
                "chain": CHAIN,
                "token_address": TOKEN,
                "side": "buy",
                "amount_usd": "100",
                "idempotency_key": "account-read-no-write",
            },
        ).status_code
        == 201
    )
    session.execute(PaperWalletRow.__table__.delete().where(PaperWalletRow.chain == CHAIN))
    session.commit()
    before = session.scalar(select(func.count()).select_from(PaperWalletRow))

    statements: list[str] = []

    def capture(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/v1/paper/account")
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200
    writes = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    assert not writes, f"a GET wrote: {writes}"
    assert session.scalar(select(func.count()).select_from(PaperWalletRow)) == before

    body = response.json()
    assert CHAIN not in {row["chain"] for row in body["wallets"]}
    wallet_bucket = next(row for row in body["wallets"] if row["chain"] == "unallocated")
    assert wallet_bucket["id"] is None
    by_wallet = {row["chain"]: row for row in body["by_wallet"]}
    assert Decimal(by_wallet["unallocated"]["settled_usd"]) == Decimal("-100.3")
    assert Decimal(body["cash_usd"]) == Decimal("9899.7")


def test_wallet_list_adds_unallocated_cash_so_the_rows_sum_to_cash(
    client_for, session: Session
) -> None:
    """The wallet list is a complete partition of cash even after a wallet row is deleted."""
    paper.ensure_genesis(session, Decimal("10000"))
    client = _client(client_for, FakeDex(price=Decimal("2")))
    assert (
        client.post(
            "/api/v1/paper/orders",
            json={
                "chain": CHAIN,
                "token_address": TOKEN,
                "side": "buy",
                "amount_usd": "100",
                "idempotency_key": "wallet-list-bucket",
            },
        ).status_code
        == 201
    )
    session.execute(PaperWalletRow.__table__.delete().where(PaperWalletRow.chain == CHAIN))
    session.commit()

    rows = client.get("/api/v1/paper/wallets").json()
    cash = Decimal(client.get("/api/v1/paper/account").json()["cash_usd"])

    assert sum((Decimal(row["balance_usd"]) for row in rows), Decimal(0)) == cash
    bucket = next(row for row in rows if row["chain"] == "unallocated")
    assert bucket["id"] is None
    assert bucket["asset"] == "USD"
    assert bucket["label"] == "Unallocated paper cash"
    assert Decimal(bucket["balance_usd"]) == Decimal("-100.3")


def test_ledger_entries_name_the_wallet_that_settled_each_cash_leg(
    client_for, session: Session
) -> None:
    """Cash legs carry their wallet id; position legs carry none, and neither is guessed."""
    paper.ensure_genesis(session, Decimal("10000"))
    router = APIRouter()
    router.include_router(paper_router.router)
    router.include_router(account_router.router)
    dex = FakeDex(price=Decimal("2"))
    quotes = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client = client_for(router, {get_dexscreener: lambda: dex, get_quote_router: lambda: quotes})
    order = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "100",
            "idempotency_key": "ledger-wallet-id",
        },
    )
    assert order.status_code == 201

    rows = client.get("/api/v1/paper/ledger", params={"limit": 200}).json()

    cash_rows = [
        row
        for row in rows
        if row["account"] == "cash:USD" and row["group_id"] == order.json()["id"]
    ]
    assert cash_rows
    assert all(row["wallet_id"] == f"paper-wallet:{CHAIN}" for row in cash_rows)
    genesis_cash = next(
        row for row in rows if row["account"] == "cash:USD" and row["group_id"] == "genesis"
    )
    assert genesis_cash["wallet_id"] == paper.SETTLEMENT_WALLET_ID
    position_rows = [row for row in rows if row["account"].startswith("position:")]
    assert position_rows
    assert all(row["wallet_id"] is None for row in position_rows)


def test_account_exposes_wallets_inside_the_equity_composition(
    client_for, session: Session
) -> None:
    paper.ensure_genesis(session, Decimal("10000"))
    client = _client(client_for, FakeDex())

    order = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "100",
            "idempotency_key": "wallet-composition",
        },
    )
    assert order.status_code == 201

    account = client.get("/api/v1/paper/account").json()

    wallets = {row["chain"]: row for row in account["wallets"]}
    assert wallets["settlement"]["balance_usd"] == "10000"
    assert Decimal(wallets[CHAIN]["balance_usd"]) == Decimal("-100.3")

    by_wallet = {row["chain"]: row for row in account["by_wallet"]}
    assert Decimal(by_wallet["settlement"]["settled_usd"]) == Decimal("10000")
    assert Decimal(by_wallet["settlement"]["market_value_usd"]) == Decimal("10000")
    assert Decimal(by_wallet[CHAIN]["settled_usd"]) == Decimal("-100.3")
    assert Decimal(by_wallet[CHAIN]["positions_usd"]) == Decimal("100")
    assert Decimal(by_wallet[CHAIN]["market_value_usd"]) == Decimal("-0.3")
    assert by_wallet[CHAIN]["mark_status"] == "known"

    # The wallet rows decompose the same equity the account reports, to the cent.
    assert sum(
        (Decimal(row["settled_usd"]) for row in account["by_wallet"]), Decimal(0)
    ) == Decimal(account["cash_usd"])
    assert sum(
        (Decimal(row["market_value_usd"]) for row in account["by_wallet"]), Decimal(0)
    ) == Decimal(account["equity_usd"])
    assert sum((Decimal(row["share_pct"]) for row in account["by_wallet"]), Decimal(0)) == Decimal(
        "100.00"
    )


def test_wallet_composition_withholds_an_unpriced_chain(client_for, session: Session) -> None:
    """An unknown mark withholds the chain's value; the settled cash stays visible."""
    paper.ensure_genesis(session, Decimal("10000"))
    client = _client(client_for, FakeDex(price=Decimal("2")))

    assert (
        client.post(
            "/api/v1/paper/orders",
            json={
                "chain": CHAIN,
                "token_address": TOKEN,
                "side": "buy",
                "amount_usd": "100",
                "idempotency_key": "wallet-unpriced-buy",
            },
        ).status_code
        == 201
    )
    # The stored snapshot keeps its mark, so remove it the way a failed observation would.
    snapshot = session.query(MarketSnapshotRow).one()
    snapshot.price_usd = None
    session.commit()

    account = client.get("/api/v1/paper/account").json()

    assert account["equity_usd"] is None
    by_wallet = {row["chain"]: row for row in account["by_wallet"]}
    assert by_wallet[CHAIN]["mark_status"] == "unknown"
    assert by_wallet[CHAIN]["positions_usd"] is None
    assert by_wallet[CHAIN]["market_value_usd"] is None
    # The exact Decimal sum keeps the ledger's money scale, so compare values not scales.
    assert Decimal(by_wallet[CHAIN]["settled_usd"]) == Decimal("-100.3")
    assert by_wallet["settlement"]["market_value_usd"] == "10000"
    assert by_wallet[CHAIN]["share_pct"] is None
