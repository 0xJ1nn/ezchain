"""The operator kill switch, checked by the executor before every submission.

The check is fail closed: if the switch cannot be read, the executor refuses. A manual
exit is not a strategy order, but the switch is still consulted by the caller that owns
the policy; this object only reports what was read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


class KillSwitchUnavailable(RuntimeError):
    """The kill switch state could not be read; execution must refuse."""

    code = "KILL_SWITCH_UNAVAILABLE"


def _unreadable_switch() -> bool:
    """The default reader: without a persistence reader the state cannot be read.

    A caller that wires no reader must not be taken as an operator who left the switch
    off. Refusing is the only safe answer, because an unreadable switch could be an
    armed one the executor cannot see.
    """
    raise KillSwitchUnavailable("no kill switch reader is configured")


@dataclass(frozen=True)
class KillSwitch:
    """A fresh read of the persisted operator switch.

    ``read`` is called on every decision, so arming takes effect on the next order
    without a restart. It is expected to raise when the state is unreadable; this class
    turns any failure into :class:`KillSwitchUnavailable` instead of assuming "off".
    With no reader supplied the switch is unreadable rather than off, so a pipeline
    wired without persistence fails closed instead of approving new risk.
    """

    read: Callable[[], bool] = field(default=_unreadable_switch)

    def active(self) -> bool:
        try:
            return bool(self.read())
        except Exception as exc:  # noqa: BLE001 - any read failure is fail-closed
            raise KillSwitchUnavailable("kill switch state is unreadable") from exc

    def check(self) -> None:
        """Raise when execution must not proceed."""
        if self.active():
            raise KillSwitchActive("kill switch is active")


class KillSwitchActive(RuntimeError):
    """The operator switch is armed; no new order may be submitted."""

    code = "KILL_SWITCH_ACTIVE"


__all__ = ["KillSwitch", "KillSwitchActive", "KillSwitchUnavailable"]
