"""Typed client for the optional in-process PyO3 bridge (ADR-0052 integration contract).

The compiled ``ezchain_bridge`` extension is optional. When it is importable the
ported ``normalize_scopes`` kernel runs in Rust; otherwise the Python original is
used unchanged. The fallback is silent in behaviour but visible through
:func:`using_rust`, so a test or an operator can tell which implementation served a
call without a config flag or telemetry.

The client lives in ``apps/api`` rather than ``packages/core-py``: the fallback calls
the Python original in this same layer, and a core-py module must never import the
application layer. The import of that original is deferred to the fallback branch so
there is no cycle with ``api_keys`` at module load.

The money wrappers preserve the Python originals' signatures: they accept a ``Decimal``
or an ``int``, take ``field`` as a keyword argument where the original does, and return
the same type (``Decimal`` for ``quantize_money``/``floor_money``/``from_minor_units``,
``int`` for ``to_minor_units``). A ``Decimal`` crosses the FFI as its exact string spelling because
the bridge accepts decimal strings or ints, never ``Decimal`` objects; every other type
is handed to the Python original so its own typed refusal decides the call. The string
the bridge returns is parsed back into a ``Decimal``, so the boundary stays typed.

No exception is caught around a bridge call: both paths raise the original exception
class from the owning Python module (``ApiKeyError``, ``MoneyError`` and friends), with
the original message, ``code`` and ``message`` attributes, so callers see one contract
either way.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

_BRIDGE_MODULE = "ezchain_bridge"


def _load_bridge() -> Any:
    """Try to import the compiled extension once. Return ``None`` if it is absent."""
    try:
        return importlib.import_module(_BRIDGE_MODULE)
    except Exception:  # noqa: BLE001 - any load failure means the Python original is used
        return None


_BRIDGE: Any = _load_bridge()


def using_rust() -> bool:
    """Whether the compiled bridge loaded and will serve the typed wrapper."""
    return _BRIDGE is not None


def normalize_scopes(scopes: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise a scope grant, raising ``ApiKeyError`` on refusal (unchanged contract)."""
    if _BRIDGE is not None:
        return cast(tuple[str, ...], _BRIDGE.normalize_scopes(scopes))
    from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes

    return python_normalize_scopes(scopes)


def _bridge_money_arg(value: object) -> str | int | None:
    """The bridge's accepted form of a money value, or ``None`` to use the oracle.

    ``Decimal`` is the type callers use; the bridge takes its exact spelling instead.
    An ``int`` (but not a ``bool``) is already exact and crosses unchanged. Every other
    type is left to the Python original so it raises its own refusal — a string, for
    example, is a valid decimal spelling to the bridge but not to ``money.py``.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def quantize_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed amount to the ledger quantum, half-even (unchanged contract)."""
    if _BRIDGE is not None:
        argument = _bridge_money_arg(value)
        if argument is not None:
            return Decimal(_BRIDGE.quantize_money(argument, field))
    from ezchain_core.money import quantize_money as python_quantize_money

    return python_quantize_money(value, field=field)


def floor_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed amount down to the ledger minor unit (unchanged contract)."""
    if _BRIDGE is not None:
        argument = _bridge_money_arg(value)
        if argument is not None:
            return Decimal(_BRIDGE.floor_money(argument, field))
    from ezchain_core.money import floor_money as python_floor_money

    return python_floor_money(value, field=field)


def to_minor_units(value: object, *, field: str = "amount") -> int:
    """Exact integer count of ledger minor units (unchanged contract)."""
    if _BRIDGE is not None:
        argument = _bridge_money_arg(value)
        if argument is not None:
            return cast(int, _BRIDGE.to_minor_units(argument, field))
    from ezchain_core.money import to_minor_units as python_to_minor_units

    return python_to_minor_units(value, field=field)


def from_minor_units(minor: int) -> Decimal:
    """Inverse scaling of an exact minor-unit count (unchanged contract)."""
    if _BRIDGE is not None:
        argument = _bridge_money_arg(minor)
        if argument is not None:
            return Decimal(_BRIDGE.from_minor_units(argument))
    from ezchain_core.money import from_minor_units as python_from_minor_units

    return python_from_minor_units(minor)


__all__ = [
    "floor_money",
    "from_minor_units",
    "normalize_scopes",
    "quantize_money",
    "to_minor_units",
    "using_rust",
]
