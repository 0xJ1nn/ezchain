"""Strategy timestamps are normalized to UTC at the API boundary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ezchain_api.models import StrategyRow
from ezchain_api.routers import strategies
from ezchain_api.routers.strategies import _dt


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
