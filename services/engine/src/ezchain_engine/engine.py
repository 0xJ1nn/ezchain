"""Interval gate for strategy evaluation. The pure decision lives in ezchain_core."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ezchain_core.strategy import interval_minutes, validate_params
from ezchain_core.types import StrategyKind


def _aware(value: datetime) -> datetime:
    """Postgres returns tz-aware values; tests and fixtures may use naive UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def should_run(last_run_at: datetime | None, now: datetime, interval: int) -> bool:
    """True when the strategy's interval has elapsed.

    The gate is inclusive: exactly one interval after the last decision is allowed. A clock
    that moved backwards never opens the gate.
    """
    if last_run_at is None:
        return True
    return _aware(now) - _aware(last_run_at) >= timedelta(minutes=interval)


def strategy_interval_minutes(kind: StrategyKind, params: dict[str, Any] | None) -> int:
    """The strategy's own cooldown, validated once per run."""
    return interval_minutes(kind, params)


def validate_strategy_params(kind: StrategyKind, params: dict[str, Any] | None) -> None:
    """Raise ``StrategyValidationError`` when params do not match the kind."""
    validate_params(kind, params)
