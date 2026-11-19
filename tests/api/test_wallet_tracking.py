"""Tracked-wallet API: identity, dedupe, and honest per-wallet metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_adapters.wallet_sources import ObservedSwap
from ezchain_api.routers import wallets_tracked
from ezchain_api.services.wallet_models import TrackedWalletRow, WalletSwapRow

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
ADDR_A = "PLACEHOLDER_SOL_ADDRESS_A"
ADDR_B = "PLACEHOLDER_EVM_ADDRESS_B"


class FakeSource:
    name = "fake"
    cost = "free"

    def __init__(self, swaps: list[ObservedSwap]) -> None:
        self.swaps = swaps

    async def fetch_swaps(self, **_: Any) -> list[ObservedSwap]:
        return self.swaps


def _swap(tx: str, side: str, tokens: str | None, usd: str | None, days: int = 1) -> ObservedSwap:
    return ObservedSwap(
        source="fake",
        source_tx=tx,
        chain="solana",
        side=side,
        token_address="PLACEHOLDER_TOKEN",
        token_symbol="TKN",
        amount_tokens=None if tokens is None else Decimal(tokens),
        amount_usd=None if usd is None else Decimal(usd),
        price_usd=None,
        observed_at=NOW - timedelta(days=days),
    )


def test_track_list_untrack_and_duplicate(client_for: Any, session: Session) -> None:
    client = client_for(wallets_tracked.router)
    created = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Alpha"},
    )
    assert created.status_code == 201, created.text
    wallet_id = created.json()["id"]
    assert created.json()["label"] == "Alpha"

    duplicate = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Renamed"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == wallet_id

    listed = client.get("/api/v1/wallets/tracked")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [wallet_id]

    assert client.delete(f"/api/v1/wallets/tracked/{wallet_id}").status_code == 204
    assert session.scalar(select(func.count()).select_from(TrackedWalletRow)) == 0


def test_refresh_is_idempotent_on_source_transaction(client_for: Any, session: Session) -> None:
    source = FakeSource(
        [_swap("tx-1", "buy", "10", "100", days=2), _swap("tx-2", "buy", "5", "60", days=1)]
    )
    client = client_for(
        wallets_tracked.router,
        {wallets_tracked.get_wallet_sources: lambda: [source]},
    )
    wallet_id = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Alpha"},
    ).json()["id"]

    first = client.post(f"/api/v1/wallets/tracked/{wallet_id}/refresh")
    second = client.post(f"/api/v1/wallets/tracked/{wallet_id}/refresh")
    assert first.status_code == second.status_code == 200
    assert first.json()["inserted"] == 2
    assert second.json()["inserted"] == 0
    assert session.scalar(select(func.count()).select_from(WalletSwapRow)) == 2

    activity = client.get(f"/api/v1/wallets/tracked/{wallet_id}/activity")
    assert [row["source_tx"] for row in activity.json()] == ["tx-2", "tx-1"]


def test_detail_reports_realized_pnl_and_win_rate(client_for: Any, session: Session) -> None:
    source = FakeSource(
        [
            _swap("buy-1", "buy", "10", "100", days=20),
            _swap("sell-1", "sell", "4", "60", days=10),
        ]
    )
    client = client_for(
        wallets_tracked.router,
        {wallets_tracked.get_wallet_sources: lambda: [source]},
    )
    wallet_id = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Alpha"},
    ).json()["id"]
    client.post(f"/api/v1/wallets/tracked/{wallet_id}/refresh")

    detail = client.get(f"/api/v1/wallets/tracked/{wallet_id}").json()
    # Cost basis 10/unit, sold 4 for 60 => realized 20, one winning trade.
    assert detail["realized_pnl_30d_usd"] == "20"
    assert detail["win_rate_30d"] == "1"
    assert detail["holdings"]["PLACEHOLDER_TOKEN"] == "6"
    assert detail["mode"] == "paper"


def test_unknown_stays_unknown_never_zero(client_for: Any, session: Session) -> None:
    source = FakeSource(
        [
            _swap("buy-1", "buy", "10", "100", days=20),
            _swap("sell-unknown", "sell", "4", None, days=10),
        ]
    )
    client = client_for(
        wallets_tracked.router,
        {wallets_tracked.get_wallet_sources: lambda: [source]},
    )
    wallet_id = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Alpha"},
    ).json()["id"]
    client.post(f"/api/v1/wallets/tracked/{wallet_id}/refresh")

    detail = client.get(f"/api/v1/wallets/tracked/{wallet_id}").json()
    # Token quantities are known, so the holding is known; only the USD-derived
    # performance is unknown when one swap has no USD value.
    assert detail["holdings"]["PLACEHOLDER_TOKEN"] == "6"
    assert detail["realized_pnl_30d_usd"] is None
    assert detail["win_rate_30d"] is None


def test_holding_is_unknown_when_a_swap_has_no_token_amount(
    client_for: Any, session: Session
) -> None:
    source = FakeSource([_swap("buy-unknown", "buy", None, "100")])
    client = client_for(
        wallets_tracked.router,
        {wallets_tracked.get_wallet_sources: lambda: [source]},
    )
    wallet_id = client.post(
        "/api/v1/wallets/track",
        json={"chain": "solana", "address": ADDR_A, "label": "Alpha"},
    ).json()["id"]
    client.post(f"/api/v1/wallets/tracked/{wallet_id}/refresh")
    detail = client.get(f"/api/v1/wallets/tracked/{wallet_id}").json()
    assert detail["holdings"]["PLACEHOLDER_TOKEN"] is None
    assert detail["realized_pnl_30d_usd"] is None


def test_unsupported_chain_is_refused(client_for: Any, session: Session) -> None:
    client = client_for(wallets_tracked.router)
    response = client.post(
        "/api/v1/wallets/track",
        json={"chain": "not-a-chain", "address": ADDR_B, "label": "Beta"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "CHAIN_NOT_FOUND"
