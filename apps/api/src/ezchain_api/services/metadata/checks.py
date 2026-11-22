"""Safety checks derived from an observation.

A check is only ever pass or fail with evidence. A missing input yields ``unknown`` with a
reason, never ``false``: the checks feed the risk gate, and a guessed verdict there is a
fail-open on money. Thresholds are explicit constants so the response can be read against
them without guessing what "safe" meant.
"""

from __future__ import annotations

from decimal import Decimal

from ezchain_adapters.metadata import TokenMetadataObservation

TOP_10_LIMIT_PERCENT = Decimal("30")
DEV_LIMIT_PERCENT = Decimal("5")
TAX_LIMIT_PERCENT = Decimal("10")
FULLY_BURNED_PERCENT = Decimal("100")

REASON_NO_EVIDENCE = "NO_EVIDENCE"
REASON_TOP_10_CONCENTRATED = "TOP_10_ABOVE_LIMIT"
REASON_DEV_CONCENTRATED = "DEV_ABOVE_LIMIT"
REASON_TAXES_HIGH = "TAXES_ABOVE_LIMIT"
REASON_LP_UNPROTECTED = "LP_BURNED_OR_LOCKED"


def _check(
    status: str,
    *,
    value: bool | str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {"status": status, "value": value, "reason": reason}


def check_mint_revoked(observation: TokenMetadataObservation) -> dict[str, object]:
    if observation.mint_revoked is None:
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    return (
        _check("pass", value=True)
        if observation.mint_revoked
        else _check("fail", value=False, reason="MINT_AUTHORITY_PRESENT")
    )


def check_freeze_revoked(observation: TokenMetadataObservation) -> dict[str, object]:
    if observation.freeze_revoked is None:
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    return (
        _check("pass", value=True)
        if observation.freeze_revoked
        else _check("fail", value=False, reason="FREEZE_AUTHORITY_PRESENT")
    )


def check_lp_protected(observation: TokenMetadataObservation) -> dict[str, object]:
    burned = observation.lp_burned_pct
    locked = observation.lp_locked_pct
    duration = observation.lp_lock_duration_seconds
    if burned is not None and burned >= FULLY_BURNED_PERCENT:
        return _check("pass")
    if locked is not None and locked > 0 and duration is not None and duration > 0:
        return _check("pass")
    if locked is not None and locked > 0:
        # A claimed lock without a known positive duration is not evidence of a
        # permanent lock, so the check stays unknown rather than passing on it.
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    if burned is not None and locked is not None:
        return _check("fail", reason=REASON_LP_UNPROTECTED)
    return _check("unknown", reason=REASON_NO_EVIDENCE)


def _threshold_check(
    value: Decimal | None, limit: Decimal, *, fail_reason: str
) -> dict[str, object]:
    if value is None:
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    text = format(value, "f")
    if value < limit:
        return _check("pass", value=text)
    return _check("fail", value=text, reason=fail_reason)


def check_top_10(observation: TokenMetadataObservation) -> dict[str, object]:
    return _threshold_check(
        observation.top_10_pct, TOP_10_LIMIT_PERCENT, fail_reason=REASON_TOP_10_CONCENTRATED
    )


def check_dev(observation: TokenMetadataObservation) -> dict[str, object]:
    return _threshold_check(
        observation.dev_holdings_pct, DEV_LIMIT_PERCENT, fail_reason=REASON_DEV_CONCENTRATED
    )


def check_sells_work(observation: TokenMetadataObservation) -> dict[str, object]:
    if observation.sells_work is None:
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    return (
        _check("pass", value=True)
        if observation.sells_work
        else _check("fail", value=False, reason="SELLS_BLOCKED")
    )


def check_taxes(observation: TokenMetadataObservation) -> dict[str, object]:
    buy = observation.buy_tax_pct
    sell = observation.sell_tax_pct
    if buy is None or sell is None:
        return _check("unknown", reason=REASON_NO_EVIDENCE)
    if buy <= TAX_LIMIT_PERCENT and sell <= TAX_LIMIT_PERCENT:
        return _check("pass")
    return _check("fail", reason=REASON_TAXES_HIGH)


SAFETY_CHECKS = {
    "mint_revoked": check_mint_revoked,
    "freeze_revoked": check_freeze_revoked,
    "lp_burned_or_locked": check_lp_protected,
    "top_10_under_30": check_top_10,
    "dev_under_5": check_dev,
    "sells_work": check_sells_work,
    "buy_sell_tax": check_taxes,
}


def derive_checks(observation: TokenMetadataObservation) -> dict[str, dict[str, object]]:
    return {name: fn(observation) for name, fn in SAFETY_CHECKS.items()}
