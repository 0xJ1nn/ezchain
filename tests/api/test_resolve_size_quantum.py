"""The resolver must not refuse the size it chose (GH-107).

``resolve_size`` derives a candidate from the percent-of-equity ceiling, then hands it to the
order gate's own sizing check. Rounding that ceiling half-even can move it *above* the true
cap, so the check refused a value the resolver had just computed from that cap, and the caller
got ``SIZE_ABOVE_MAX`` for a request where a legal size plainly existed.
"""

from decimal import Decimal

from ezchain_api.config import Settings
from ezchain_api.services import trade_policy
from ezchain_core.trade_policy import preview_size


def test_a_size_that_rounds_past_its_own_percent_cap_is_not_refused() -> None:
    """Equity 1000.0000007 gives a 5% cap of 50.000000035.

    Half-even to 8dp is 50.00000004, which is above that cap, so the resolver refused its own
    answer. Floored, the size is 50.00000003 and sits inside the cap.
    """
    equity = Decimal("1000.0000007")
    resolved = trade_policy.resolve_size("mid", equity, Settings())

    assert resolved.amount_usd == Decimal("50.00000003")
    assert resolved.amount_usd <= equity * Decimal("0.05")


def test_a_size_genuinely_above_the_cap_is_still_refused() -> None:
    """Flooring the ceiling must not disable the ceiling.

    Only the value the resolver chose itself is exempt, and only because flooring keeps it at
    or below the cap it came from. An amount above the cap is still refused.
    """
    result = preview_size("mid", Decimal("60"), Decimal("1000"))

    assert result.allowed is False
    assert [reason.code for reason in result.reasons] == ["SIZE_ABOVE_MAX"]


def test_the_resolved_size_always_sits_inside_the_cap_it_was_derived_from() -> None:
    """The property, not one case: whatever the equity, the answer is never above its cap.

    Two outcomes are correct and they are the whole contract. Either the resolver returns a
    size that is at or below the cap it was derived from *and* passes the order gate's own
    check, or it refuses with a typed code — the case where the profile's minimum does not fit
    inside the percent cap at all (equity ``1`` against a ``mid`` minimum). What must not
    happen, and did before the floor, is a size chosen from the cap being refused by the cap.
    """
    for equity in (
        Decimal("1"),
        Decimal("999.9999999"),
        Decimal("1000.0000007"),
        Decimal("2000.0000015"),
        Decimal("123456.789012345"),
    ):
        try:
            resolved = trade_policy.resolve_size("mid", equity, Settings())
        except Exception as refused:
            assert "above the configured maximum" in str(refused), (equity, refused)
            continue

        cap = equity * Decimal("0.05")
        assert resolved.amount_usd <= cap, (equity, resolved.amount_usd, cap)

        result = preview_size("mid", resolved.amount_usd, equity)
        assert result.allowed is True, (equity, [r.code for r in result.reasons])
