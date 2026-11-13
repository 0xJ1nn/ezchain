"""Concurrency regression: an engine tick must not undo a guarded API edit.

The engine loads the arm, awaits a market read, then writes. An API writer can
commit inside that gap. The engine's write is a compare-and-swap on the version
it read, so it either wins against that exact row or reloads and retries; the
version never goes backwards and the API edit is never dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from helpers import CHAIN, TOKEN, make_snapshot
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.db import Base
from ezchain_api.models import LedgerEntryRow, ProtectionArmRow, ProtectionEventRow
from ezchain_api.services import protection as protection_service
from ezchain_core.ledger import position_account
from ezchain_core.types import OrderRequest, TokenSnapshot
from ezchain_engine.protection import evaluate_arm_row

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _maker(path: str) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed(maker: sessionmaker[Session]) -> str:
    with maker() as session:
        session.add(
            LedgerEntryRow(
                group_id="g1",
                account=position_account(CHAIN, TOKEN),
                asset=TOKEN,
                amount=Decimal("100"),
                usd_value=Decimal("100"),
                kind="buy",
            )
        )
        session.commit()
        arm = protection_service.create_arm(
            session,
            chain=CHAIN,
            token_address=TOKEN,
            token_symbol="TEST",
            kind="stop_loss",
            params={"price_usd": "0.90"},
        )
        return arm.id


class PatchingSource:
    """Two guarded API patches commit while the engine is waiting on the mark."""

    def __init__(self, maker: sessionmaker[Session], arm_id: str) -> None:
        self.maker = maker
        self.arm_id = arm_id

    async def get_token(
        self, chain: str, address: str, *, max_age_seconds: float | None = None
    ) -> TokenSnapshot:
        with self.maker() as session:
            protection_service.patch_arm(
                session, self.arm_id, {"price_usd": "0.89"}, expected_version=1
            )
        with self.maker() as session:
            protection_service.patch_arm(
                session, self.arm_id, {"price_usd": "0.88"}, expected_version=2
            )
        return make_snapshot(price="0.85", change_h1="0")


class FakeExecutor:
    def __init__(self) -> None:
        self.requests: list[OrderRequest] = []

    async def place_order(self, request: OrderRequest) -> str:
        self.requests.append(request)
        return "ord-prot-race"


async def test_engine_write_does_not_regress_a_concurrent_api_version(tmp_path) -> None:
    maker = _maker(str(tmp_path / "protection-engine-race.db"))
    arm_id = _seed(maker)
    source = PatchingSource(maker, arm_id)
    executor = FakeExecutor()

    with maker() as session:
        row = session.get(ProtectionArmRow, arm_id)
        assert row is not None
        assert row.version == 1
        result = await evaluate_arm_row(session, row, source, executor, now=NOW)

    assert result.action == "trigger"
    assert len(executor.requests) == 1
    with maker() as session:
        arm = session.get(ProtectionArmRow, arm_id)
        assert arm is not None
        # Two API writes and one engine write: each incremented the version it
        # read, so the counter is monotonic and the last API rule survives.
        assert arm.version == 4
        assert arm.status == "triggered"
        assert arm.params == {"price_usd": "0.88"}
        trail = list(
            session.scalars(
                select(ProtectionEventRow)
                .where(ProtectionEventRow.arm_id == arm_id)
                .order_by(ProtectionEventRow.seq)
            ).all()
        )
    assert [row.seq for row in trail] == [1, 2, 3, 4]
    assert [row.action for row in trail] == ["armed", "updated", "updated", "triggered"]
    assert trail[1].rule_snapshot == {
        "before": {"price_usd": "0.90"},
        "after": {"price_usd": "0.89"},
    }
    assert trail[2].rule_snapshot == {
        "before": {"price_usd": "0.89"},
        "after": {"price_usd": "0.88"},
    }
