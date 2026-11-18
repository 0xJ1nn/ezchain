"""Typed client for the optional in-process PyO3 bridge, executor side (ADR-0052).

The executor is a separately deployed service, so it cannot import ``ezchain_api.native``:
that would reverse the service boundary. This is the executor's own probe of the same
optional ``ezchain_bridge`` extension, kept deliberately small — only ``quantize_money``,
the one money kernel the executor calls. When the extension is importable the ported
Rust kernel serves the call; otherwise the Python original in ``ezchain_core.money`` is
used unchanged. The fallback is silent in behaviour but visible through
:func:`using_rust`, so a test or an operator can tell which implementation served a call.

The wrapper preserves ``ezchain_core.money.quantize_money``'s contract: it accepts a
``Decimal`` or an ``int`` and a keyword ``field``, and returns a ``Decimal``. A ``Decimal``
crosses the FFI as its exact string spelling because the bridge accepts decimal strings
or ints, never ``Decimal`` objects; every other type is handed to the Python original so
its own typed refusal decides the call. The returned string is parsed back into a
``Decimal``. No exception is caught around a bridge call: both paths raise the original
exception class from ``ezchain_core.money``, with its original message, ``code`` and
``message`` attributes.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

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


def _bridge_money_arg(value: object) -> str | int | None:
    """The bridge's accepted form of a money value, or ``None`` to use the oracle."""
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


__all__ = ["quantize_money", "using_rust"]
