"""Turns, tokens and cost — per session and per day.

The daily counters belong to the process, not the session: a new session starts with a fresh
session budget and inherits the day's spend for its agent. Reaching a limit refuses the next
turn or tool call with a typed :class:`StopReason`, never a silent clamp; crossing one on the
turn that produced the answer stops the session instead of delivering it.
"""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
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


# The process-wide day, keyed by (agent, UTC date). A loop is built per session, so the
# ledger cannot live on the loop: three sessions in one day must share one agent budget.
PROCESS_SPEND: dict[tuple[str, date], Usage] = {}


class DailyLedger:
    """One agent's spend for the current UTC day, shared by every session in the process.

    The counters live in :data:`PROCESS_SPEND` rather than on this object, so building a new
    loop for a new session does not reset the day. Keys are ``(agent, UTC date)``: two agents
    have two budgets, and a new day starts at zero.

    The default store is process memory. A runtime run as several processes hands in a
    persisted mapping with the same ``consume``/``used`` contract; the key is the row key.
    """

    def __init__(
        self,
        *,
        agent: str = "default",
        clock: Clock = utcnow,
        spend: MutableMapping[tuple[str, date], Usage] | None = None,
    ) -> None:
        if not agent:
            raise ValueError("a daily ledger needs an agent identity")
        self._agent = agent
        self._clock = clock
        self._spend = PROCESS_SPEND if spend is None else spend

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def day(self) -> date:
        return self._clock().date()

    def _usage(self) -> Usage:
        return self._spend.setdefault((self._agent, self.day), Usage())

    def used(self) -> Usage:
        return self._usage().copy()

    def consume(self, *, turns: int = 0, tokens: int = 0, cost_usd: Decimal = Decimal("0")) -> None:
        self._usage().add(turns=turns, tokens=tokens, cost_usd=cost_usd)


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

    def _reached(self, *, inclusive: bool) -> BudgetExhausted | None:
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
            if (used >= limit) if inclusive else (used > limit):
                return BudgetExhausted(
                    _REASONS[(window, measure)],
                    window=window,
                    measure=measure,
                    limit=limit,
                    used=used,
                )
        return None

    def guard(self) -> None:
        """Refuse further work when any limit has been reached."""
        exhausted = self._reached(inclusive=True)
        if exhausted is not None:
            raise exhausted

    def overtaken(self) -> BudgetExhausted | None:
        """The limit this last consumption crossed, if any.

        Reaching a limit exactly is a finished budget, not a refusal: the turn that spent
        the last cent may still deliver its answer. Crossing it is a refusal, so the answer
        that was bought by the overrun is dropped and the session stops with a typed reason.
        """
        return self._reached(inclusive=False)


__all__ = [
    "BudgetExhausted",
    "BudgetLimits",
    "Clock",
    "DailyLedger",
    "PROCESS_SPEND",
    "SessionBudget",
    "Usage",
    "utcnow",
]
