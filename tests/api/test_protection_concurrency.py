"""Concurrency regression: two transactions append to one arm's event trail.

The per-arm sequence is read from ``MAX(seq)``. Without a lock, two writers see
the same maximum, one loses the unique constraint, and its whole transaction
rolls back, including the arm edit it was making.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.db import Base
from ezchain_api.models import (
    LedgerEntryRow,
    ProtectionArmRow,
    ProtectionEventRow,
)
from ezchain_api.services import protection as protection_service
from ezchain_core.ledger import position_account
from ezchain_core.types import utcnow

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"


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
        row = protection_service.create_arm(
            session,
            chain=CHAIN,
            token_address=TOKEN,
            token_symbol="TEST",
            kind="stop_loss",
            params={"price_usd": "90"},
        )
        return row.id


def test_two_sessions_keep_both_events_and_both_arm_edits(tmp_path) -> None:
    maker = _maker(str(tmp_path / "protection-events.db"))
    arm_id = _seed(maker)
    first_locked = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def append(price: str, first: bool) -> None:
        try:
            if not first:
                second_started.set()
            with maker() as session:
                protection_service.record_event(
                    session,
                    arm_id=arm_id,
                    action="held",
                    reason="RACE",
                    rule_snapshot={"price_usd": price},
                    mark=Decimal(price),
                    observed_at=utcnow(),
                    order_id=None,
                )
                session.execute(
                    update(ProtectionArmRow)
                    .where(ProtectionArmRow.id == arm_id)
                    .values(
                        params={"price_usd": price},
                        version=ProtectionArmRow.version + 1,
                        updated_at=utcnow(),
                    )
                )
                if first:
                    first_locked.set()
                    release.wait(timeout=10)
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - a thread failure is the test failing
            errors.append(exc)

    first = threading.Thread(target=append, args=("80", True))
    second = threading.Thread(target=append, args=("81", False))
    first.start()
    assert first_locked.wait(timeout=10)
    second.start()
    assert second_started.wait(timeout=10)
    # Let the second writer reach the per-arm lock before the first commits.
    time.sleep(0.2)
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not errors, errors
    with maker() as session:
        arm = session.get(ProtectionArmRow, arm_id)
        assert arm is not None
        # One create, two relative edits: no edit was lost to the loser's rollback.
        assert arm.version == 3
        assert arm.params == {"price_usd": "81"}
        trail = list(
            session.scalars(
                select(ProtectionEventRow)
                .where(ProtectionEventRow.arm_id == arm_id)
                .order_by(ProtectionEventRow.seq)
            ).all()
        )
    assert [row.seq for row in trail] == [1, 2, 3]
    assert [(row.action, row.reason) for row in trail] == [
        ("armed", None),
        ("held", "RACE"),
        ("held", "RACE"),
    ]
