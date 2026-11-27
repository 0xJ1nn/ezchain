"""Shared value types for the pure trust-boundary kernels.

Everything here is data and pure functions: no keys, no I/O, no clock, no venue.
The ``Refusal`` is the typed result a caller branches on; the decimal helpers are the
only arithmetic-adjacent code in the trust package and they parse and re-spell exact
values without ever rounding, multiplying or adding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

# The nine fields that are signed, bound and compared. One order for the digest, the
# binding check and the wire form, so the three can never disagree about what a field is.
BINDING_FIELDS: Final[tuple[str, ...]] = (
    "chain",
    "cluster",
    "mint",
    "side",
    "amount",
    "minimum_output",
    "recipient",
    "program_id",
    "idempotency_key",
)


@dataclass(frozen=True)
class Refusal:
    """A typed reason to stop before a signer is reached.

    ``code`` is always a string from ``ezchain_core.reason_codes``; ``check`` names the
    check that found it (``binding.<field>`` or a preflight check name) and ``detail``
    is operator context, never part of the code.
    """

    code: str
    check: str | None = None
    detail: str | None = None

    @property
    def message(self) -> str:
        """Display text from the one taxonomy, never a locally invented string."""
        from ..reason_codes import message_for

        return message_for(self.code)


def parse_exact_decimal(value: str | Decimal) -> Decimal:
    """Parse a money field without loss: strings and ``Decimal`` only, never float.

    A binary float is rejected because it cannot represent most decimal tickets exactly.
    Non-finite values are rejected because "unknown" must never become a zero or an
    infinity that passes a bound. No rounding or arithmetic takes place here.
    """
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError("a money field must be a decimal string or Decimal, never a float")
    try:
        parsed = Decimal(value) if isinstance(value, str) else value
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("a money field is not a valid decimal") from None
    if not parsed.is_finite():
        raise ValueError("a money field must be finite")
    return parsed


def canonical_decimal(value: Decimal) -> str:
    """One spelling per value, so ``1.50``, ``1.5`` and ``1.500`` bind alike.

    Plain (non-scientific) notation, no trailing fractional zeros, no negative zero.
    This is a re-spelling of an exact value, not money arithmetic.
    """
    if not value.is_finite():
        raise ValueError("a bound decimal must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0", "+0") else text


__all__ = [
    "BINDING_FIELDS",
    "Refusal",
    "canonical_decimal",
    "parse_exact_decimal",
]
