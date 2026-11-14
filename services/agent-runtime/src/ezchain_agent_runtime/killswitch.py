"""The runtime's own kill switch.

Arming it is an operator act, not an agent tool. The loop reads :attr:`KillSwitch.armed`
before every model turn and before every tool call, so an armed switch stops a running
session at the next step — within one tool call.
"""

from __future__ import annotations

from typing import Protocol


class KillSwitch(Protocol):
    @property
    def armed(self) -> bool: ...

    @property
    def reason(self) -> str: ...


class InMemoryKillSwitch:
    def __init__(self, *, armed: bool = False, reason: str = "") -> None:
        self._armed = armed
        self._reason = reason

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def reason(self) -> str:
        return self._reason

    def arm(self, reason: str = "") -> None:
        self._armed = True
        self._reason = reason

    def release(self) -> None:
        self._armed = False
        self._reason = ""


__all__ = ["InMemoryKillSwitch", "KillSwitch"]
