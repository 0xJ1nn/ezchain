"""Paper account read models. The order service lands with P0.2."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_core import genesis_entries
from ezchain_core.ledger import cash_account, realized_account

from ..models import LedgerEntryRow, PaperFillRow, PaperOrderRow


def ensure_genesis(session: Session, starting_cash_usd: Decimal) -> None:
    existing = session.scalar(select(func.count()).select_from(LedgerEntryRow))
    if existing:
        return
    for entry in genesis_entries(starting_cash_usd):
        session.add(LedgerEntryRow(**entry.to_row("genesis")))
    session.commit()


def cash_balance(session: Session) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(LedgerEntryRow.usd_value), 0)).where(
            LedgerEntryRow.account == cash_account()
        )
    )
    return Decimal(str(value or 0))


def realized_pnl(session: Session) -> Decimal:
    value = session.scalar(
        select(func.coalesce(func.sum(LedgerEntryRow.usd_value), 0)).where(
            LedgerEntryRow.account == realized_account()
        )
    )
    return -Decimal(str(value or 0))


def account_snapshot(session: Session, starting_cash_usd: Decimal) -> dict[str, object]:
    cash = cash_balance(session)
    realized = realized_pnl(session)
    return {
        "cash_usd": str(cash),
        "equity_usd": str(cash),
        "realized_usd": str(realized),
        "unrealized_usd": "0",
        "positions": [],
        "mode": "paper",
    }


def orders(session: Session, limit: int = 50) -> list[PaperOrderRow]:
    return list(
        session.scalars(
            select(PaperOrderRow).order_by(PaperOrderRow.created_at.desc()).limit(limit)
        )
    )


def fills(session: Session, limit: int = 50) -> list[PaperFillRow]:
    return list(
        session.scalars(select(PaperFillRow).order_by(PaperFillRow.created_at.desc()).limit(limit))
    )
