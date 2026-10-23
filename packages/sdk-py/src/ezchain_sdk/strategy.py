"""The strategy lifecycle a plugin implements."""

from __future__ import annotations

from typing import Any

from .types import Candle, Fill, Intent, PluginContext, Signal


class Strategy:
    """Base class for every external strategy.

    Subclasses override only the callbacks they need. Every callback returns a list of
    :class:`~ezchain_sdk.Intent`; returning nothing is a hold. The host calls exactly one
    decision callback per tick: ``on_candle`` when the manifest declares a
    ``primary_timeframe`` and a closed bar is available, otherwise ``on_timer``.
    """

    #: Optional human description used by the catalogue when the manifest omits one.
    description: str = ""

    def on_start(self, ctx: PluginContext) -> None:
        """Called once per worker lifetime. Use ``ctx.state`` for replayable state."""

    def on_candle(self, ctx: PluginContext, candle: Candle) -> list[Intent]:
        """Called with the latest closed bar of the manifest's primary timeframe."""
        return []

    def on_timer(self, ctx: PluginContext) -> list[Intent]:
        """Called on the strategy interval when no primary timeframe is declared."""
        return []

    def on_signal(self, ctx: PluginContext, signal: Signal) -> list[Intent]:
        """Called for each routed signal. Signals are not enabled in v1."""
        return []

    def on_fill(self, ctx: PluginContext, fill: Fill) -> None:
        """Called after a fill attributed to this strategy, on the next tick."""

    def on_stop(self, ctx: PluginContext, reason: str) -> None:
        """Called before the worker exits. Best effort; do not rely on it for money."""

    def state(self) -> dict[str, Any]:
        """Optional hook for a strategy that keeps state outside ``ctx.state``.

        The host persists only ``ctx.state``. A strategy that returns state here has it
        merged into the persisted state after each tick.
        """
        return {}


__all__ = ["Strategy"]
