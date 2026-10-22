"""Small typed builders shared by the engine tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ezchain_core.strategy import StrategySpec
from ezchain_core.types import SourceRef, TokenSnapshot

CHAIN = "solana"
TOKEN = "So11111111111111111111111111111111111111112"


def make_snapshot(
    *,
    price: str | None = "1",
    change_h1: str | None = "6",
    change_m5: str | None = "0",
    change_h6: str | None = "0",
    change_h24: str | None = "0",
    stale: bool = False,
) -> TokenSnapshot:
    def dec(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    return TokenSnapshot(
        chain=CHAIN,
        address=TOKEN,
        symbol="TEST",
        name="Test",
        price_usd=dec(price),
        liquidity_usd=Decimal("100000"),
        volume_24h_usd=Decimal("1000000"),
        price_change_m5=dec(change_m5),
        price_change_h1=dec(change_h1),
        price_change_h6=dec(change_h6),
        price_change_h24=dec(change_h24),
        source=SourceRef("dexscreener", "https://dexscreener.com", datetime.now(UTC)),
        stale=stale,
    )


def make_spec(
    kind: str = "momentum",
    params: dict[str, object] | None = None,
    *,
    status: str = "active",
    last_run_at: datetime | None = None,
) -> StrategySpec:
    return StrategySpec(
        id="s1",
        name="test",
        chain=CHAIN,
        token_address=TOKEN,
        token_symbol="TEST",
        kind=kind,  # type: ignore[arg-type]
        params=params or {},
        status=status,
        last_run_at=last_run_at,
    )


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


__all__ = ["CHAIN", "TOKEN", "make_snapshot", "make_spec", "utc"]
