"""Persisted operator flags.

The kill switch must survive a restart and a second process, so it cannot live in memory.
It is a tiny JSON row keyed by a stable name; the first read seeds it from settings.
Reads are side-effect free; only an explicit operator mutation writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ezchain_core import utcnow

from ..config import Settings
from ..models import SystemFlagRow

logger = logging.getLogger(__name__)

KILL_SWITCH_KEY = "engine_kill_switch"
KILL_SWITCH_SCOPE = "strategy"


@dataclass(frozen=True)
class KillSwitch:
    active: bool
    reason: str | None
    updated_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        stamp = None
        if self.updated_at is not None:
            stamp = (
                self.updated_at.astimezone(UTC)
                if self.updated_at.tzinfo is not None
                else self.updated_at.replace(tzinfo=UTC)
            ).isoformat()
        return {
            "active": self.active,
            "reason": self.reason,
            "updated_at": stamp,
            "scope": KILL_SWITCH_SCOPE,
        }


def _state(row: SystemFlagRow) -> KillSwitch:
    payload = dict(row.value or {})
    reason = payload.get("reason")
    return KillSwitch(
        active=bool(payload.get("active", False)),
        reason=reason if isinstance(reason, str) else None,
        updated_at=row.updated_at,
    )


def read_kill_switch(session: Session, settings: Settings) -> KillSwitch:
    """Current switch. Absent row falls back to the configured seed without writing."""
    row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
    if row is None:
        return KillSwitch(active=settings.engine_kill_switch, reason=None, updated_at=None)
    return _state(row)


def seed_kill_switch(session: Session, settings: Settings) -> KillSwitch:
    """Persist the seed once so the state survives the next process restart.

    Two processes can boot together and both attempt the first insert. The primary key
    makes that safe: the loser rolls back and reads the winner's row instead of failing
    startup.
    """
    row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
    if row is not None:
        return _state(row)
    try:
        return set_kill_switch(session, active=settings.engine_kill_switch, reason=None)
    except IntegrityError:
        session.rollback()
        logger.info("kill switch seed lost an insert race; reading the winner row")
        row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
        if row is None:  # pragma: no cover - the unique key guarantees a row exists
            raise
        return _state(row)


def set_kill_switch(session: Session, *, active: bool, reason: str | None) -> KillSwitch:
    """Persist a switch change. Returns the auditable state (who is not known yet)."""
    stamp = utcnow()
    row = session.get(SystemFlagRow, KILL_SWITCH_KEY)
    if row is None:
        row = SystemFlagRow(key=KILL_SWITCH_KEY, value={}, updated_at=stamp)
        session.add(row)
    row.value = {"active": bool(active), "reason": reason, "scope": KILL_SWITCH_SCOPE}
    row.updated_at = stamp
    session.commit()
    session.refresh(row)
    logger.info(
        "kill switch %s scope=%s reason=%r updated_at=%s",
        "armed" if active else "released",
        KILL_SWITCH_SCOPE,
        reason,
        stamp.isoformat(),
    )
    return _state(row)


__all__ = [
    "KILL_SWITCH_KEY",
    "KILL_SWITCH_SCOPE",
    "KillSwitch",
    "read_kill_switch",
    "seed_kill_switch",
    "set_kill_switch",
]
