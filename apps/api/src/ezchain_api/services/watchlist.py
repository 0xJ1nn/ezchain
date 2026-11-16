"""The persisted watchlist: identity only, never a second market read.

A row says which base token a person watches on which chain. It caches no price and no
snapshot, so a watched token that has never been observed is still a row; a filtered read
must report it as unknown rather than drop it. Validation is by chain capability and token
shape, not by a live lookup: watching a token with no snapshot is allowed and is exactly
the case the markets read has to explain.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_adapters import get_chain

from ..models import WatchlistRow

_EVM_ADDRESS = re.compile(r"^0[xX][0-9a-fA-F]{40}$")
_SVM_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class WatchlistError(ValueError):
    """A typed refusal for an identity the watchlist cannot store."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_chain(value: str) -> str:
    return value.strip().lower()[:40]


def normalize_token(value: str) -> str:
    """EVM addresses are case-insensitive; Solana mints are not."""
    token = value.strip()[:160]
    return token.lower() if _EVM_ADDRESS.fullmatch(token) else token


def validate(chain: str, base_token: str) -> tuple[str, str]:
    """Resolve a canonical identity or raise a typed 422-worthy refusal."""
    normalized_chain = normalize_chain(chain)
    meta = get_chain(normalized_chain)
    if meta is None:
        raise WatchlistError(
            "WATCHLIST_CHAIN_UNKNOWN", f"unknown chain {normalized_chain or '(empty)'}"
        )
    token = normalize_token(base_token)
    if meta.family == "evm":
        valid = _EVM_ADDRESS.fullmatch(token) is not None
    elif meta.family == "svm":
        valid = _SVM_ADDRESS.fullmatch(token) is not None
    else:
        valid = bool(token) and 2 <= len(token) <= 160 and not any(c.isspace() for c in token)
    if not valid:
        raise WatchlistError(
            "WATCHLIST_TOKEN_UNKNOWN",
            f"{token or '(empty)'} is not a valid token on {normalized_chain}",
        )
    return normalized_chain, token


def add(
    session: Session,
    chain: str,
    base_token: str,
    *,
    symbol: str | None = None,
    name: str | None = None,
) -> WatchlistRow:
    """Idempotent add: a second POST of the same pair returns the existing row.

    The unique key does the claiming; a concurrent loser re-reads the winner rather than
    surfacing an integrity error. Nothing about the row changes on a repeat add.
    """
    normalized_chain, token = validate(chain, base_token)
    row = session.scalar(
        select(WatchlistRow).where(
            WatchlistRow.chain == normalized_chain, WatchlistRow.base_token == token
        )
    )
    if row is not None:
        return row
    row = WatchlistRow(
        chain=normalized_chain,
        base_token=token,
        symbol=(symbol or None) and symbol.strip()[:40],
        name=(name or None) and name.strip()[:120],
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(WatchlistRow).where(
                WatchlistRow.chain == normalized_chain, WatchlistRow.base_token == token
            )
        )
        if existing is None:  # pragma: no cover - the unique key guarantees a winner
            raise
        return existing
    session.refresh(row)
    return row


def remove(session: Session, chain: str, base_token: str) -> bool:
    """Delete one watched pair. Removing a pair that is not watched is not an error."""
    normalized_chain, token = validate(chain, base_token)
    row = session.scalar(
        select(WatchlistRow).where(
            WatchlistRow.chain == normalized_chain, WatchlistRow.base_token == token
        )
    )
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def rows(session: Session) -> list[WatchlistRow]:
    return list(
        session.scalars(select(WatchlistRow).order_by(WatchlistRow.chain, WatchlistRow.base_token))
    )


def watched_pairs(session: Session) -> set[tuple[str, str]]:
    return {(row.chain, row.base_token) for row in rows(session)}


def to_dict(row: WatchlistRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "chain": row.chain,
        "base_token": row.base_token,
        "symbol": row.symbol,
        "name": row.name,
        "added_at": row.created_at.isoformat(),
    }
