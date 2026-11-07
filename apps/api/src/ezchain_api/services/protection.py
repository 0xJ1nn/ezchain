"""Position protection service: arm lifecycle, cancel, profiles, evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ezchain_adapters import QuoteRouter
from ezchain_core.ledger import LedgerEntry, position_account, replay_position
from ezchain_core.protection import (
    TrailingState,
    evaluate_protection,
    resolve_profile_params,
    validate_protection_params,
)
from ezchain_core.types import OrderRequest, Quote, QuoteKind, utcnow

from ..models import (
    LedgerEntryRow,
    ProtectionEventRow,
    ProtectionProfileRow,
    ProtectionRow,
)
from . import paper


class ProtectionConflict(Exception):
    """Raised when an idempotency key is reused with a different payload."""


@dataclass
class ProtectionEvaluationResult:
    protection_id: str
    action: str
    reason: str
    order_id: str | None = None


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.isoformat()


def _record_event(
    session: Session,
    protection: ProtectionRow,
    mark: Decimal | None,
    action: str,
    order_id: str | None,
) -> None:
    session.add(
        ProtectionEventRow(
            protection_id=protection.id,
            chain=protection.chain,
            token_address=protection.token_address,
            rule_snapshot=dict(protection.params or {}),
            mark=mark,
            observed_at=utcnow(),
            action=action,
            order_id=order_id,
        )
    )


def _same_payload(
    existing: ProtectionRow,
    chain: str,
    token_address: str,
    kind: str,
    params: dict[str, Any],
    profile_id: str | None,
) -> bool:
    return (
        existing.chain == chain
        and existing.token_address == token_address
        and existing.kind == kind
        and existing.params == params
        and existing.profile_id == profile_id
    )


def create_profile(
    session: Session,
    *,
    name: str,
    profile_type: str = "manual",
    defaults: dict[str, Any] | None = None,
    chain: str | None = None,
    description: str | None = None,
) -> ProtectionProfileRow:
    row = ProtectionProfileRow(
        name=name,
        profile_type=profile_type,
        chain=chain,
        defaults=defaults or {},
        description=description,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def resolve_for_chain(session: Session, *, chain: str) -> dict[str, Any]:
    globals_ = list(
        session.scalars(
            select(ProtectionProfileRow).where(ProtectionProfileRow.chain.is_(None))
        ).all()
    )
    chains = list(
        session.scalars(
            select(ProtectionProfileRow).where(ProtectionProfileRow.chain == chain)
        ).all()
    )
    result: dict[str, Any] = {}
    for profile in sorted(globals_, key=lambda p: p.created_at):
        for kind, defaults in (profile.defaults or {}).items():
            result[kind] = resolve_profile_params(
                global_defaults=result.get(kind, {}),
                chain_defaults=defaults if isinstance(defaults, dict) else {},
                explicit={},
            )
    for profile in sorted(chains, key=lambda p: p.created_at):
        for kind, defaults in (profile.defaults or {}).items():
            result[kind] = resolve_profile_params(
                global_defaults=result.get(kind, {}),
                chain_defaults=defaults if isinstance(defaults, dict) else {},
                explicit={},
            )
    return result


def arm_protection(
    session: Session,
    *,
    chain: str,
    token_address: str,
    kind: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str,
    profile_id: str | None = None,
) -> ProtectionRow:
    params = params or {}
    profile: ProtectionProfileRow | None = None
    base: dict[str, Any] = {}
    if profile_id is not None:
        profile = session.get(ProtectionProfileRow, profile_id)
        if profile is None:
            raise ValueError("profile not found")
        defaults = profile.defaults or {}
        if isinstance(defaults, dict):
            candidate = defaults.get(kind, {})
            if isinstance(candidate, dict):
                base = candidate

    final_params = resolve_profile_params(
        global_defaults=base, chain_defaults=None, explicit=params
    )
    validate_protection_params(kind, final_params)

    existing = session.scalar(
        select(ProtectionRow).where(ProtectionRow.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if _same_payload(existing, chain, token_address, kind, final_params, profile_id):
            return existing
        raise ProtectionConflict()

    source = "manual"
    if profile is not None and profile.profile_type == "smart":
        source = "smart"

    row = ProtectionRow(
        chain=chain,
        token_address=token_address,
        kind=kind,
        params=final_params,
        source=source,
        profile_id=profile_id,
        status="armed",
        version=1,
        idempotency_key=idempotency_key,
        trailing_activated=False,
        trailing_peak=None,
        trailing_rung_index=0,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    _record_event(session, row, None, "armed", None)
    session.commit()
    return row


def disarm_protection(session: Session, protection_id: str) -> ProtectionRow:
    row = session.get(ProtectionRow, protection_id)
    if row is None:
        raise ValueError("protection not found")
    row.status = "cancelled"
    session.commit()
    session.refresh(row)
    _record_event(session, row, None, "cancelled", None)
    session.commit()
    return row


def update_protection(
    session: Session,
    protection_id: str,
    *,
    params: dict[str, Any] | None = None,
    expected_version: int | None = None,
    status: str | None = None,
    profile_id: str | None = None,
) -> ProtectionRow:
    row = session.get(ProtectionRow, protection_id)
    if row is None:
        raise ValueError("protection not found")
    if expected_version is not None and row.version != expected_version:
        raise ProtectionConflict()

    changed = False
    if params is not None:
        validate_protection_params(row.kind, params)
        row.params = params
        changed = True
    if status is not None:
        row.status = status
        changed = True
    if profile_id is not None:
        row.profile_id = profile_id
        changed = True
    if changed:
        row.version = row.version + 1
    session.commit()
    session.refresh(row)
    return row


def make_router_for_tests(dex: Any, jupiter: Any) -> QuoteRouter:
    class _Provider:
        kind: ClassVar[QuoteKind] = "jupiter"

        def __init__(self, client: Any) -> None:
            self._client = client

        def supports(self, chain: Any) -> bool:
            return bool(getattr(chain, "id", chain) == "solana")

        async def quote(self, request: Any) -> Quote:
            quote, _, _ = await self._client.quote_paper(
                token_address=request.token_address,
                side=request.side,
                amount_usd=request.amount_usd,
                quantity=request.quantity,
            )
            return quote  # type: ignore[no-any-return]

    return QuoteRouter(providers=[_Provider(jupiter)], dexscreener=dex)


async def evaluate_position(
    session: Session,
    chain: str,
    token_address: str,
    *,
    settings: Any,
    dexscreener: Any,
    quotes: Any,
) -> list[ProtectionEvaluationResult]:
    account = position_account(chain, token_address)
    rows = list(
        session.scalars(
            select(LedgerEntryRow)
            .where(LedgerEntryRow.account == account)
            .order_by(LedgerEntryRow.created_at, LedgerEntryRow.id)
        ).all()
    )
    entries = (
        LedgerEntry(
            account=r.account,
            asset=r.asset,
            amount=Decimal(str(r.amount)),
            usd_value=Decimal(str(r.usd_value)),
            kind=r.kind,
        )
        for r in rows
    )
    quantity, cost = replay_position(entries)
    avg_cost = (cost / quantity) if quantity > 0 else Decimal("0")

    protections = list(
        session.scalars(
            select(ProtectionRow).where(
                ProtectionRow.chain == chain,
                ProtectionRow.token_address == token_address,
                ProtectionRow.status == "armed",
            )
        ).all()
    )
    if not protections:
        return []

    snapshot = await dexscreener.get_token(chain, token_address)
    mark = snapshot.price_usd
    results: list[ProtectionEvaluationResult] = []

    for prot in protections:
        trailing = TrailingState(
            peak=prot.trailing_peak,
            activated=prot.trailing_activated,
            rung_index=prot.trailing_rung_index or 0,
        )
        if mark is None or getattr(snapshot, "stale", False):
            reason = "PRICE_UNKNOWN" if mark is None else "PRICE_STALE"
            results.append(ProtectionEvaluationResult(prot.id, "hold", reason, None))
            continue

        decision = evaluate_protection(
            kind=prot.kind,
            params=prot.params or {},
            avg_cost_usd=avg_cost,
            mark_usd=mark,
            held_qty=quantity,
            trailing=trailing,
        )

        prot.trailing_activated = bool(decision.new_activated)
        if decision.new_peak is not None:
            prot.trailing_peak = decision.new_peak
        prot.trailing_rung_index = int(decision.new_rung_index or 0)
        session.flush()

        if decision.action == "sell":
            sell_qty = quantity * decision.sell_fraction
            amount_usd = sell_qty * Decimal(str(mark))
            idem = f"protection:{prot.id}:{mark}"
            payload = OrderRequest(
                chain=chain,
                token_address=token_address,
                side="sell",
                amount_usd=amount_usd,
                idempotency_key=idem,
            )
            try:
                order = await paper.place_order(session, payload, settings, dexscreener, quotes)
            except Exception:
                order = None
            if order is not None and getattr(order, "status", None) == "filled":
                prot.status = "triggered"
                _record_event(session, prot, mark, "triggered", order.id)
                results.append(
                    ProtectionEvaluationResult(prot.id, "triggered", decision.reason, order.id)
                )
            else:
                order_id = getattr(order, "id", None)
                _record_event(session, prot, mark, "rejected", order_id)
                results.append(
                    ProtectionEvaluationResult(
                        prot.id,
                        "rejected",
                        getattr(order, "reason", decision.reason) or decision.reason,
                        order_id,
                    )
                )
        else:
            results.append(ProtectionEvaluationResult(prot.id, "hold", decision.reason, None))

    session.commit()
    return results
