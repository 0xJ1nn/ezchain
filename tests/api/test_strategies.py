"""Strategy timestamps are normalized to UTC at the API boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_api.deps import get_dexscreener
from ezchain_api.models import StrategyRow, StrategyRunRow
from ezchain_api.routers import strategies
from ezchain_api.routers.strategies import _dt
from ezchain_core.types import PairInfo, SourceRef, TokenSnapshot


class FakeDex:
    """A fresh, liquid pair so a manual cycle reaches its decision, not a data gate."""

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        observed = datetime.now(UTC)
        return TokenSnapshot(
            chain=chain,
            address=address,
            symbol="TEST",
            name="Test",
            price_usd=Decimal("2"),
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
                    Decimal("2"),
                    Decimal("500000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
            source=SourceRef("dexscreener", None, observed),
            pair_created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )


def test_dt_converts_an_offset_timestamp_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 10, 22, 17, 30, tzinfo=offset)

    assert _dt(local) == "2026-10-22T12:00:00+00:00"
    # A naive database value is defined as UTC, never relabelled as local time.
    assert _dt(datetime(2026, 10, 22, 12, 0)) == "2026-10-22T12:00:00+00:00"


def test_strategy_response_labels_timestamps_as_utc(client_for, session: Session) -> None:
    session.add(
        StrategyRow(
            id="strategy-utc",
            name="utc",
            chain="solana",
            token_address="So11111111111111111111111111111111111111112",
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": 10, "interval_minutes": 60, "max_position_usd": 100},
            status="active",
            created_at=datetime(2026, 10, 22, 12, 0),
            last_run_at=datetime(2026, 10, 22, 11, 55),
        )
    )
    session.commit()
    client: TestClient = client_for(strategies.router)

    response = client.get("/api/v1/strategies/strategy-utc")

    assert response.status_code == 200
    body = response.json()
    assert body["created_at"] == "2026-10-22T12:00:00+00:00"
    assert body["last_run_at"] == "2026-10-22T11:55:00+00:00"


def test_run_now_on_a_paused_strategy_executes_one_manual_cycle(
    client_for, session: Session
) -> None:
    """Pause stops the scheduler, not an explicit owner request.

    The oldest possible manual run is one where ``last_run_at`` is now: without the
    manual override both the paused gate and the interval gate would hold. The cycle
    must reach the decision, and the strategy must stay paused afterwards.
    """
    now = datetime.now(UTC)
    session.add(
        StrategyRow(
            id="strategy-paused",
            name="paused",
            chain="blast",
            token_address="0x0000000000000000000000000000000000000abc",
            token_symbol="TEST",
            kind="dca",
            params={"order_usd": 10, "interval_minutes": 60, "max_position_usd": 100},
            status="paused",
            created_at=now,
            last_run_at=now,
        )
    )
    session.commit()
    client: TestClient = client_for(strategies.router, {get_dexscreener: lambda: FakeDex()})

    response = client.post("/api/v1/strategies/strategy-paused/run")

    assert response.status_code == 200
    body = response.json()
    # A decision ran (the paper executor is unwired in this edge test), so neither the
    # pause nor the interval refused it.
    assert body["reason"] == "ORDER_EXECUTOR_UNAVAILABLE", body
    assert body["id"]
    assert (
        session.scalar(
            select(func.count())
            .select_from(StrategyRunRow)
            .where(StrategyRunRow.strategy_id == "strategy-paused")
        )
        == 1
    )

    session.expire_all()
    row = session.get(StrategyRow, "strategy-paused")
    assert row is not None
    assert row.status == "paused"
