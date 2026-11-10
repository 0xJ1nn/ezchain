"""Engine evaluation for protection arms on scheduler ticks.

Pure decisions live in ``ezchain_core.protection``; this module adds the side
effects: one market read, one ledger replay, one OrderExecutor intent on a
trigger. The intent carries no strategy attribution, so it shares the single
paper execution path with manual orders (CONTRACT I2) and the ledger journals
the close exactly like any other fill. Unknown or stale marks fail closed
without an order; a missing market read never expires or cancels an arm.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, cast, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_api.models import LedgerEntryRow, ProtectionArmRow
from ezchain_api.services import protection as protection_service
from ezchain_core.ledger import LedgerEntry, position_account, replay_position
from ezchain_core.protection import (
    ArmKind,
    ArmStatus,
    ProtectionArm,
    ProtectionValidationError,
    evaluate_arm,
    validate_arm,
)
from ezchain_core.types import OrderRequest, TokenSnapshot, utcnow

logger = logging.getLogger(__name__)


@runtime_checkable
class TokenSource(Protocol):
    """The only market-data call the protection runner needs."""

    async def get_token(self, chain: str, address: str) -> TokenSnapshot: ...


@runtime_checkable
class OrderExecutor(Protocol):
    """The paper order service port; see scheduler.OrderExecutor."""

    async def place_order(self, request: OrderRequest) -> str: ...


@dataclass(frozen=True)
class ProtectionResult:
    arm_id: str
    action: str
    reason: str
    sell_fraction: Decimal
    level_index: int | None
    order_id: str | None
    arm_status: str
    peak_usd: Decimal | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _stamp(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def protection_idempotency_key(arm_id: str, level_index: int | None) -> str:
    """Stable per arm and ladder level: a retry after a crash cannot double-fill."""
    suffix = "single" if level_index is None else f"level-{level_index}"
    return f"protection:{arm_id}:{suffix}"


def arm_from_row(row: ProtectionArmRow) -> ProtectionArm:
    """Parse a persisted row through the core validator. Raises on bad params."""
    params = validate_arm(row.kind, row.params or {})
    return ProtectionArm(
        id=row.id,
        chain=row.chain,
        token_address=row.token_address,
        token_symbol=row.token_symbol,
        kind=cast(ArmKind, row.kind),
        status=cast(ArmStatus, row.status),
        params=params,
        peak_usd=Decimal(str(row.peak_usd)) if row.peak_usd is not None else None,
        triggered_level_indexes=tuple(int(v) for v in (row.triggered_levels or [])),
        triggered_at=_aware(row.triggered_at),
        order_id=row.order_id,
        expires_at=_aware(row.expires_at),
        profile_id=row.profile_id,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def position_state(session: Session, chain: str, token_address: str) -> tuple[Decimal, Decimal]:
    """(quantity, avg_cost_usd) from the ledger. No mutable scalar."""
    rows = session.scalars(
        select(LedgerEntryRow)
        .where(LedgerEntryRow.account == position_account(chain, token_address))
        .order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
    ).all()
    entries = (
        LedgerEntry(
            account=row.account,
            asset=row.asset,
            amount=Decimal(str(row.amount)),
            usd_value=Decimal(str(row.usd_value)),
            kind=row.kind,
        )
        for row in rows
    )
    quantity, cost = replay_position(entries)
    avg = Decimal(0) if quantity == 0 else cost / quantity
    return quantity, avg


def _hold(row: ProtectionArmRow, reason: str, peak: Decimal | None) -> ProtectionResult:
    return ProtectionResult(
        arm_id=row.id,
        action="hold",
        reason=reason,
        sell_fraction=Decimal(0),
        level_index=None,
        order_id=None,
        arm_status=row.status,
        peak_usd=peak,
    )


async def evaluate_arm_row(
    session: Session,
    row: ProtectionArmRow,
    token_source: TokenSource,
    order_executor: OrderExecutor,
    *,
    now: datetime | None = None,
) -> ProtectionResult:
    """Evaluate one arm once. One failing arm never raises to the scheduler pass."""
    now = now or utcnow()
    if row.status != "armed":
        return _hold(row, "NOT_ARMED", row.peak_usd)
    if row.expires_at is not None and now >= _aware(row.expires_at):  # type: ignore[operator]
        row.status = "expired"
        row.version = int(row.version or 1) + 1
        row.updated_at = _stamp(now)
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="expired",
            reason="EXPIRED",
            rule_snapshot=dict(row.params or {}),
            mark=None,
            observed_at=now,
            order_id=row.order_id,
        )
        session.commit()
        return _hold(row, "EXPIRED", row.peak_usd)

    try:
        arm = arm_from_row(row)
    except ProtectionValidationError:
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="held",
            reason="INVALID_ARM",
            rule_snapshot=dict(row.params or {}),
            mark=None,
            observed_at=now,
            order_id=None,
            dedupe=True,
        )
        session.commit()
        return _hold(row, "INVALID_ARM", row.peak_usd)

    try:
        snapshot = await token_source.get_token(row.chain, row.token_address)
    except Exception:  # fail closed: no mark, no order, arm stays armed
        logger.warning("protection arm %s market data unavailable", row.id)
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="held",
            reason="MARKET_DATA_UNAVAILABLE",
            rule_snapshot=dict(row.params or {}),
            mark=None,
            observed_at=now,
            order_id=None,
            dedupe=True,
        )
        session.commit()
        return _hold(row, "MARKET_DATA_UNAVAILABLE", row.peak_usd)

    quantity, avg_cost = position_state(session, row.chain, row.token_address)
    evaluation = evaluate_arm(
        arm,
        price_usd=snapshot.price_usd,
        position_quantity=quantity,
        position_avg_cost_usd=avg_cost,
        stale=snapshot.stale,
        now=now,
    )

    # Persist trailing peaks even on a hold: the peak is persisted input for replay.
    if evaluation.peak_usd != (Decimal(str(row.peak_usd)) if row.peak_usd is not None else None):
        row.peak_usd = evaluation.peak_usd
        row.version = int(row.version or 1) + 1
        row.updated_at = _stamp(now)
        session.commit()

    if evaluation.action == "hold":
        mark = snapshot.price_usd
        if evaluation.reason == "EXPIRED":
            row.status = "expired"
            row.version = int(row.version or 1) + 1
            row.updated_at = _stamp(now)
            protection_service.record_event(
                session,
                arm_id=row.id,
                action="expired",
                reason=evaluation.reason,
                rule_snapshot=dict(row.params or {}),
                mark=mark,
                observed_at=now,
                order_id=row.order_id,
            )
            session.commit()
            return _hold(row, evaluation.reason, evaluation.peak_usd)
        # Record a held observation (including fail-closed holds) for replay.
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="held",
            reason=evaluation.reason,
            rule_snapshot=dict(row.params or {}),
            mark=mark,
            observed_at=now,
            order_id=None,
            dedupe=True,
        )
        session.commit()
        return _hold(row, evaluation.reason, evaluation.peak_usd)

    price = snapshot.price_usd
    assert price is not None and quantity > 0
    amount = quantity * price * evaluation.sell_fraction
    request = OrderRequest(
        chain=row.chain,
        token_address=row.token_address,
        side="sell",
        amount_usd=amount,
        idempotency_key=protection_idempotency_key(row.id, evaluation.level_index),
        # The arm knows the position, so it sells an exact token quantity rather than a
        # USD notional divided by a mark that may move between evaluation and order.
        quantity=quantity * evaluation.sell_fraction,
        # A protection exit reduces an existing position; entry-only risk gates must not
        # be able to veto it after the arm has decided to fire.
        reduce_only=True,
    )
    try:
        order_id = await order_executor.place_order(request)
    except Exception:  # fail closed: retry next tick, keep the arm armed
        logger.warning("protection arm %s order failed; staying armed", row.id)
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="rejected",
            reason="ORDER_FAILED",
            rule_snapshot=dict(row.params or {}),
            mark=snapshot.price_usd,
            observed_at=now,
            order_id=None,
            dedupe=True,
        )
        session.commit()
        return _hold(row, "ORDER_FAILED", evaluation.peak_usd)

    if evaluation.level_index is not None and arm.params.levels:
        triggered = list(row.triggered_levels or [])
        if evaluation.level_index not in triggered:
            triggered.append(evaluation.level_index)
        row.triggered_levels = triggered
        if len(triggered) >= len(arm.params.levels):
            row.status = "triggered"
            row.triggered_at = _stamp(now)
            row.order_id = order_id
        row.version = int(row.version or 1) + 1
        row.updated_at = _stamp(now)
        protection_service.record_event(
            session,
            arm_id=row.id,
            action="triggered",
            reason=evaluation.reason,
            rule_snapshot=dict(row.params or {}),
            mark=snapshot.price_usd,
            observed_at=now,
            order_id=order_id,
        )
        session.commit()
        return ProtectionResult(
            arm_id=row.id,
            action="trigger",
            reason=evaluation.reason,
            sell_fraction=evaluation.sell_fraction,
            level_index=evaluation.level_index,
            order_id=order_id,
            arm_status=row.status,
            peak_usd=evaluation.peak_usd,
        )

    row.status = "triggered"
    row.triggered_at = _stamp(now)
    row.order_id = order_id
    row.version = int(row.version or 1) + 1
    row.updated_at = _stamp(now)
    protection_service.record_event(
        session,
        arm_id=row.id,
        action="triggered",
        reason=evaluation.reason,
        rule_snapshot=dict(row.params or {}),
        mark=snapshot.price_usd,
        observed_at=now,
        order_id=order_id,
    )
    session.commit()
    return ProtectionResult(
        arm_id=row.id,
        action="trigger",
        reason=evaluation.reason,
        sell_fraction=evaluation.sell_fraction,
        level_index=evaluation.level_index,
        order_id=order_id,
        arm_status=row.status,
        peak_usd=evaluation.peak_usd,
    )


async def evaluate_all_arms(
    session_factory: Callable[[], Session],
    token_source: TokenSource,
    order_executor: OrderExecutor,
    *,
    now: datetime | None = None,
) -> list[ProtectionResult]:
    """One pass over armed arms. One buckled arm never stops the rest."""
    if getattr(order_executor, "active", False):
        logger.warning("protection pass idle: operator kill switch is active")
        return []
    moment = now or utcnow()
    with session_factory() as session:
        arm_ids = list(
            session.scalars(
                select(ProtectionArmRow.id)
                .where(ProtectionArmRow.status == "armed")
                .order_by(ProtectionArmRow.created_at)
            ).all()
        )
    results: list[ProtectionResult] = []
    for arm_id in arm_ids:
        with session_factory() as session:
            row = session.get(ProtectionArmRow, arm_id)
            if row is None or row.status != "armed":
                continue
            try:
                results.append(
                    await evaluate_arm_row(session, row, token_source, order_executor, now=moment)
                )
            except Exception:
                logger.exception("protection arm %s evaluation crashed", arm_id)
                results.append(
                    ProtectionResult(
                        arm_id=arm_id,
                        action="hold",
                        reason="EVALUATION_FAILED",
                        sell_fraction=Decimal(0),
                        level_index=None,
                        order_id=None,
                        arm_status="armed",
                        peak_usd=None,
                    )
                )
    return results
