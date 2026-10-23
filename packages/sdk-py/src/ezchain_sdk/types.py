"""The data a plugin may see and the intents it may return.

Money and quantities are :class:`~decimal.Decimal` in process and decimal strings on the
wire. Nothing here imports platform code; this package is Apache-2.0 and standalone.
"""

from __future__ import annotations

import random as _random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Mode = Literal["paper", "live"]
Side = Literal["buy", "sell"]


def _decimal(value: object, *, default: Decimal | None = None) -> Decimal | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("decimal must be finite")
    return result


def _optional_decimal(value: object) -> Decimal | None:
    return _decimal(value)


def _aware(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError(f"invalid datetime: {value!r}")


def _iso(value: datetime) -> str:
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return moment.isoformat()


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_time": _iso(self.open_time),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": _decimal_str(self.volume),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Candle:
        return cls(
            open_time=_aware(data["open_time"]),
            open=_decimal(data["open"]) or Decimal(0),
            high=_decimal(data["high"]) or Decimal(0),
            low=_decimal(data["low"]) or Decimal(0),
            close=_decimal(data["close"]) or Decimal(0),
            volume=_optional_decimal(data.get("volume")),
        )


@dataclass(frozen=True)
class Snapshot:
    chain: str
    address: str
    symbol: str
    name: str
    price_usd: Decimal | None
    liquidity_usd: Decimal | None = None
    volume_24h_usd: Decimal | None = None
    price_change_m5: Decimal | None = None
    price_change_h1: Decimal | None = None
    price_change_h6: Decimal | None = None
    price_change_h24: Decimal | None = None
    stale: bool = False
    observed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "address": self.address,
            "symbol": self.symbol,
            "name": self.name,
            "price_usd": _decimal_str(self.price_usd),
            "liquidity_usd": _decimal_str(self.liquidity_usd),
            "volume_24h_usd": _decimal_str(self.volume_24h_usd),
            "price_change_m5": _decimal_str(self.price_change_m5),
            "price_change_h1": _decimal_str(self.price_change_h1),
            "price_change_h6": _decimal_str(self.price_change_h6),
            "price_change_h24": _decimal_str(self.price_change_h24),
            "stale": self.stale,
            "observed_at": None if self.observed_at is None else _iso(self.observed_at),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Snapshot:
        return cls(
            chain=str(data["chain"]),
            address=str(data["address"]),
            symbol=str(data["symbol"]),
            name=str(data["name"]),
            price_usd=_optional_decimal(data.get("price_usd")),
            liquidity_usd=_optional_decimal(data.get("liquidity_usd")),
            volume_24h_usd=_optional_decimal(data.get("volume_24h_usd")),
            price_change_m5=_optional_decimal(data.get("price_change_m5")),
            price_change_h1=_optional_decimal(data.get("price_change_h1")),
            price_change_h6=_optional_decimal(data.get("price_change_h6")),
            price_change_h24=_optional_decimal(data.get("price_change_h24")),
            stale=bool(data.get("stale", False)),
            observed_at=None if data.get("observed_at") is None else _aware(data["observed_at"]),
        )


@dataclass(frozen=True)
class Position:
    chain: str
    token_address: str
    symbol: str
    quantity: Decimal
    avg_cost_usd: Decimal | None
    mark_usd: Decimal | None = None
    market_value_usd: Decimal | None = None
    unrealized_usd: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "token_address": self.token_address,
            "symbol": self.symbol,
            "quantity": str(self.quantity),
            "avg_cost_usd": _decimal_str(self.avg_cost_usd),
            "mark_usd": _decimal_str(self.mark_usd),
            "market_value_usd": _decimal_str(self.market_value_usd),
            "unrealized_usd": _decimal_str(self.unrealized_usd),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Position:
        return cls(
            chain=str(data["chain"]),
            token_address=str(data["token_address"]),
            symbol=str(data.get("symbol", "")),
            quantity=_decimal(data.get("quantity")) or Decimal(0),
            avg_cost_usd=_optional_decimal(data.get("avg_cost_usd")),
            mark_usd=_optional_decimal(data.get("mark_usd")),
            market_value_usd=_optional_decimal(data.get("market_value_usd")),
            unrealized_usd=_optional_decimal(data.get("unrealized_usd")),
        )


@dataclass(frozen=True)
class Account:
    cash_usd: Decimal
    equity_usd: Decimal
    realized_usd: Decimal
    unrealized_usd: Decimal
    open_positions: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_usd": str(self.cash_usd),
            "equity_usd": str(self.equity_usd),
            "realized_usd": str(self.realized_usd),
            "unrealized_usd": str(self.unrealized_usd),
            "open_positions": self.open_positions,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Account:
        return cls(
            cash_usd=_decimal(data.get("cash_usd")) or Decimal(0),
            equity_usd=_decimal(data.get("equity_usd")) or Decimal(0),
            realized_usd=_decimal(data.get("realized_usd")) or Decimal(0),
            unrealized_usd=_decimal(data.get("unrealized_usd")) or Decimal(0),
            open_positions=int(data.get("open_positions", 0)),
        )


@dataclass(frozen=True)
class Fill:
    side: Side
    quantity: Decimal
    price_usd: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "quantity": str(self.quantity),
            "price_usd": str(self.price_usd),
            "notional_usd": str(self.notional_usd),
            "fee_usd": str(self.fee_usd),
            "created_at": _iso(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Fill:
        return cls(
            side="buy" if data.get("side") == "buy" else "sell",
            quantity=_decimal(data.get("quantity")) or Decimal(0),
            price_usd=_decimal(data.get("price_usd")) or Decimal(0),
            notional_usd=_decimal(data.get("notional_usd")) or Decimal(0),
            fee_usd=_decimal(data.get("fee_usd")) or Decimal(0),
            created_at=_aware(data["created_at"]),
        )


@dataclass(frozen=True)
class Signal:
    name: str
    observed_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed_at": _iso(self.observed_at),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Signal:
        payload = data.get("payload", {})
        return cls(
            name=str(data["name"]),
            observed_at=_aware(data["observed_at"]),
            payload=dict(payload) if isinstance(payload, Mapping) else {},
        )


@dataclass(frozen=True)
class Allocation:
    """The only balance a plugin may reason about; the host still clamps every order."""

    max_notional_usd: Decimal
    max_positions: int
    max_daily_loss_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_notional_usd": str(self.max_notional_usd),
            "max_positions": self.max_positions,
            "max_daily_loss_usd": str(self.max_daily_loss_usd),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Allocation:
        return cls(
            max_notional_usd=_decimal(data.get("max_notional_usd")) or Decimal(0),
            max_positions=int(data.get("max_positions", 0)),
            max_daily_loss_usd=_decimal(data.get("max_daily_loss_usd")) or Decimal(0),
        )


@dataclass(frozen=True)
class Intent:
    """A request, never an order. The host decides size, price, route and execution."""

    side: Side
    notional_usd: Decimal
    reason: str
    dedupe_key: str | None = None
    # Reserved for a future execution contract. The v1 host rejects them explicitly
    # rather than silently ignoring a limit price the user believed was active.
    limit_price: Decimal | None = None
    ttl_s: int | None = None
    size_pct: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if not isinstance(self.notional_usd, Decimal):
            object.__setattr__(self, "notional_usd", _decimal(self.notional_usd) or Decimal(0))
        if self.notional_usd <= 0:
            raise ValueError("notional_usd must be > 0")
        if not self.reason or len(self.reason) > 64:
            raise ValueError("reason must be 1-64 characters")
        if self.dedupe_key is not None and len(self.dedupe_key) > 120:
            raise ValueError("dedupe_key must be at most 120 characters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "notional_usd": str(self.notional_usd),
            "reason": self.reason,
            "dedupe_key": self.dedupe_key,
            "limit_price": _decimal_str(self.limit_price),
            "ttl_s": self.ttl_s,
            "size_pct": _decimal_str(self.size_pct),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Intent:
        side = data.get("side")
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        return cls(
            side=side,
            notional_usd=_decimal(data.get("notional_usd")) or Decimal(0),
            reason=str(data.get("reason", "")),
            dedupe_key=None if data.get("dedupe_key") is None else str(data["dedupe_key"]),
            limit_price=_optional_decimal(data.get("limit_price")),
            ttl_s=None if data.get("ttl_s") is None else int(data["ttl_s"]),
            size_pct=_optional_decimal(data.get("size_pct")),
        )


@dataclass
class PluginContext:
    """Everything one tick may read. Mutable only through ``state`` and ``logs``."""

    now: datetime
    mode: Mode
    params: Mapping[str, Any]
    state: dict[str, Any]
    snapshot: Snapshot | None = None
    candles: Mapping[str, tuple[Candle, ...]] = field(default_factory=dict)
    position: Position | None = None
    positions: tuple[Position, ...] = ()
    account: Account | None = None
    signals: tuple[Signal, ...] = ()
    allocation: Allocation | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)

    def candles_for(self, timeframe: str, n: int) -> tuple[Candle, ...]:
        """The last ``n`` closed bars, oldest first. Unknown timeframe is empty."""
        bars = self.candles.get(timeframe, ())
        if n <= 0:
            return ()
        return tuple(bars[-n:])

    def log(self, level: str, message: str, **fields: Any) -> None:
        """Structured log line returned to the host. Rate limiting is the host's job."""
        if len(self.logs) >= 100:
            return
        self.logs.append(
            {
                "level": level.lower()[:16],
                "message": str(message)[:500],
                "fields": {str(key)[:64]: str(value)[:500] for key, value in fields.items()},
            }
        )

    def random(self, seed: int | str) -> _random.Random:
        """A deterministic RNG. The same seed always yields the same stream."""
        return _random.Random(str(seed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "now": _iso(self.now),
            "mode": self.mode,
            "params": dict(self.params),
            "state": dict(self.state),
            "snapshot": None if self.snapshot is None else self.snapshot.to_dict(),
            "candles": {
                timeframe: [bar.to_dict() for bar in bars]
                for timeframe, bars in self.candles.items()
            },
            "position": None if self.position is None else self.position.to_dict(),
            "positions": [position.to_dict() for position in self.positions],
            "account": None if self.account is None else self.account.to_dict(),
            "signals": [signal.to_dict() for signal in self.signals],
            "allocation": None if self.allocation is None else self.allocation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PluginContext:
        candles_raw = data.get("candles", {})
        candles = (
            {
                str(timeframe): tuple(Candle.from_dict(bar) for bar in bars)
                for timeframe, bars in candles_raw.items()
            }
            if isinstance(candles_raw, Mapping)
            else {}
        )
        positions_raw = data.get("positions", [])
        signals_raw = data.get("signals", [])
        return cls(
            now=_aware(data["now"]),
            mode="live" if data.get("mode") == "live" else "paper",
            params=dict(data.get("params", {})),
            state=dict(data.get("state", {})),
            snapshot=(
                None if data.get("snapshot") is None else Snapshot.from_dict(data["snapshot"])
            ),
            candles=candles,
            position=(
                None if data.get("position") is None else Position.from_dict(data["position"])
            ),
            positions=tuple(Position.from_dict(item) for item in positions_raw)
            if isinstance(positions_raw, Sequence)
            else (),
            account=(None if data.get("account") is None else Account.from_dict(data["account"])),
            signals=tuple(Signal.from_dict(item) for item in signals_raw)
            if isinstance(signals_raw, Sequence)
            else (),
            allocation=(
                None if data.get("allocation") is None else Allocation.from_dict(data["allocation"])
            ),
        )


__all__ = [
    "Account",
    "Allocation",
    "Candle",
    "Fill",
    "Intent",
    "Mode",
    "PluginContext",
    "Position",
    "Side",
    "Signal",
    "Snapshot",
]
