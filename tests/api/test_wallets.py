"""Wallet read routes and the wallet composition of the paper account."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import MarketSnapshotRow
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
    assert by_wallet[CHAIN]["settled_usd"] == "-100.3"
    assert by_wallet["settlement"]["market_value_usd"] == "10000"
    assert by_wallet[CHAIN]["share_pct"] is None
