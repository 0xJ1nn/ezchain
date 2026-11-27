"""Build the metadata response from an observation without inventing values.

Every displayed number is wrapped with ``known``, ``unit``, ``source`` and ``observed_at``.
An unknown keeps ``value`` null and still names the source that was asked, so "the provider
answered without this field" stays distinct from "no provider was asked". A window shorter
than the pair's age is suppressed entirely: showing activity from before the pair existed
would be a claim the evidence cannot support.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ezchain_adapters.metadata import (
    WINDOW_SECONDS,
    WINDOWS,
    TokenMetadataObservation,
    WindowFacts,
)

from .checks import derive_checks

CHECK_GROUPS: dict[str, str] = {
    "mint_revoked": "rpc",
    "freeze_revoked": "rpc",
    "lp_burned_or_locked": "liquidity",
    "top_10_under_30": "holders",
    "dev_under_5": "holders",
    "sells_work": "safety",
    "buy_sell_tax": "safety",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    ).isoformat()


def _source(source: Any) -> dict[str, Any] | None:
    return None if source is None else source.to_dict()


def _metric(value: Any, unit: str, source: Any) -> dict[str, Any]:
    text: str | bool | None
    if isinstance(value, Decimal):
        text = format(value, "f") if value.is_finite() else None
    elif isinstance(value, bool):
        text = value
    elif value is not None:
        text = str(value)
    else:
        text = None
    return {
        "value": text,
        "known": text is not None,
        "unit": unit,
        "source": _source(source),
        "observed_at": _iso(source.observed_at) if source is not None else None,
    }


def _pair_age(observation: TokenMetadataObservation) -> int | None:
    source = observation.source
    if observation.pair_created_at is None or source is None:
        return None
    created = observation.pair_created_at
    seen = source.observed_at
    created = created.astimezone(UTC) if created.tzinfo else created.replace(tzinfo=UTC)
    seen = seen.astimezone(UTC) if seen.tzinfo else seen.replace(tzinfo=UTC)
    delta = (seen - created).total_seconds()
    return int(delta) if delta >= 0 else None


def _window(
    facts: WindowFacts, *, group_source: Any, suppressed: bool
) -> dict[str, dict[str, Any]]:
    def build(value: Any, unit: str) -> dict[str, Any]:
        return _metric(None if suppressed else value, unit, group_source)

    bsr: Decimal | None = None
    if not suppressed and facts.buys is not None and facts.sells:
        bsr = Decimal(facts.buys) / Decimal(facts.sells)
    return {
        "price_change_pct": build(facts.price_change_pct, "percent"),
        "buys": build(facts.buys, "count"),
        "sells": build(facts.sells, "count"),
        "buy_volume_usd": build(facts.buy_volume_usd, "usd"),
        "sell_volume_usd": build(facts.sell_volume_usd, "usd"),
        "buying_makers": build(facts.buying_makers, "count"),
        "selling_makers": build(facts.selling_makers, "count"),
        "bsr": build(bsr, "ratio"),
    }


def build_response(observation: TokenMetadataObservation) -> dict[str, Any]:
    """Derive the full response, including the fail-closed checks and any reason codes."""
    degraded = list(observation.degraded)
    market_source = observation.source_for("market")
    liquidity_source = observation.source_for("liquidity")
    holders_source = observation.source_for("holders")
    windows_source = observation.source_for("windows")
    safety_source = observation.source_for("safety")
    age = _pair_age(observation)

    windows: dict[str, Any] = {}
    for window in WINDOWS:
        suppressed = age is not None and age < WINDOW_SECONDS[window]
        if suppressed:
            degraded.append(f"WINDOW_EXCEEDS_PAIR_AGE:{window}")
        windows[window] = _window(
            observation.windows.get(window, WindowFacts()),
            group_source=observation.source_for(f"windows:{window}") or windows_source,
            suppressed=suppressed,
        )

    checks: dict[str, Any] = {}
    for name, check in derive_checks(observation).items():
        source = observation.source_for(CHECK_GROUPS[name])
        with_source = dict(check)
        with_source["source"] = _source(source)
        with_source["observed_at"] = _iso(source.observed_at) if source is not None else None
        checks[name] = with_source

    lp_burned = observation.lp_burned_pct
    lp_locked = observation.lp_locked_pct
    if lp_burned is not None and lp_burned >= Decimal("100"):
        lp_state = "burned"
    elif lp_locked is not None and lp_locked > 0:
        lp_state = "locked"
    elif lp_burned is not None or lp_locked is not None:
        lp_state = "unlocked"
    else:
        lp_state = None

    return {
        "chain": observation.chain,
        "address": observation.address,
        "symbol": _metric(observation.symbol, "text", market_source),
        "name": _metric(observation.name, "text", market_source),
        "market": {
            "price_usd": _metric(observation.price_usd, "usd", market_source),
            "market_cap_usd": _metric(observation.market_cap_usd, "usd", market_source),
            "fdv_usd": _metric(observation.fdv_usd, "usd", market_source),
            "liquidity_usd": _metric(observation.liquidity_usd, "usd", market_source),
            "liquidity_change_pct": _metric(
                observation.liquidity_change_pct, "percent", market_source
            ),
        },
        "liquidity": {
            "state": _metric(lp_state, "state", liquidity_source),
            "lp_burned_pct": _metric(lp_burned, "percent", liquidity_source),
            "lp_locked_pct": _metric(lp_locked, "percent", liquidity_source),
            "lp_lock_duration_seconds": _metric(
                observation.lp_lock_duration_seconds, "seconds", liquidity_source
            ),
        },
        "holders": {
            "count": _metric(observation.holders, "count", holders_source),
            "change_h1_pct": _metric(observation.holders_change_h1_pct, "percent", holders_source),
            "top_10_pct": _metric(observation.top_10_pct, "percent", holders_source),
            "dev_holdings_pct": _metric(observation.dev_holdings_pct, "percent", holders_source),
        },
        "pair_age_seconds": _metric(age, "seconds", market_source),
        "windows": windows,
        "safety": {
            "checks": checks,
            "buy_tax_pct": _metric(observation.buy_tax_pct, "percent", safety_source),
            "sell_tax_pct": _metric(observation.sell_tax_pct, "percent", safety_source),
        },
        "degraded": list(dict.fromkeys(degraded)),
    }
