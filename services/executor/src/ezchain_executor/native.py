"""Typed client for the in-process PyO3 bridge, executor side (ADR-0052).

The executor is a separately deployed service, so it cannot import ``ezchain_api.native``:
that would reverse the service boundary. This is the executor's own probe of the same
``ezchain_bridge`` extension, kept deliberately small — only ``quantize_money``, the one
money kernel the executor calls. The extension is a prerequisite, not an optional
accelerator: it is probed once at import and, if absent, every wrapper raises
:class:`NativeBridgeUnavailable` naming ``bash tools/build-native.sh``. There is no
Python fallback on this money path. ``using_rust()`` remains as a diagnostic only.

The wrapper preserves ``ezchain_core.money.quantize_money``'s contract: it accepts a
``Decimal`` or an ``int`` and a keyword ``field``, and returns a ``Decimal``. A
``Decimal`` crosses the FFI as its exact string spelling; every other value goes to the
bridge unchanged so its own typed refusal decides the call. No exception is caught
around the call: bridge-originated errors keep the original class, message, ``code`` and
``message`` attributes from ``ezchain_core.money``.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

_BRIDGE_MODULE = "ezchain_bridge"

_UNAVAILABLE_MESSAGE = (
    "ezchain_bridge native extension is unavailable; build and install it with "
    "`bash tools/build-native.sh` before running the app or the gate"
)


class NativeBridgeUnavailable(RuntimeError):
    """The compiled bridge is missing; no Python fallback will silently take over."""


_BRIDGE_ERROR: BaseException | None = None


def _load_bridge() -> Any:
    """Import the compiled extension once; return ``None`` and record why on failure."""
    global _BRIDGE_ERROR
    try:
        return importlib.import_module(_BRIDGE_MODULE)
    except Exception as exc:  # noqa: BLE001 - any load failure leaves the client fail-closed
        _BRIDGE_ERROR = exc
        return None


_BRIDGE: Any = _load_bridge()


def _require_bridge() -> Any:
    """Return the loaded bridge, or raise the typed fail-closed error."""
    if _BRIDGE is None:
        detail = f" ({_BRIDGE_ERROR})" if _BRIDGE_ERROR is not None else ""
        raise NativeBridgeUnavailable(_UNAVAILABLE_MESSAGE + detail)
    return _BRIDGE


def using_rust() -> bool:
    """Whether the compiled bridge loaded and will serve the typed wrapper."""
    return _BRIDGE is not None


def _bridge_money_arg(value: object) -> str | int | object:
    """The bridge's accepted form of a money value (no Python original to fall back to)."""
    if isinstance(value, Decimal):
        return str(value)
    return value


def quantize_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed amount to the ledger quantum, half-even (unchanged contract)."""
    return Decimal(_require_bridge().quantize_money(_bridge_money_arg(value), field))


__all__ = ["NativeBridgeUnavailable", "quantize_money", "using_rust"]
