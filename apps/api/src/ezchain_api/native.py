"""Typed client for the in-process PyO3 bridge (ADR-0052 integration contract).

The compiled ``ezchain_bridge`` extension is a prerequisite, not an optional
accelerator. It is probed once at import; if it is absent the failure is recorded and
every wrapper raises :class:`NativeBridgeUnavailable` with the command that builds it
(``bash tools/build-native.sh``). There is no Python fallback: a Rust-owned layer that
silently ran the Python original hid a missing build artifact behind correct-looking
results, which is unacceptable on a money path. ``using_rust()`` remains as a diagnostic
so an operator can confirm which backend would serve a call.

The client stays in ``apps/api`` rather than ``packages/core-py`` because the money and
scope contracts it wraps belong to this application layer; a core-py module must never
import the application layer. No wrapper imports the Python originals — doing so would
reintroduce the fallback this module exists to remove.

The money wrappers preserve the Python originals' signatures: they accept a ``Decimal``
or an ``int``, take ``field`` as a keyword argument, and return the same type. A
``Decimal`` crosses the FFI as its exact string spelling because the bridge accepts
decimal strings or ints, never ``Decimal`` objects. Every other value is passed to the
bridge unchanged so the bridge's own typed refusals decide the call. No exception is
caught around a bridge call: bridge-originated errors keep the original exception
class, message, ``code`` and ``message`` attributes, so callers see one contract.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

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


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise a scope grant, raising ``ApiKeyError`` on refusal (unchanged contract)."""
    return cast(tuple[str, ...], _require_bridge().normalize_scopes(scopes))


def _bridge_money_arg(value: object) -> str | int | object:
    """The bridge's accepted form of a money value.

    ``Decimal`` is the type callers use; the bridge takes its exact spelling instead.
    Every other value (including a float or a bool) is handed to the bridge unchanged so
    its original typed refusal decides the call. There is no Python original to fall
    back to.
    """
    if isinstance(value, Decimal):
        return str(value)
    return value


def quantize_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed amount to the ledger quantum, half-even (unchanged contract)."""
    return Decimal(_require_bridge().quantize_money(_bridge_money_arg(value), field))


def floor_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed amount down to the ledger minor unit (unchanged contract)."""
    return Decimal(_require_bridge().floor_money(_bridge_money_arg(value), field))


def to_minor_units(value: object, *, field: str = "amount") -> int:
    """Exact integer count of ledger minor units (unchanged contract)."""
    return cast(int, _require_bridge().to_minor_units(_bridge_money_arg(value), field))


def from_minor_units(minor: int) -> Decimal:
    """Inverse scaling of an exact minor-unit count (unchanged contract)."""
    return Decimal(_require_bridge().from_minor_units(_bridge_money_arg(minor)))


__all__ = [
    "NativeBridgeUnavailable",
    "floor_money",
    "from_minor_units",
    "normalize_scopes",
    "quantize_money",
    "to_minor_units",
    "using_rust",
]
