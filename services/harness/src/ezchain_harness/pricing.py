"""What one turn costs, from the operator's rate for the pinned model.

No table of published rates ships here. A baked-in price goes stale silently and a stale
price is a wrong budget, so the rate is a required constructor argument: the operator states
the number the provider bills them at, and :class:`~.budget.ModelUsage` charges the loop with
it. The arithmetic is ``Decimal`` end to end and the result is quantised to six places, which
is the smallest unit the session ledger can see.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.000001")


@dataclass(frozen=True)
class ModelPricing:
    """USD per one million tokens, in and out, for one model id."""

    input_per_million: Decimal
    output_per_million: Decimal

    def __post_init__(self) -> None:
        for name in ("input_per_million", "output_per_million"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be a Decimal")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

    def cost(self, *, prompt_tokens: int, completion_tokens: int) -> Decimal:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token counts cannot be negative")
        exact = (
            Decimal(prompt_tokens) * self.input_per_million
            + Decimal(completion_tokens) * self.output_per_million
        ) / _MILLION
        return exact.quantize(_CENT, rounding=ROUND_HALF_UP)


__all__ = ["ModelPricing"]
