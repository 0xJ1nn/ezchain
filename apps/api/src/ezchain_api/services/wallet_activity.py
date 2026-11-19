"""Tracked wallets, their observed swaps and honestly-derived performance.

A tracked wallet is an identity the person follows, not a paper account: it holds
no cash and writes no ledger entry. Observations are recorded before any read and
keyed by the source's own transaction id, so a refresh cannot double-count. Every
derived number comes from those stored observations; where a value is missing it
stays unknown rather than becoming zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_adapters import get_chain
from ezchain_adapters.wallet_sources import ObservedSwap, WalletActivitySource

from ..models import new_id
from .wallet_models import TrackedWalletRow, WalletSwapRow

WINDOW_DAYS = 30
REFRESH_LIMIT = 100


class WalletError(ValueError):
    code = "WALLET_ERROR"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ChainNotFound(WalletError):
    code = "CHAIN_NOT_FOUND"
    status_code = 404


class WalletNotFound(WalletError):
    code = "WALLET_NOT_FOUND"
    status_code = 404


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()


def track(
    session: Session, *, chain: str, address: str, label: str
) -> tuple[TrackedWalletRow, bool]:
    """Follow an address on a supported chain. Repeating the pair is idempotent."""
    if get_chain(chain) is None:
        raise ChainNotFound(f"unknown chain {chain}")
    clean_address = address.strip()
    clean_label = label.strip()
    if not clean_address or len(clean_address) > 160:
        raise WalletError("address must be 1-160 characters")
    if not clean_label or len(clean_label) > 120:
        raise WalletError("label must be 1-120 characters")
    existing = session.scalar(
        select(TrackedWalletRow).where(
            TrackedWalletRow.chain == chain, TrackedWalletRow.address == clean_address
        )
    )
    if existing is not None:
        return existing, False
    row = TrackedWalletRow(id=new_id(), chain=chain, address=clean_address, label=clean_label)
    session.add(row)
    session.commit()
    return row, True


def untrack(session: Session, wallet_id: str) -> bool:
    """Stop following a wallet. Its recorded observations are kept as evidence."""
    row = session.get(TrackedWalletRow, wallet_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_tracked(session: Session) -> list[TrackedWalletRow]:
    return list(
        session.scalars(select(TrackedWalletRow).order_by(TrackedWalletRow.created_at.desc()))
    )


def get_wallet(session: Session, wallet_id: str) -> TrackedWalletRow:
    row = session.get(TrackedWalletRow, wallet_id)
    if row is None:
        raise WalletNotFound(f"unknown tracked wallet {wallet_id}")
    return row


def activity(session: Session, wallet_id: str, *, limit: int = 50) -> list[WalletSwapRow]:
    return list(
        session.scalars(
            select(WalletSwapRow)
            .where(WalletSwapRow.wallet_id == wallet_id)
            .order_by(WalletSwapRow.observed_at.desc(), WalletSwapRow.id.desc())
            .limit(limit)
        )
    )


def record_swaps(session: Session, wallet_id: str, swaps: Iterable[ObservedSwap]) -> int:
    """Insert observations not already seen. The source transaction is the identity."""
    existing = set(
        session.scalars(
            select(
                WalletSwapRow.source, WalletSwapRow.source_tx, WalletSwapRow.token_address
            ).where(WalletSwapRow.wallet_id == wallet_id)
        )
    )
    inserted = 0
    for swap in swaps:
        key = (swap.source, swap.source_tx, swap.token_address)
        if key in existing:
            continue
        session.add(
            WalletSwapRow(
                id=new_id(),
                wallet_id=wallet_id,
                source=swap.source,
                source_tx=swap.source_tx,
                chain=swap.chain,
                side=swap.side,
                token_address=swap.token_address,
                token_symbol=swap.token_symbol,
                amount_tokens=swap.amount_tokens,
                amount_usd=swap.amount_usd,
                price_usd=swap.price_usd,
                observed_at=_aware(swap.observed_at),
                raw={},
            )
        )
        existing.add(key)
        inserted += 1
    if not inserted:
        return 0
    try:
        session.commit()
    except IntegrityError:
        # A concurrent refresh recorded the same observation first. The evidence is
        # on disk either way; this reader does not manufacture another copy.
        session.rollback()
        return 0
    return inserted


async def refresh(
    session: Session,
    wallet: TrackedWalletRow,
    sources: Iterable[WalletActivitySource],
    *,
    now: datetime | None = None,
) -> int:
    """Pull recent activity from every source and record what is new."""
    current = _aware(now or datetime.now(UTC))
    since = current - timedelta(days=WINDOW_DAYS)
    collected: list[ObservedSwap] = []
    for source in sources:
        found = await source.fetch_swaps(
            chain=wallet.chain, address=wallet.address, since=since, limit=REFRESH_LIMIT
        )
        collected.extend(found)
    return record_swaps(session, wallet.id, collected)


def performance(
    swaps: list[WalletSwapRow], *, since: datetime
) -> tuple[dict[str, Decimal | None], Decimal | None, Decimal | None]:
    """Holdings, 30-day realized P&L and win rate from stored observations.

    A token's holding is unknown when any of its swaps omitted a token amount.
    Realized P&L and win rate are unknown when any swap in the window omitted a
    token amount or a USD value: averaging over the known trades would present a
    partial result as the wallet's whole performance (CONTRACT I3).
    """
    holdings: dict[str, Decimal | None] = {}
    for token in sorted({row.token_address for row in swaps}):
        rows = [row for row in swaps if row.token_address == token]
        if any(row.amount_tokens is None for row in rows):
            holdings[token] = None
            continue
        quantity = Decimal(0)
        for row in rows:
            amount = row.amount_tokens or Decimal(0)
            quantity += amount if row.side == "buy" else -amount
        holdings[token] = quantity

    window = [row for row in swaps if _aware(row.observed_at) >= since]
    if any(row.amount_tokens is None or row.amount_usd is None for row in window):
        return holdings, None, None
    quantity = Decimal(0)
    cost = Decimal(0)
    realized = Decimal(0)
    wins = 0
    closed = 0
    for row in sorted(window, key=lambda item: (item.observed_at, item.id)):
        amount = row.amount_tokens or Decimal(0)
        usd = row.amount_usd or Decimal(0)
        if row.side == "buy":
            quantity += amount
            cost += usd
            continue
        sold = min(amount, quantity)
        if quantity <= 0 or sold <= 0:
            continue
        average = cost / quantity
        pnl = usd - average * sold
        realized += pnl
        cost -= average * sold
        quantity -= sold
        closed += 1
        if pnl > 0:
            wins += 1
    win_rate = None if closed == 0 else Decimal(wins) / Decimal(closed)
    return holdings, realized, win_rate


def detail(session: Session, wallet_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    current = _aware(now or datetime.now(UTC))
    wallet = get_wallet(session, wallet_id)
    swaps = list(session.scalars(select(WalletSwapRow).where(WalletSwapRow.wallet_id == wallet.id)))
    holdings, realized, win_rate = performance(swaps, since=current - timedelta(days=WINDOW_DAYS))
    recent = sorted(swaps, key=lambda row: (row.observed_at, row.id), reverse=True)[:10]
    return {
        "id": wallet.id,
        "chain": wallet.chain,
        "address": wallet.address,
        "label": wallet.label,
        "created_at": _iso(wallet.created_at),
        "holdings": {
            token: None if value is None else str(value) for token, value in holdings.items()
        },
        "realized_pnl_30d_usd": None if realized is None else str(realized),
        "win_rate_30d": None if win_rate is None else str(win_rate),
        "recent_swaps": [_swap_out(row) for row in recent],
        "mode": "paper",
    }


def _swap_out(row: WalletSwapRow) -> dict[str, Any]:
    return {
        "source_tx": row.source_tx,
        "source": row.source,
        "chain": row.chain,
        "side": row.side,
        "token_address": row.token_address,
        "token_symbol": row.token_symbol,
        "amount_tokens": None if row.amount_tokens is None else str(row.amount_tokens),
        "amount_usd": None if row.amount_usd is None else str(row.amount_usd),
        "observed_at": _iso(row.observed_at),
    }


def wallet_out(row: TrackedWalletRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "chain": row.chain,
        "address": row.address,
        "label": row.label,
        "created_at": _iso(row.created_at),
    }


def swap_out(row: WalletSwapRow) -> dict[str, Any]:
    return _swap_out(row)


__all__ = [
    "ChainNotFound",
    "WalletError",
    "WalletNotFound",
    "activity",
    "detail",
    "get_wallet",
    "list_tracked",
    "performance",
    "record_swaps",
    "refresh",
    "swap_out",
    "track",
    "untrack",
    "wallet_out",
]
