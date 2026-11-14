"""Turns, tokens and cost — per session and per day.

The daily counters belong to the service, not the session: a new session starts with a fresh
session budget and inherits the day's spend. Reaching a limit refuses the next turn or tool
call with a typed :class:`StopReason`, never a silent clamp.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal

from .session import StopReason

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class BudgetLimits:
    session_turns: int
    session_tokens: int
    session_cost_usd: Decimal
    daily_turns: int
    daily_tokens: int
    daily_cost_usd: Decimal

    def __post_init__(self) -> None:
        for name in ("session_turns", "session_tokens", "daily_turns", "daily_tokens"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        for name in ("session_cost_usd", "daily_cost_usd"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass
class Usage:
    turns: int = 0
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")

    def add(self, *, turns: int = 0, tokens: int = 0, cost_usd: Decimal = Decimal("0")) -> None:
        self.turns += turns
        self.tokens += tokens
        self.cost_usd += cost_usd

    def copy(self) -> Usage:
        return replace(self)


class DailyLedger:
    """The day's spend, rolled at UTC midnight."""

    def __init__(self, limits: BudgetLimits, *, clock: Clock = utcnow) -> None:
        self._limits = limits
        self._clock = clock
        self._day: date = clock().date()
        self._usage = Usage()

    @property
    def day(self) -> date:
        return self._day

    def _roll(self) -> None:
        today = self._clock().date()
        if today != self._day:
            self._day = today
            self._usage = Usage()

    def used(self) -> Usage:
        self._roll()
        return self._usage.copy()

    def consume(self, *, turns: int = 0, tokens: int = 0, cost_usd: Decimal = Decimal("0")) -> None:
        self._roll()
        self._usage.add(turns=turns, tokens=tokens, cost_usd=cost_usd)


class BudgetExhausted(RuntimeError):
    def __init__(
        self,
        reason: StopReason,
        *,
        window: str,
        measure: str,
        limit: int | Decimal,
        used: int | Decimal,
    ) -> None:
        super().__init__(f"{window} {measure} budget reached: used {used} of {limit}")
        self.reason = reason
        self.window = window
        self.measure = measure
        self.limit = limit
        self.used = used


_REASONS: dict[tuple[str, str], StopReason] = {
    ("session", "turns"): StopReason.SESSION_TURNS_EXHAUSTED,
    ("session", "tokens"): StopReason.SESSION_TOKENS_EXHAUSTED,
    ("session", "cost"): StopReason.SESSION_COST_EXHAUSTED,
    ("daily", "turns"): StopReason.DAILY_TURNS_EXHAUSTED,
    ("daily", "tokens"): StopReason.DAILY_TOKENS_EXHAUSTED,
    ("daily", "cost"): StopReason.DAILY_COST_EXHAUSTED,
}


class SessionBudget:
    """One session's counters over the shared daily ledger."""

    def __init__(
        self,
        limits: BudgetLimits,
        daily: DailyLedger,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self.limits = limits
        self._daily = daily
        self._clock = clock
        self._usage = Usage()

    @property
    def used(self) -> Usage:
        return self._usage.copy()

    @property
    def daily_used(self) -> Usage:
        return self._daily.used()

    def consume(self, *, turns: int = 0, tokens: int = 0, cost_usd: Decimal = Decimal("0")) -> None:
        self._usage.add(turns=turns, tokens=tokens, cost_usd=cost_usd)
        self._daily.consume(turns=turns, tokens=tokens, cost_usd=cost_usd)

    def guard(self) -> None:
        """Refuse further work when any limit has been reached."""
        session = self._usage
        daily = self._daily.used()
        checks: tuple[tuple[str, str, int | Decimal, int | Decimal], ...] = (
            ("session", "turns", session.turns, self.limits.session_turns),
            ("session", "tokens", session.tokens, self.limits.session_tokens),
            ("session", "cost", session.cost_usd, self.limits.session_cost_usd),
            ("daily", "turns", daily.turns, self.limits.daily_turns),
            ("daily", "tokens", daily.tokens, self.limits.daily_tokens),
            ("daily", "cost", daily.cost_usd, self.limits.daily_cost_usd),
        )
        for window, measure, used, limit in checks:
            if used >= limit:
                raise BudgetExhausted(
                    _REASONS[(window, measure)],
                    window=window,
                    measure=measure,
                    limit=limit,
                    used=used,
                )


__all__ = [
    "BudgetExhausted",
    "BudgetLimits",
    "Clock",
    "DailyLedger",
    "SessionBudget",
    "Usage",
    "utcnow",
]
