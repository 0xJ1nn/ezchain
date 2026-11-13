"""The manual paper-order route cannot claim strategy attribution."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_api.deps import get_dexscreener, get_quote_router
from ezchain_api.models import PaperFillRow, PaperOrderRow
from ezchain_api.routers import paper as paper_router
from ezchain_api.services.system_flags import set_kill_switch
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot

# A chain with no keyless executable quote: this test is about attribution, not venue.
CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class FakeDex:
    def __init__(self, price: Decimal | None = Decimal("2")) -> None:
        self.price = price

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
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
                    self.price or Decimal(0),
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def test_manual_order_with_strategy_id_is_refused_and_names_it(
    client_for, session: Session
) -> None:
    """A hand-written request cannot claim strategy attribution.

    The field is refused rather than dropped, so the caller learns it was ignored: the
    422 loc carries ``strategy_id``. A refusal happens before the quote or any journal
    write, so no order and no fill row appears.
    """
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )

    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "25",
            "idempotency_key": "manual-attribution",
            "strategy_id": "strategy-1",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [error["loc"] for error in detail] == [["body", "strategy_id"]]
    assert detail[0]["type"] == "extra_forbidden"
    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 0
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def _paper_client(client_for, dex: FakeDex) -> TestClient:
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    return client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )


def _payload(key: str) -> dict[str, str]:
    return {
        "chain": CHAIN,
        "token_address": TOKEN,
        "side": "buy",
        "amount_usd": "25",
        "idempotency_key": key,
    }


def test_an_armed_kill_switch_refuses_a_route_buy(client_for, session: Session) -> None:
    """The manual route carries no strategy, so the global level is its guard."""
    set_kill_switch(session, active=True, reason="incident drill")
    client = _paper_client(client_for, FakeDex())

    response = client.post("/api/v1/paper/orders", json=_payload("armed-route-buy"))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "KILL_SWITCH_ARMED"
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 0


def test_http_idempotency_is_201_then_200_then_409(client_for, session: Session) -> None:
    client = _paper_client(client_for, FakeDex())
    payload = _payload("http-idem-replay")

    first = client.post("/api/v1/paper/orders", json=payload)
    assert first.status_code == 201
    assert first.json()["status"] == "filled"

    replay = client.post("/api/v1/paper/orders", json={**payload, "amount_usd": "25.00"})
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]

    conflict = client.post("/api/v1/paper/orders", json={**payload, "amount_usd": "26"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def test_http_idempotency_conflicts_on_a_changed_side_or_token(
    client_for, session: Session
) -> None:
    client = _paper_client(client_for, FakeDex())
    first = client.post("/api/v1/paper/orders", json=_payload("http-idem-dimensions"))
    assert first.status_code == 201

    for change in (
        {"side": "sell"},
        {"token_address": "0x0000000000000000000000000000000000000def"},
    ):
        response = client.post(
            "/api/v1/paper/orders", json={**_payload("http-idem-dimensions"), **change}
        )
        assert response.status_code == 409, change
        assert response.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    assert session.scalar(select(func.count()).select_from(PaperOrderRow)) == 1
    assert session.scalar(select(func.count()).select_from(PaperFillRow)) == 1


def _valid_payload() -> dict[str, str]:
    return {
        "chain": CHAIN,
        "token_address": TOKEN,
        "side": "buy",
        "amount_usd": "25",
        "idempotency_key": "validation-key-1",
    }


def test_float_overflow_amount_is_422_not_500(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["amount_usd"] = "1e400"

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_non_ascii_idempotency_key_is_422(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["idempotency_key"] = "idem-ünicode-123"

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_reduce_only_on_a_buy_is_422(client_for, session: Session) -> None:
    """The flag is a sell concept; a contradictory buy is refused at the edge."""
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["reduce_only"] = True

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_quantity_on_a_buy_is_422(client_for, session: Session) -> None:
    """Buys size in USD; a token quantity on a buy is a contradictory request."""
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    payload = _valid_payload()
    payload["quantity"] = "12.5"

    response = client.post("/api/v1/paper/orders", json=payload)

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None


def test_reduce_only_sell_without_a_mark_is_recorded_as_exit_no_route(
    client_for, session: Session
) -> None:
    """The API keeps the intent, the typed reason and the router evidence."""
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )

    opened = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "buy",
            "amount_usd": "25",
            "idempotency_key": "reduce-buy-1",
        },
    )
    assert opened.status_code == 201
    assert opened.json()["status"] == "filled"

    dex.price = None
    response = client.post(
        "/api/v1/paper/orders",
        json={
            "chain": CHAIN,
            "token_address": TOKEN,
            "side": "sell",
            "amount_usd": "25",
            "idempotency_key": "reduce-sell-1",
            "reduce_only": True,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "EXIT_NO_ROUTE"
    row = session.get(PaperOrderRow, body["id"])
    assert row is not None
    assert row.quote_json is not None
    assert row.quote_json["reduce_only"] is True
    assert row.quote_json["error"]["code"] == "NO_SELL_ROUTE"


def test_json_float_overflow_is_422_not_500(client_for, session: Session) -> None:
    dex = FakeDex()
    router = QuoteRouter(providers=[], dexscreener=dex)  # type: ignore[arg-type]
    client: TestClient = client_for(
        paper_router.router,
        {get_dexscreener: lambda: dex, get_quote_router: lambda: router},
    )
    body = (
        '{"chain": "'
        + CHAIN
        + '", "token_address": "'
        + TOKEN
        + '", "side": "buy", "amount_usd": 1e400, '
        + '"idempotency_key": "overflow-raw-123"}'
    )

    response = client.post(
        "/api/v1/paper/orders",
        content=body.encode(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert session.scalar(select(PaperOrderRow)) is None
