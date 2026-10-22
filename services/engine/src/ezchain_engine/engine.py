"""Interval gate for strategy evaluation. The pure decision lives in ezchain_core."""

from __future__ import annotations

from datetime import datetime, timedelta


def should_run(last_run_at: datetime | None, now: datetime, interval_minutes: float) -> bool:
    if last_run_at is None:
        return True
    return now - last_run_at >= timedelta(minutes=interval_minutes)
