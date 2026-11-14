"""Money helpers: exact Decimal arithmetic in integer minor units; no floats.

Rounding policy (normative, see ``docs/LEDGER.md``):

* The ledger's settlement asset is USD, accounted in **integer minor units** at
  ``MONEY_QUANTUM`` = 1e-8 USD. That is finer than the fiat cent so sub-cent fees and
  long-tail notionals stay exact; every monetary value the ledger writes is a whole
  number of these units.
* A *computed* value (``quantity * price``, a bps fee, a derived cost basis) is rounded
  at the ledger boundary with **half-even**, the IEEE-754/SQL default and the policy in
  ``docs/DOMAIN-MODEL.md``. Rounding happens once, at the boundary, never in a loop.
* An *explicit* value is never silently rounded: a value that is not a whole number of
  minor units raises :class:`SubQuantumError`. Fail closed, never guess.
* Floats are refused at every boundary (:class:`FloatMoneyError`), including
  ``Decimal(str(float))`` which has already lost the exact value.
* Every conversion and computation below sizes its own decimal context from the input's
  digit count. The ambient context (28 significant digits by default) is never trusted:
  a value that does not fit is a refusal, never a rounded answer. A bare ``a * b`` is
  itself a rounding operation at that ambient precision, so a product is always
  :func:`exact_product`: quantizing the ambient product afterwards cannot recover the
  digits the multiply already dropped.

The pre-trade cost preview lives here too, not at the edge: the web preview and the
journal must agree on one cost model (CONTRACT I1/I7), so ``preview_cost`` produces
the exact fill fee the ledger journals for the same quote. The web fixture mirrors
this module; it never invents a second fee formula.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, Overflow, localcontext
from typing import Any

from .types import SourceRef

BPS = Decimal(10_000)
# Dividing by 10_000 shifts the exponent by four; an exact bps quotient needs that many
# extra digits on top of the product's own.
BPS_DIGITS = 4
# The ledger's minimum monetary unit: 1e-8 USD. The database type is NUMERIC(38, 8);
# this constant is the one home for that scale.
MONEY_SCALE = 8
MONEY_QUANTUM = Decimal(1).scaleb(-MONEY_SCALE)
ROUNDING = ROUND_HALF_EVEN
# Computation precision for quantising/summing: wide enough for the 38-digit column.
_MONEY_PRECISION = 60
# The slippage window every executable quote is requested with. Quotes are requested
# at this window (adapters default to it), so the preview can quote the receiver's
# worst case inside the window without another provider round-trip.
DEFAULT_SLIPPAGE_BPS = 50
# The non-executable DexScreener mark kind. Mirrors the adapters' PAPER_MARK literal;
# core imports nothing from the adapters, so the value is duplicated here by necessity.
PAPER_MARK = "paper_mark"


class MoneyError(ValueError):
    """A value that cannot enter the ledger as exact money."""


class FloatMoneyError(MoneyError):
    """A float reached a money boundary; floats are never ledger money."""


class SubQuantumError(MoneyError):
    """An explicit value is not a whole number of ledger minor units."""


def exact_precision(*values: Decimal, extra: int = 0) -> int:
    """The decimal precision needed to compute on ``values`` without rounding.

    Decimal arithmetic obeys the *ambient* context, which defaults to 28 significant
    digits. A notional, a bps product or a long sum can carry more digits than that, and
    anything computed under the cap is silently rounded — a conversion that looks exact
    and is not. Every exact path in this package sizes its own local context with this
    helper instead of trusting the ambient default.
    """
    needed = _MONEY_PRECISION + extra
    for value in values:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int):
            raise MoneyError("exact_precision needs a finite Decimal")
        needed += len(value.as_tuple().digits) + max(exponent, 0)
    return needed


def _as_decimal(value: object, *, field: str) -> Decimal:
    """Reject floats and non-finite values; accept exact Decimal/int inputs."""
    if isinstance(value, bool):
        raise MoneyError(f"{field} must be exact Decimal, not bool")
    if isinstance(value, float):
        raise FloatMoneyError(
            f"{field} must be exact Decimal, not float; a float has already lost the amount"
        )
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise MoneyError(f"{field} must be Decimal or int, not {type(value).__name__}")
    if not value.is_finite():
        raise MoneyError(f"{field} must be finite")
    return value


def quantize_money(value: object, *, field: str = "amount") -> Decimal:
    """Round a computed USD amount to the ledger minor unit, half-even."""
    amount = _as_decimal(value, field=field)
    with localcontext() as context:
        context.prec = exact_precision(amount, extra=MONEY_SCALE)
        try:
            return amount.quantize(MONEY_QUANTUM, rounding=ROUNDING)
        except InvalidOperation as exc:  # pragma: no cover - only absurd exponents
            raise MoneyError(f"{field} is outside the supported money range") from exc


def require_money(value: object, *, field: str = "amount") -> Decimal:
    """Return ``value`` only if it is already a whole number of minor units.

    Explicit values are never silently rounded: a sub-minor amount is a caller bug,
    not something the ledger may absorb.
    """
    amount = _as_decimal(value, field=field)
    if quantize_money(amount, field=field) != amount:
        raise SubQuantumError(
            f"{field}={amount} is not a whole number of {MONEY_QUANTUM} minor units"
        )
    return amount


def exact_neg(value: Decimal) -> Decimal:
    """Negate a money/quantity Decimal without touching the ambient context.

    ``-value`` on a ``Decimal`` is a *rounding* operation: it applies the current context,
    so a 29-significant-digit quantity silently loses its last digit when the journal
    writes the -quantity leg. ``copy_negate`` flips the sign on the digits as they are.
    """
    if not isinstance(value, Decimal):
        raise MoneyError(f"exact_neg needs a Decimal, not {type(value).__name__}")
    return value.copy_negate()


def exact_sum(values: Iterable[object]) -> Decimal:
    """Sum money/quantity Decimals *exactly*, with no ambient-precision rounding.

    Addition is exact when the context has room for every operand's integer digits and
    the widest fractional part, plus the carry of the count. The precision is derived
    from the operands; the ambient 28-digit default is never used for a sum, because a
    long tape would otherwise leave a phantom residue the ledger would then have to
    compare or explain.
    """
    items = [
        item if isinstance(item, Decimal) else _as_decimal(item, field="sum") for item in values
    ]
    if not items:
        return Decimal(0)
    exponents: list[int] = []
    integer_digits = 1
    for item in items:
        exponent = item.as_tuple().exponent
        if not isinstance(exponent, int):
            raise MoneyError("exact_sum needs finite Decimals")
        exponents.append(exponent)
        integer_digits = max(integer_digits, len(item.as_tuple().digits) + max(exponent, 0))
    scale = max(0, -min(exponents))
    with localcontext() as context:
        context.prec = integer_digits + scale + len(str(len(items))) + 2
        return sum(items, Decimal(0))


def exact_product(*values: object) -> Decimal:
    """Multiply Decimals exactly, with no ambient-precision rounding.

    ``quantity * price_usd`` under the ambient 28-digit context is rounded *before* the
    quantize that follows it, so the journal books a double-rounded value: at 22 integer
    digits the ambient product hides thirteen minor units. The precision here is derived
    from the operands — a product of factors with ``m`` and ``n`` significant digits has at
    most ``m + n`` — so the multiplication itself never rounds and the caller's
    ``quantize_money`` is the only rounding the value ever sees.
    """
    factors = [_as_decimal(value, field="factor") for value in values]
    if not factors:
        raise MoneyError("exact_product needs at least one factor")
    significant = sum(len(factor.as_tuple().digits) for factor in factors)
    with localcontext() as context:
        context.prec = significant + len(factors) + 2
        product = Decimal(1)
        for factor in factors:
            product = product * factor
    return product


def require_quantity(value: object, *, field: str = "quantity") -> Decimal:
    """Validate a token quantity: exact and finite, but not restricted to USD scale."""
    return _as_decimal(value, field=field)


def to_minor_units(value: object, *, field: str = "amount") -> int:
    """Exact integer count of ledger minor units. The comparison unit for money.

    The conversion runs in a local context sized from the input's own digits, so a
    notional with more than 28 significant digits is never silently rounded by the
    ambient display precision. A value that does not land on a whole minor unit fails
    closed with :class:`SubQuantumError` instead of being rounded, and the round trip
    back through :func:`from_minor_units` proves the integer is the same number.
    """
    amount = require_money(value, field=field)
    with localcontext() as context:
        context.prec = exact_precision(amount, extra=MONEY_SCALE)
        try:
            scaled = amount.scaleb(MONEY_SCALE)
        except (InvalidOperation, Overflow) as exc:  # pragma: no cover - absurd exponents
            raise MoneyError(f"{field}={amount} is outside the supported money range") from exc
        if not scaled.is_finite() or scaled != scaled.to_integral_value():
            raise SubQuantumError(f"{field}={amount} is not a whole number of minor units")
        minor = int(scaled)
    if from_minor_units(minor) != amount:
        raise SubQuantumError(f"{field}={amount} does not survive the minor-unit conversion")
    return minor


def from_minor_units(minor: int) -> Decimal:
    """Inverse of :func:`to_minor_units`, for reporting and tests."""
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise MoneyError(f"minor units must be int, not {type(minor).__name__}")
    with localcontext() as context:
        context.prec = exact_precision(Decimal(minor), extra=MONEY_SCALE)
        return Decimal(minor).scaleb(-MONEY_SCALE)


def bps_fee(notional_usd: Decimal, fee_bps: int) -> Decimal:
    if fee_bps < 0:
        raise ValueError("fee_bps cannot be negative")
    notional = _as_decimal(notional_usd, field="notional_usd")
    with localcontext() as context:
        # The bps product carries digits beyond either input; size the context from both
        # so a long-tail notional is not rounded by the ambient display precision.
        context.prec = exact_precision(notional, Decimal(fee_bps), extra=MONEY_SCALE + BPS_DIGITS)
        fee = notional * Decimal(fee_bps) / BPS
    return quantize_money(fee, field="fee_usd")


@dataclass(frozen=True)
class CostPreview:
    """The full pre-trade cost sheet for one quote, as the journal will see it.

    ``platform_fee_usd`` is always zero (self-hosted: the software takes no cut).
    ``venue_fee_usd`` is the quote's own provider cost. ``network_fee_usd`` is null:
    no adapter breaks gas or priority cost out in USD, so the sheet prints an em
    dash — an unknown, never a zero. ``worst_case_out`` is the receiver's minimum
    amount inside the slippage window; it is null for a paper mark, which has no
    venue to guarantee any window. ``fill_fee_usd`` is the fee the ledger journals.
    """

    platform_fee_usd: Decimal
    venue_fee_usd: Decimal
    network_fee_usd: Decimal | None
    price_impact_pct: Decimal | None
    slippage_bps: int | None
    worst_case_out: Decimal | None
    fill_fee_usd: Decimal
    source: SourceRef

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform_fee_usd": str(self.platform_fee_usd),
            "venue_fee_usd": str(self.venue_fee_usd),
            "network_fee_usd": None if self.network_fee_usd is None else str(self.network_fee_usd),
            "price_impact_pct": None
            if self.price_impact_pct is None
            else str(self.price_impact_pct),
            "slippage_bps": self.slippage_bps,
            "worst_case_out": None if self.worst_case_out is None else str(self.worst_case_out),
            "fill_fee_usd": str(self.fill_fee_usd),
            "source": self.source.to_dict(),
        }


def preview_cost(
    *,
    kind: str,
    notional_usd: Decimal,
    venue_fee_usd: Decimal,
    price_impact_pct: Decimal | None,
    amount_out: Decimal | None,
    fee_bps: int,
    source: SourceRef,
) -> CostPreview:
    """Compute the cost sheet for one quote. Pure: no I/O, no settings.

    ``fill_fee_usd`` is the journal rule: a paper mark pays the flat bps fee; an
    executable quote pays the larger of the bps leg and its own venue cost, never
    their sum (the venue already prices pool fees and gas). The venue leg is a
    computed receipt figure, so it is quantized half-even at the ledger boundary
    *before* the comparison: ``fill_fee_usd`` is then exactly the fee the journal
    books, never a value the journal would round down by a minor unit. The echoed
    ``venue_fee_usd`` is the quote's own reported cost, unchanged — a venue that
    reports a sub-minor fee shows it, while the journal books the quantized figure.
    ``worst_case_out`` is the expected out less the slippage window, rounded like
    every other money figure; it is null when there is no window to guarantee.
    """
    # The float guard belongs at the argument, not only downstream: a caller that passes
    # ``0.5`` must get ``FloatMoneyError`` here, where the field name is known, instead of
    # a journal boundary refusing a preview's fill fee later.
    notional = _as_decimal(notional_usd, field="notional_usd")
    venue_fee = _as_decimal(venue_fee_usd, field="venue_fee_usd")
    venue_leg = quantize_money(venue_fee, field="venue_fee_usd")
    if notional <= 0:
        raise ValueError("notional must be positive")
    paper_leg = bps_fee(notional, fee_bps)
    fill_fee = paper_leg if kind == PAPER_MARK else max(paper_leg, venue_leg)
    slippage_bps = None if kind == PAPER_MARK else DEFAULT_SLIPPAGE_BPS
    worst_case_out: Decimal | None = None
    if slippage_bps is not None and amount_out is not None:
        amount = _as_decimal(amount_out, field="amount_out")
        with localcontext() as context:
            # The window product needs the amount's own digits plus the bps shift; the
            # ambient 28-digit default would round a long-tail quantity silently.
            context.prec = exact_precision(amount, extra=MONEY_SCALE + BPS_DIGITS)
            inside_window = amount * (BPS - Decimal(slippage_bps)) / BPS
        worst_case_out = quantize_money(inside_window, field="worst_case_out")
    return CostPreview(
        platform_fee_usd=Decimal(0),
        venue_fee_usd=venue_fee,
        network_fee_usd=None,
        price_impact_pct=price_impact_pct,
        slippage_bps=slippage_bps,
        worst_case_out=worst_case_out,
        fill_fee_usd=fill_fee,
        source=source,
    )
