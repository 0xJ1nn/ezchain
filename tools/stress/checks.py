"""Pure invariant checkers for the PAPER stress harness.

Every function takes plain JSON-shaped data and returns :class:`Finding` values. There is
no I/O here, so the checks are unit-testable without a running API. Fingerprints and issue
payloads live in :mod:`tools.stress.issues`; a finding carries only stable identifiers,
expected/actual text and optional evidence.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

TOLERANCE = Decimal("0.01")
# Position quantity is persisted at 8 decimal places; anything smaller is rounding.
QUANTITY_TOLERANCE = Decimal("0.00000001")

ALLOWED_ORDER_STATUSES = frozenset({"pending", "filled", "rejected", "cancelled", "expired"})
ALLOWED_ORDER_SIDES = frozenset({"buy", "sell"})

# The engine logs this reason when the operator kill switch stops a strategy decision
# (``services/engine/src/ezchain_engine/scheduler.py``). ``ORDER_EXECUTOR_UNAVAILABLE`` is
# the documented fallback when no order service is wired at all.
KILL_SWITCH_REASONS = frozenset({"KILL_SWITCH_ACTIVE"})

# Invariant ids are stable API for reports and issue dedupe. Never rename one silently.
ORDER_INVARIANTS = (
    "orders.status_allowed",
    "orders.side_allowed",
    "orders.amount_valid",
    "orders.fill_order_exists",
    "orders.filled_has_one_fill",
)
IDEMPOTENCY_INVARIANTS = (
    "idempotency.same_order",
    "idempotency.at_most_one_fill",
)
POSITION_INVARIANTS = (
    "positions.quantity_positive",
    "positions.market_value_matches",
    "positions.unrealized_matches",
)
ACCOUNT_INVARIANTS = (
    "account.decimals",
    "account.cash_non_negative",
    "account.equity_matches",
    "account.equity_complete",
)
MARKET_INVARIANTS = ("markets.shape", "chains.shape", "token.shape", "quote.shape")
STRATEGY_INVARIANTS = (
    "strategies.crud",
    "strategy.run_log_non_empty",
    "strategy.run_order_filled",
)
RECONCILIATION_INVARIANTS = ("reconciliation.ok", "reconciliation.missing")
LEDGER_INVARIANTS = ("ledger.positions_match_fills", "ledger.cash_matches_fills")
RACE_INVARIANTS = (
    "race.no_oversell",
    "race.quantity_non_negative",
    "race.cash_non_negative",
)
KILLSWITCH_INVARIANTS = (
    "killswitch.blocks_strategy",
    "killswitch.allows_manual_order",
    "killswitch.restored",
)
MATRIX_INVARIANTS = ("matrix.chain_flow",)
FUZZ_INVARIANTS = (
    "fuzz.no_server_error",
    "fuzz.rejects_invalid",
    "fuzz.no_fill",
    "fuzz.error_body",
    "fuzz.delete_twice",
)
AUTH_INVARIANTS = (
    "auth.missing_token_rejected",
    "auth.wrong_token_rejected",
    "auth.error_body",
    "auth.no_mutation",
    "auth.control_valid",
)
HTTP_INVARIANTS = ("http.no_unexpected_5xx", "http.no_connection_errors")
LATENCY_INVARIANTS = (
    "latency.reads_p95",
    "latency.paper_mutations_p95",
    "latency.paper_mutations_p99",
)


@dataclass(frozen=True)
class Finding:
    """One invariant breach. ``code``/``scenario``/``invariant`` form the fingerprint."""

    code: str
    scenario: str
    invariant: str
    summary: str
    expected: str
    actual: str
    evidence: str = ""


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _decimal(value: object) -> Decimal | None:
    """Parse a finite decimal. ``None`` for null, booleans, NaN and infinity."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        parsed = Decimal(str(value))
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    return parsed if parsed.is_finite() else None


def _mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return None


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, list):
        return cast(Sequence[object], value)
    return None


def _label(item: Mapping[str, object], fallback: str) -> str:
    identifier = _text(item.get("id")) or _text(item.get("token_address"))
    if identifier is not None:
        return identifier
    chain = _text(item.get("chain"))
    return f"{chain}:{fallback}" if chain else fallback


def check_order(order: Mapping[str, object], *, scenario: str) -> list[Finding]:
    """Validate the fields of one order returned by ``POST /paper/orders``."""
    findings: list[Finding] = []
    label = _label(order, "order")
    status = _text(order.get("status"))
    if status not in ALLOWED_ORDER_STATUSES:
        findings.append(
            Finding(
                code="ORDER_STATUS_INVALID",
                scenario=scenario,
                invariant="orders.status_allowed",
                summary=f"order {label} has an unknown status",
                expected=f"one of {sorted(ALLOWED_ORDER_STATUSES)}",
                actual=repr(order.get("status")),
            )
        )
    side = _text(order.get("side"))
    if side not in ALLOWED_ORDER_SIDES:
        findings.append(
            Finding(
                code="ORDER_SIDE_INVALID",
                scenario=scenario,
                invariant="orders.side_allowed",
                summary=f"order {label} has an unknown side",
                expected=f"one of {sorted(ALLOWED_ORDER_SIDES)}",
                actual=repr(order.get("side")),
            )
        )
    amount = _decimal(order.get("amount_usd"))
    if amount is None or amount <= 0:
        findings.append(
            Finding(
                code="ORDER_AMOUNT_INVALID",
                scenario=scenario,
                invariant="orders.amount_valid",
                summary=f"order {label} has an invalid amount",
                expected="positive finite amount_usd",
                actual=repr(order.get("amount_usd")),
            )
        )
    return findings


def _timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z`` and a numeric value."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def check_orders_and_fills(
    orders: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]],
    *,
    scenario: str,
    order_ids: set[str] | None = None,
) -> list[Finding]:
    """Check order statuses and the order/fill relationship.

    ``order_ids`` limits the check to orders created by this harness, which keeps a shared
    paper account from producing false positives when the API caps history at 200 rows.
    """
    findings: list[Finding] = []
    order_by_id: dict[str, Mapping[str, object]] = {}
    for order in orders:
        order_id = _text(order.get("id"))
        if order_id is None:
            findings.append(
                Finding(
                    code="ORDER_ID_MISSING",
                    scenario=scenario,
                    invariant="orders.fill_order_exists",
                    summary="order row has no id",
                    expected="a non-empty order id",
                    actual=repr(order.get("id")),
                )
            )
            continue
        if order_id in order_by_id:
            findings.append(
                Finding(
                    code="ORDER_DUPLICATE_ID",
                    scenario=scenario,
                    invariant="orders.fill_order_exists",
                    summary=f"order id {order_id} appears more than once",
                    expected="unique order ids",
                    actual="duplicate row",
                )
            )
            continue
        order_by_id[order_id] = order

    fills_by_order: dict[str, int] = {}
    seen_fill_ids: set[str] = set()
    # The API returns the newest N rows for both orders and fills. A fill whose created_at
    # predates the oldest order in the page is outside the truncated window, which is not
    # evidence of corruption; a fill newer than that with no order is.
    order_times = [
        parsed for order in orders if (parsed := _timestamp(order.get("created_at"))) is not None
    ]
    oldest_order_at = min(order_times) if order_times else None
    for fill in fills:
        fill_id = _text(fill.get("id"))
        if fill_id is not None:
            if fill_id in seen_fill_ids:
                findings.append(
                    Finding(
                        code="FILL_DUPLICATE_ID",
                        scenario=scenario,
                        invariant="orders.filled_has_one_fill",
                        summary=f"fill id {fill_id} appears more than once",
                        expected="unique fill ids",
                        actual="duplicate row",
                    )
                )
            seen_fill_ids.add(fill_id)
        order_id = _text(fill.get("order_id"))
        if order_id is None:
            findings.append(
                Finding(
                    code="FILL_ORDER_MISSING",
                    scenario=scenario,
                    invariant="orders.fill_order_exists",
                    summary="fill row has no order id",
                    expected="a non-empty order_id",
                    actual=repr(fill.get("order_id")),
                )
            )
            continue
        fills_by_order[order_id] = fills_by_order.get(order_id, 0) + 1
        if order_id not in order_by_id and (order_ids is None or order_id in order_ids):
            fill_at = _timestamp(fill.get("created_at"))
            if oldest_order_at is not None and fill_at is not None and fill_at < oldest_order_at:
                # Older than the truncated order page: not proof of a missing order.
                continue
            findings.append(
                Finding(
                    code="FILL_ORDER_MISSING",
                    scenario=scenario,
                    invariant="orders.fill_order_exists",
                    summary=f"fill {fill_id or '?'} references a missing order",
                    expected="order_id present in GET /paper/orders",
                    actual=order_id,
                )
            )

    targets = order_ids if order_ids is not None else set(order_by_id)
    for order_id in sorted(targets):
        target = order_by_id.get(order_id)
        if target is None:
            # The API exposes at most 200 rows; a scoped order outside the window is not a
            # proof of corruption. The scenario caps creation so this should stay empty.
            continue
        status = _text(target.get("status"))
        if status not in ALLOWED_ORDER_STATUSES:
            findings.append(
                Finding(
                    code="ORDER_STATUS_INVALID",
                    scenario=scenario,
                    invariant="orders.status_allowed",
                    summary=f"order {order_id} has an unknown status",
                    expected=f"one of {sorted(ALLOWED_ORDER_STATUSES)}",
                    actual=repr(target.get("status")),
                )
            )
        side = _text(target.get("side"))
        if side not in ALLOWED_ORDER_SIDES:
            findings.append(
                Finding(
                    code="ORDER_SIDE_INVALID",
                    scenario=scenario,
                    invariant="orders.side_allowed",
                    summary=f"order {order_id} has an unknown side",
                    expected=f"one of {sorted(ALLOWED_ORDER_SIDES)}",
                    actual=repr(target.get("side")),
                )
            )
        if status == "filled":
            count = fills_by_order.get(order_id, 0)
            if count != 1:
                findings.append(
                    Finding(
                        code="FILLED_ORDER_FILL_COUNT",
                        scenario=scenario,
                        invariant="orders.filled_has_one_fill",
                        summary=f"filled order {order_id} has {count} fill(s)",
                        expected="exactly one fill per filled order",
                        actual=str(count),
                    )
                )
    return findings


def check_idempotency(
    first: Mapping[str, object],
    second: Mapping[str, object],
    fills_for_order: Sequence[Mapping[str, object]],
    *,
    scenario: str,
) -> list[Finding]:
    """Same idempotency key twice must return the same order and at most one fill."""
    findings: list[Finding] = []
    first_id = _text(first.get("id"))
    second_id = _text(second.get("id"))
    if first_id is None or first_id != second_id:
        findings.append(
            Finding(
                code="IDEMPOTENCY_DIFFERENT_ORDER",
                scenario=scenario,
                invariant="idempotency.same_order",
                summary="the same idempotency key produced a different order",
                expected=f"both responses return order {first_id or '<missing>'}",
                actual=repr(second_id),
            )
        )
    count = len(fills_for_order)
    if count > 1:
        findings.append(
            Finding(
                code="IDEMPOTENCY_MULTIPLE_FILLS",
                scenario=scenario,
                invariant="idempotency.at_most_one_fill",
                summary="the same idempotency key produced multiple fills",
                expected="at most one fill",
                actual=str(count),
            )
        )
    return findings


def check_positions(
    positions: Sequence[Mapping[str, object]],
    *,
    scenario: str,
    tolerance: Decimal = TOLERANCE,
) -> list[Finding]:
    """Quantity is positive; market value and unrealized PnL agree with the mark."""
    findings: list[Finding] = []
    for position in positions:
        label = _label(position, "position")
        quantity = _decimal(position.get("quantity"))
        if quantity is None or quantity <= 0:
            findings.append(
                Finding(
                    code="POSITION_QUANTITY_INVALID",
                    scenario=scenario,
                    invariant="positions.quantity_positive",
                    summary=f"position {label} has a non-positive quantity",
                    expected="quantity > 0",
                    actual=repr(position.get("quantity")),
                )
            )
            continue
        avg_cost = _decimal(position.get("avg_cost_usd"))
        if avg_cost is None:
            findings.append(
                Finding(
                    code="POSITION_AVG_COST_INVALID",
                    scenario=scenario,
                    invariant="positions.unrealized_matches",
                    summary=f"position {label} has an invalid average cost",
                    expected="finite avg_cost_usd",
                    actual=repr(position.get("avg_cost_usd")),
                )
            )
            continue
        mark_raw = position.get("mark_usd")
        mark = _decimal(mark_raw)
        if mark_raw is not None and mark is None:
            findings.append(
                Finding(
                    code="POSITION_MARK_INVALID",
                    scenario=scenario,
                    invariant="positions.market_value_matches",
                    summary=f"position {label} has an invalid mark",
                    expected="null or finite mark_usd",
                    actual=repr(mark_raw),
                )
            )
        market_value_raw = position.get("market_value_usd")
        market_value = _decimal(market_value_raw)
        if market_value_raw is not None and market_value is None:
            findings.append(
                Finding(
                    code="POSITION_MARKET_VALUE_INVALID",
                    scenario=scenario,
                    invariant="positions.market_value_matches",
                    summary=f"position {label} has an invalid market value",
                    expected="null or finite market_value_usd",
                    actual=repr(market_value_raw),
                )
            )
        elif market_value is not None:
            if mark is None:
                findings.append(
                    Finding(
                        code="POSITION_MARKET_VALUE_WITHOUT_MARK",
                        scenario=scenario,
                        invariant="positions.market_value_matches",
                        summary=f"position {label} has a market value but no mark",
                        expected="mark_usd present when market_value_usd is present",
                        actual="mark_usd is null",
                    )
                )
            else:
                expected_value = quantity * mark
                if abs(market_value - expected_value) > tolerance:
                    findings.append(
                        Finding(
                            code="POSITION_MARKET_VALUE_MISMATCH",
                            scenario=scenario,
                            invariant="positions.market_value_matches",
                            summary=f"position {label} market value disagrees with quantity*mark",
                            expected=str(expected_value),
                            actual=str(market_value),
                        )
                    )
        unrealized_raw = position.get("unrealized_usd")
        unrealized = _decimal(unrealized_raw)
        if unrealized_raw is not None and unrealized is None:
            findings.append(
                Finding(
                    code="POSITION_UNREALIZED_INVALID",
                    scenario=scenario,
                    invariant="positions.unrealized_matches",
                    summary=f"position {label} has an invalid unrealized value",
                    expected="null or finite unrealized_usd",
                    actual=repr(unrealized_raw),
                )
            )
        elif unrealized is not None:
            if market_value is None:
                findings.append(
                    Finding(
                        code="POSITION_UNREALIZED_WITHOUT_MARKET_VALUE",
                        scenario=scenario,
                        invariant="positions.unrealized_matches",
                        summary=f"position {label} has unrealized PnL but no market value",
                        expected="market_value_usd present when unrealized_usd is present",
                        actual="market_value_usd is null",
                    )
                )
            else:
                expected_unrealized = market_value - quantity * avg_cost
                if abs(unrealized - expected_unrealized) > tolerance:
                    findings.append(
                        Finding(
                            code="POSITION_UNREALIZED_MISMATCH",
                            scenario=scenario,
                            invariant="positions.unrealized_matches",
                            summary=f"position {label} unrealized PnL disagrees with value-cost",
                            expected=str(expected_unrealized),
                            actual=str(unrealized),
                        )
                    )
    return findings


def check_account(
    account: Mapping[str, object],
    *,
    scenario: str,
    tolerance: Decimal = TOLERANCE,
) -> list[Finding]:
    """Cash/equity/PnL are finite decimals, cash is non-negative and equity reconciles."""
    findings: list[Finding] = []

    def parsed_field(key: str, *, nullable: bool) -> Decimal | None:
        raw = account.get(key)
        parsed = _decimal(raw)
        if parsed is None and (raw is not None or not nullable):
            findings.append(
                Finding(
                    code="ACCOUNT_DECIMAL_INVALID",
                    scenario=scenario,
                    invariant="account.decimals",
                    summary=f"account field {key} is not a finite decimal",
                    expected="finite decimal string"
                    if not nullable
                    else "null or finite decimal string",
                    actual=repr(raw),
                )
            )
        return parsed

    cash = parsed_field("cash_usd", nullable=False)
    equity = parsed_field("equity_usd", nullable=True)
    parsed_field("realized_usd", nullable=False)
    parsed_field("unrealized_usd", nullable=True)

    positions_raw = _sequence(account.get("positions"))
    if positions_raw is None:
        findings.append(
            Finding(
                code="ACCOUNT_POSITIONS_INVALID",
                scenario=scenario,
                invariant="account.equity_matches",
                summary="account positions is not a list",
                expected="a list of positions",
                actual=repr(account.get("positions")),
            )
        )
        positions: list[Mapping[str, object]] = []
    else:
        positions = []
        for item in positions_raw:
            position = _mapping(item)
            if position is None:
                findings.append(
                    Finding(
                        code="ACCOUNT_POSITION_INVALID",
                        scenario=scenario,
                        invariant="account.equity_matches",
                        summary="account contains a non-object position",
                        expected="position objects",
                        actual=repr(item),
                    )
                )
            else:
                positions.append(position)

    if cash is not None and cash < 0:
        findings.append(
            Finding(
                code="ACCOUNT_NEGATIVE_CASH",
                scenario=scenario,
                invariant="account.cash_non_negative",
                summary="paper cash is negative",
                expected="cash_usd >= 0",
                actual=str(cash),
            )
        )

    known_market_value = Decimal(0)
    unpriced = 0
    for position in positions:
        raw = position.get("market_value_usd")
        parsed = _decimal(raw)
        if raw is not None and parsed is None:
            findings.append(
                Finding(
                    code="ACCOUNT_POSITION_VALUE_INVALID",
                    scenario=scenario,
                    invariant="account.equity_matches",
                    summary="account position has an invalid market value",
                    expected="null or finite market_value_usd",
                    actual=repr(raw),
                )
            )
        elif parsed is not None:
            known_market_value += parsed
        else:
            unpriced += 1

    marks_unknown = unpriced > 0
    complete_raw = account.get("complete")
    complete = complete_raw if isinstance(complete_raw, bool) else None
    if complete_raw is not None and complete is None:
        findings.append(
            Finding(
                code="ACCOUNT_COMPLETE_INVALID",
                scenario=scenario,
                invariant="account.equity_complete",
                summary="account complete flag is not a boolean",
                expected="true or false",
                actual=repr(complete_raw),
            )
        )
    if complete is not None and complete != (not marks_unknown):
        findings.append(
            Finding(
                code="ACCOUNT_COMPLETE_MISMATCH",
                scenario=scenario,
                invariant="account.equity_complete",
                summary="account completeness disagrees with its open position marks",
                expected=f"complete={not marks_unknown} ({unpriced} unpriced position(s))",
                actual=f"complete={complete}",
            )
        )
    if equity is not None and marks_unknown:
        findings.append(
            Finding(
                code="ACCOUNT_EQUITY_PARTIAL",
                scenario=scenario,
                invariant="account.equity_complete",
                summary="account reports equity while an open position has no mark",
                expected="equity_usd null",
                actual=str(equity),
            )
        )
    if equity is None and complete is True:
        findings.append(
            Finding(
                code="ACCOUNT_EQUITY_WITHHELD",
                scenario=scenario,
                invariant="account.equity_complete",
                summary="account withholds equity although every open mark is known",
                expected="equity_usd present",
                actual="equity_usd is null",
            )
        )

    known_raw = account.get("known_market_value_usd")
    known_parsed = _decimal(known_raw)
    if known_raw is not None and known_parsed is None:
        findings.append(
            Finding(
                code="ACCOUNT_DECIMAL_INVALID",
                scenario=scenario,
                invariant="account.decimals",
                summary="account field known_market_value_usd is not a finite decimal",
                expected="null or finite decimal string",
                actual=repr(known_raw),
            )
        )
    elif known_parsed is not None and abs(known_parsed - known_market_value) > tolerance:
        findings.append(
            Finding(
                code="ACCOUNT_KNOWN_VALUE_MISMATCH",
                scenario=scenario,
                invariant="account.equity_matches",
                summary="known_market_value_usd disagrees with the priced positions",
                expected=str(known_market_value),
                actual=str(known_parsed),
            )
        )

    if cash is not None and equity is not None:
        expected_equity = cash + (known_parsed if known_parsed is not None else known_market_value)
        if abs(equity - expected_equity) > tolerance:
            findings.append(
                Finding(
                    code="ACCOUNT_EQUITY_MISMATCH",
                    scenario=scenario,
                    invariant="account.equity_matches",
                    summary="equity does not equal cash plus known position market values",
                    expected=str(expected_equity),
                    actual=str(equity),
                )
            )
    return findings


def check_strategy_run(
    runs: Sequence[Mapping[str, object]],
    *,
    scenario: str,
) -> list[Finding]:
    """A strategy that was run must have at least one persisted decision."""
    if runs:
        return []
    return [
        Finding(
            code="STRATEGY_RUN_NOT_LOGGED",
            scenario=scenario,
            invariant="strategy.run_log_non_empty",
            summary="strategy run produced an empty run log",
            expected="at least one GET /strategies/{id}/runs row",
            actual="0 rows",
        )
    ]


def check_strategy_run_orders(
    runs: Sequence[Mapping[str, object]],
    orders: Sequence[Mapping[str, object]],
    *,
    scenario: str,
) -> list[Finding]:
    """A run that claims a buy/sell must point at a filled order.

    A rejected order is a decision, not an execution. Reporting ``action=buy`` with a
    rejected order id makes the decision log claim a trade that never happened.
    """
    order_by_id = {
        order_id: order for order in orders if (order_id := _text(order.get("id"))) is not None
    }
    findings: list[Finding] = []
    for run in runs:
        action = _text(run.get("action"))
        order_id = _text(run.get("order_id"))
        if action not in {"buy", "sell"} or order_id is None:
            continue
        order = order_by_id.get(order_id)
        if order is None:
            findings.append(
                Finding(
                    code="STRATEGY_RUN_ORDER_MISSING",
                    scenario=scenario,
                    invariant="strategy.run_order_filled",
                    summary=f"strategy run {run.get('id')!r} references an order outside history",
                    expected="order id present in GET /paper/orders",
                    actual=order_id,
                )
            )
            continue
        status = _text(order.get("status"))
        if status != "filled":
            findings.append(
                Finding(
                    code="STRATEGY_RUN_ORDER_NOT_FILLED",
                    scenario=scenario,
                    invariant="strategy.run_order_filled",
                    summary=(f"strategy run claims {action} but order {order_id} is {status!r}"),
                    expected="a filled order when the run action is buy/sell",
                    actual=f"status={status!r} reason={order.get('reason')!r}",
                    evidence=json.dumps(
                        {
                            "run_id": run.get("id"),
                            "action": action,
                            "order_id": order_id,
                            "order_status": status,
                            "order_reason": order.get("reason"),
                        },
                        sort_keys=True,
                    ),
                )
            )
    return findings


def check_strategy(
    strategy: Mapping[str, object],
    *,
    scenario: str,
    expected_status: str | None = None,
) -> list[Finding]:
    """Basic shape check for a created/patched strategy."""
    findings: list[Finding] = []
    if not _text(strategy.get("id")):
        findings.append(
            Finding(
                code="STRATEGY_ID_MISSING",
                scenario=scenario,
                invariant="strategies.crud",
                summary="strategy response has no id",
                expected="non-empty id",
                actual=repr(strategy.get("id")),
            )
        )
    if not _text(strategy.get("kind")):
        findings.append(
            Finding(
                code="STRATEGY_KIND_MISSING",
                scenario=scenario,
                invariant="strategies.crud",
                summary="strategy response has no kind",
                expected="non-empty kind",
                actual=repr(strategy.get("kind")),
            )
        )
    if expected_status is not None and strategy.get("status") != expected_status:
        findings.append(
            Finding(
                code="STRATEGY_STATUS_MISMATCH",
                scenario=scenario,
                invariant="strategies.crud",
                summary="strategy status did not change as requested",
                expected=expected_status,
                actual=repr(strategy.get("status")),
            )
        )
    return findings


def check_reconciliation(
    status: int | None,
    payload: Mapping[str, object] | None,
    *,
    scenario: str,
) -> list[Finding]:
    """``GET /paper/reconciliation`` is mandatory: a 404 is a failure, not a skip.

    The endpoint is the API's own proof that the journal, fills and orders agree. A
    harness that treats its absence as "not applicable" cannot claim ledger truth, so a
    missing endpoint fails the run with ``RECONCILIATION_MISSING``.
    """
    if status == 404:
        return [
            Finding(
                code="RECONCILIATION_MISSING",
                scenario=scenario,
                invariant="reconciliation.missing",
                summary="GET /api/v1/paper/reconciliation does not exist",
                expected="HTTP 200 with ok=true; the reconciliation endpoint is mandatory",
                actual="HTTP 404",
            )
        ]
    if status is None:
        return [
            Finding(
                code="RECONCILIATION_UNREACHABLE",
                scenario=scenario,
                invariant="reconciliation.ok",
                summary="reconciliation endpoint did not answer",
                expected="200 with ok=true",
                actual="transport error",
            )
        ]
    if status != 200:
        return [
            Finding(
                code="RECONCILIATION_STATUS",
                scenario=scenario,
                invariant="reconciliation.ok",
                summary="reconciliation endpoint returned a non-200 status",
                expected="200",
                actual=str(status),
            )
        ]
    ok = payload is not None and (
        payload.get("ok") is True
        or payload.get("status") == "ok"
        or payload.get("reconciled") is True
    )
    if not ok:
        return [
            Finding(
                code="RECONCILIATION_NOT_OK",
                scenario=scenario,
                invariant="reconciliation.ok",
                summary="reconciliation did not report ok",
                expected="ok=true (or status=ok)",
                actual=repr(None if payload is None else dict(payload)),
            )
        ]
    return []


def check_http(
    statuses: Sequence[int],
    transport_errors: Sequence[str],
    *,
    scenario: str,
    chaos: bool = False,
) -> list[Finding]:
    """No unexpected 5xx; connection errors are failures outside the chaos scenario."""
    findings: list[Finding] = []
    server_errors = [status for status in statuses if 500 <= status <= 599]
    if server_errors:
        findings.append(
            Finding(
                code="UNEXPECTED_5XX",
                scenario=scenario,
                invariant="http.no_unexpected_5xx",
                summary="the API returned a 5xx response",
                expected="no 5xx responses",
                actual=f"{len(server_errors)} of {len(statuses)} responses were 5xx",
            )
        )
    if transport_errors and not chaos:
        findings.append(
            Finding(
                code="CONNECTION_ERROR",
                scenario=scenario,
                invariant="http.no_connection_errors",
                summary="one or more requests could not reach the API",
                expected="every request reaches the API",
                actual=f"{len(transport_errors)} transport error(s): "
                + ", ".join(sorted(set(transport_errors))),
            )
        )
    return findings


def percentiles(samples: Sequence[float]) -> dict[str, float]:
    """Nearest-rank p50/p95/p99. Empty input returns zeros."""
    if not samples:
        return {"count": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    ordered = sorted(samples)

    def rank(percent: int) -> float:
        index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
        return round(ordered[index], 3)

    return {
        "count": float(len(ordered)),
        "p50": rank(50),
        "p95": rank(95),
        "p99": rank(99),
    }


def check_latency(
    samples_by_group: Mapping[str, Sequence[float]],
    *,
    scenario: str,
    read_p95_ms: float,
    mutation_p95_ms: float,
    mutation_p99_ms: float,
) -> list[Finding]:
    """Compare endpoint-group latencies against the configured budgets."""
    findings: list[Finding] = []
    reads = percentiles(samples_by_group.get("reads", ()))
    mutations = percentiles(samples_by_group.get("paper_mutations", ()))
    if reads["count"] > 0 and reads["p95"] > read_p95_ms:
        findings.append(
            Finding(
                code="LATENCY_BUDGET_READS",
                scenario=scenario,
                invariant="latency.reads_p95",
                summary="read p95 exceeded its budget",
                expected=f"reads p95 <= {read_p95_ms:g} ms",
                actual=f"reads p95 = {reads['p95']:.3f} ms (n={int(reads['count'])})",
            )
        )
    if mutations["count"] > 0 and mutations["p95"] > mutation_p95_ms:
        findings.append(
            Finding(
                code="LATENCY_BUDGET_MUTATIONS_P95",
                scenario=scenario,
                invariant="latency.paper_mutations_p95",
                summary="paper mutation p95 exceeded its budget",
                expected=f"paper mutation p95 <= {mutation_p95_ms:g} ms",
                actual=f"p95 = {mutations['p95']:.3f} ms (n={int(mutations['count'])})",
            )
        )
    if mutations["count"] > 0 and mutations["p99"] > mutation_p99_ms:
        findings.append(
            Finding(
                code="LATENCY_BUDGET_MUTATIONS_P99",
                scenario=scenario,
                invariant="latency.paper_mutations_p99",
                summary="paper mutation p99 exceeded its budget",
                expected=f"paper mutation p99 <= {mutation_p99_ms:g} ms",
                actual=f"p99 = {mutations['p99']:.3f} ms (n={int(mutations['count'])})",
            )
        )
    return findings


def _check_source(value: object, *, scenario: str, label: str) -> list[Finding]:
    source = _mapping(value)
    if source is None:
        return [
            Finding(
                code="MARKET_SHAPE",
                scenario=scenario,
                invariant="markets.shape",
                summary=f"{label} has no source object",
                expected="source with name and observed_at",
                actual=repr(value),
            )
        ]
    findings: list[Finding] = []
    if not _text(source.get("name")):
        findings.append(
            Finding(
                code="MARKET_SHAPE",
                scenario=scenario,
                invariant="markets.shape",
                summary=f"{label} source has no name",
                expected="non-empty source.name",
                actual=repr(source.get("name")),
            )
        )
    if not _text(source.get("observed_at")):
        findings.append(
            Finding(
                code="MARKET_SHAPE",
                scenario=scenario,
                invariant="markets.shape",
                summary=f"{label} source has no observed_at",
                expected="non-empty source.observed_at",
                actual=repr(source.get("observed_at")),
            )
        )
    return findings


def check_markets(payload: Mapping[str, object], *, scenario: str) -> list[Finding]:
    """Validate ``GET /api/v1/markets`` shape and numeric fields."""
    findings: list[Finding] = []
    rows = _sequence(payload.get("rows"))
    if rows is None:
        findings.append(
            Finding(
                code="MARKET_SHAPE",
                scenario=scenario,
                invariant="markets.shape",
                summary="markets payload has no rows list",
                expected="rows: []",
                actual=repr(payload.get("rows")),
            )
        )
        return findings
    if "stale" in payload and not isinstance(payload.get("stale"), bool):
        findings.append(
            Finding(
                code="MARKET_SHAPE",
                scenario=scenario,
                invariant="markets.shape",
                summary="markets stale flag is not a boolean",
                expected="boolean stale",
                actual=repr(payload.get("stale")),
            )
        )
    findings.extend(_check_source(payload.get("source"), scenario=scenario, label="markets"))
    for index, item in enumerate(rows):
        row = _mapping(item)
        if row is None:
            findings.append(
                Finding(
                    code="MARKET_SHAPE",
                    scenario=scenario,
                    invariant="markets.shape",
                    summary=f"market row {index} is not an object",
                    expected="market row object",
                    actual=repr(item),
                )
            )
            continue
        for key in ("chain", "pair_address", "base_token", "symbol"):
            if not _text(row.get(key)):
                findings.append(
                    Finding(
                        code="MARKET_SHAPE",
                        scenario=scenario,
                        invariant="markets.shape",
                        summary=f"market row {index} has no {key}",
                        expected=f"non-empty {key}",
                        actual=repr(row.get(key)),
                    )
                )
        price = _decimal(row.get("price_usd"))
        if price is None or price <= 0:
            findings.append(
                Finding(
                    code="MARKET_SHAPE",
                    scenario=scenario,
                    invariant="markets.shape",
                    summary=f"market row {index} has an invalid price",
                    expected="positive finite price_usd",
                    actual=repr(row.get("price_usd")),
                )
            )
        raw_liquidity = row.get("liquidity_usd")
        liquidity = _decimal(raw_liquidity)
        # Unknown liquidity is null by contract, never zero. Only reject a non-null
        # value that is malformed, negative or non-finite.
        if raw_liquidity is not None and (liquidity is None or liquidity < 0):
            findings.append(
                Finding(
                    code="MARKET_SHAPE",
                    scenario=scenario,
                    invariant="markets.shape",
                    summary=f"market row {index} has an invalid liquidity",
                    expected="null or non-negative finite liquidity_usd",
                    actual=repr(raw_liquidity),
                )
            )
        findings.extend(
            _check_source(row.get("source"), scenario=scenario, label=f"market row {index}")
        )
    return findings


def check_chains(payload: Sequence[object], *, scenario: str) -> list[Finding]:
    """Validate ``GET /api/v1/chains`` and require at least one paper-executable chain."""
    findings: list[Finding] = []
    if not payload:
        findings.append(
            Finding(
                code="CHAIN_SHAPE",
                scenario=scenario,
                invariant="chains.shape",
                summary="chain registry is empty",
                expected="at least one chain",
                actual="0 chains",
            )
        )
        return findings
    paper_chains = 0
    for index, item in enumerate(payload):
        chain = _mapping(item)
        if chain is None:
            findings.append(
                Finding(
                    code="CHAIN_SHAPE",
                    scenario=scenario,
                    invariant="chains.shape",
                    summary=f"chain row {index} is not an object",
                    expected="chain object",
                    actual=repr(item),
                )
            )
            continue
        for key in ("id", "name", "family", "native_asset", "tier"):
            if not _text(chain.get(key)):
                findings.append(
                    Finding(
                        code="CHAIN_SHAPE",
                        scenario=scenario,
                        invariant="chains.shape",
                        summary=f"chain row {index} has no {key}",
                        expected=f"non-empty {key}",
                        actual=repr(chain.get(key)),
                    )
                )
        capabilities = _mapping(chain.get("capabilities"))
        if capabilities is None:
            findings.append(
                Finding(
                    code="CHAIN_SHAPE",
                    scenario=scenario,
                    invariant="chains.shape",
                    summary=f"chain row {index} has no capabilities",
                    expected="capabilities object",
                    actual=repr(chain.get("capabilities")),
                )
            )
            continue
        for key in ("browse", "paper_execute", "live_execute"):
            if not isinstance(capabilities.get(key), bool):
                findings.append(
                    Finding(
                        code="CHAIN_SHAPE",
                        scenario=scenario,
                        invariant="chains.shape",
                        summary=f"chain row {index} capability {key} is not a boolean",
                        expected=f"boolean capabilities.{key}",
                        actual=repr(capabilities.get(key)),
                    )
                )
        if capabilities.get("paper_execute") is True:
            paper_chains += 1
    if paper_chains == 0:
        findings.append(
            Finding(
                code="CHAIN_PAPER_CAPABILITY_MISSING",
                scenario=scenario,
                invariant="chains.shape",
                summary="no chain advertises paper execution",
                expected="at least one capabilities.paper_execute = true",
                actual="0 chains",
            )
        )
    return findings


def check_token(
    payload: Mapping[str, object],
    *,
    scenario: str,
    chain: str,
    address: str,
) -> list[Finding]:
    """Validate ``GET /api/v1/tokens/{chain}/{address}``."""
    findings: list[Finding] = []
    if payload.get("chain") != chain:
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token chain does not match the request",
                expected=chain,
                actual=repr(payload.get("chain")),
            )
        )
    if payload.get("address") != address:
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token address does not match the request",
                expected=address,
                actual=repr(payload.get("address")),
            )
        )
    for key in ("symbol", "name"):
        if not _text(payload.get(key)):
            findings.append(
                Finding(
                    code="TOKEN_SHAPE",
                    scenario=scenario,
                    invariant="token.shape",
                    summary=f"token has no {key}",
                    expected=f"non-empty {key}",
                    actual=repr(payload.get(key)),
                )
            )
    if payload.get("price_usd") is not None and _decimal(payload.get("price_usd")) is None:
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token price is not a finite decimal or null",
                expected="null or finite price_usd",
                actual=repr(payload.get("price_usd")),
            )
        )
    if _sequence(payload.get("pairs")) is None:
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token pairs is not a list",
                expected="pairs: []",
                actual=repr(payload.get("pairs")),
            )
        )
    if _mapping(payload.get("safety")) is None:
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token safety is not an object",
                expected="safety: {}",
                actual=repr(payload.get("safety")),
            )
        )
    if not isinstance(payload.get("stale"), bool):
        findings.append(
            Finding(
                code="TOKEN_SHAPE",
                scenario=scenario,
                invariant="token.shape",
                summary="token stale flag is not a boolean",
                expected="boolean stale",
                actual=repr(payload.get("stale")),
            )
        )
    return findings


def check_quote(payload: Mapping[str, object], *, scenario: str) -> list[Finding]:
    """Validate ``GET /api/v1/quotes`` numeric fields and source."""
    findings: list[Finding] = []
    for key in ("chain", "token_in", "token_out", "side", "kind"):
        if not _text(payload.get(key)):
            findings.append(
                Finding(
                    code="QUOTE_SHAPE",
                    scenario=scenario,
                    invariant="quote.shape",
                    summary=f"quote has no {key}",
                    expected=f"non-empty {key}",
                    actual=repr(payload.get(key)),
                )
            )
    for key in ("amount_in", "amount_out", "price", "fee_usd"):
        parsed = _decimal(payload.get(key))
        if parsed is None:
            findings.append(
                Finding(
                    code="QUOTE_SHAPE",
                    scenario=scenario,
                    invariant="quote.shape",
                    summary=f"quote {key} is not a finite decimal",
                    expected=f"finite {key}",
                    actual=repr(payload.get(key)),
                )
            )
        elif key in {"amount_in", "amount_out", "price"} and parsed <= 0:
            findings.append(
                Finding(
                    code="QUOTE_SHAPE",
                    scenario=scenario,
                    invariant="quote.shape",
                    summary=f"quote {key} is not positive",
                    expected=f"{key} > 0",
                    actual=str(parsed),
                )
            )
    findings.extend(_check_source(payload.get("source"), scenario=scenario, label="quote"))
    return findings


def check_oversell(
    *,
    scenario: str,
    held_quantity: Decimal,
    sold_quantity: Decimal,
    final_quantity: Decimal | None = None,
    cash_usd: Decimal | None = None,
    tolerance: Decimal = QUANTITY_TOLERANCE,
) -> list[Finding]:
    """Concurrent full-size sells may never sell more than was held.

    ``held_quantity`` is what the position held before the concurrent sells, ``sold`` is
    the sum of the quantities on the fills they produced, and ``final_quantity`` is what
    the position reports afterwards (``None`` when the position is gone).
    """
    findings: list[Finding] = []
    if sold_quantity - held_quantity > tolerance:
        findings.append(
            Finding(
                code="RACE_OVERSELL",
                scenario=scenario,
                invariant="race.no_oversell",
                summary="concurrent sells sold more than the position held",
                expected=f"at most {held_quantity} sold",
                actual=f"{sold_quantity} sold",
            )
        )
    observed_final = Decimal(0) if final_quantity is None else final_quantity
    if observed_final + tolerance < Decimal(0):
        findings.append(
            Finding(
                code="RACE_NEGATIVE_POSITION",
                scenario=scenario,
                invariant="race.quantity_non_negative",
                summary="the position quantity went negative after concurrent sells",
                expected="quantity >= 0",
                actual=str(observed_final),
            )
        )
    if cash_usd is not None and cash_usd < 0:
        findings.append(
            Finding(
                code="RACE_NEGATIVE_CASH",
                scenario=scenario,
                invariant="race.cash_non_negative",
                summary="paper cash went negative after concurrent sells",
                expected="cash_usd >= 0",
                actual=str(cash_usd),
            )
        )
    return findings


def _run_reason(run: Mapping[str, object] | None) -> str | None:
    """The decision reason of a strategy run, with its structured ``error_code``."""
    if run is None:
        return None
    reason = _text(run.get("reason"))
    inputs = _mapping(run.get("inputs")) or {}
    error_code = _text(inputs.get("error_code"))
    return reason or error_code


def _blocked_by_kill_switch(run: Mapping[str, object] | None) -> bool:
    """True when a run row records the kill switch stopping an order.

    Two documented shapes exist. ``services/engine`` raises ``ExecutionBlocked`` before the
    API is called, which logs ``hold`` / ``KILL_SWITCH_ACTIVE``. The order service's own
    defence in depth rejects a strategy-attributed order instead, which the engine records
    as ``hold`` / ``ORDER_REJECTED`` with ``inputs.order_reason = KILL_SWITCH_ACTIVE``.
    """
    if run is None or _text(run.get("action")) != "hold":
        return False
    reason = _text(run.get("reason"))
    inputs = _mapping(run.get("inputs")) or {}
    if reason in KILL_SWITCH_REASONS:
        return True
    if reason == "ORDER_REJECTED":
        order_reason = _text(inputs.get("order_reason")) or ""
        return any(code in order_reason for code in KILL_SWITCH_REASONS)
    return False


def check_killswitch_run(
    run: Mapping[str, object] | None,
    *,
    persisted_runs: Sequence[Mapping[str, object]],
    orders_created: int,
    scenario: str,
) -> list[Finding]:
    """An armed kill switch must log a hold with its documented reason and place no order.

    ``run`` is the ``POST /strategies/{id}/run`` response (the fresh decision) and
    ``persisted_runs`` is ``GET /strategies/{id}/runs`` — the run log is the durable proof,
    so the check accepts a hold that either source records.
    """
    findings: list[Finding] = []
    candidates: list[Mapping[str, object]] = [item for item in (run, *persisted_runs) if item]
    blocked = [item for item in candidates if _blocked_by_kill_switch(item)]
    if not blocked:
        observed = [
            f"action={item.get('action')!r} reason={_run_reason(item)!r}" for item in candidates
        ]
        findings.append(
            Finding(
                code="KILLSWITCH_NOT_LOGGED",
                scenario=scenario,
                invariant="killswitch.blocks_strategy",
                summary="an armed kill switch did not produce a hold/KILL_SWITCH_ACTIVE decision",
                expected="a run logged with action=hold and reason=KILL_SWITCH_ACTIVE",
                actual="; ".join(observed) or "no run row",
            )
        )
    if orders_created:
        findings.append(
            Finding(
                code="KILLSWITCH_ORDER_PLACED",
                scenario=scenario,
                invariant="killswitch.blocks_strategy",
                summary="a strategy placed an order while the kill switch was armed",
                expected="no order from a strategy run while armed",
                actual=f"{orders_created} order(s) created",
            )
        )
    return findings


def check_killswitch_allows_manual_order(
    order: Mapping[str, object] | None,
    *,
    status_code: int | None,
    scenario: str,
) -> list[Finding]:
    """A manual paper order stays allowed while the switch is armed (documented scope)."""
    if order is None:
        return [
            Finding(
                code="KILLSWITCH_MANUAL_ORDER_REFUSED",
                scenario=scenario,
                invariant="killswitch.allows_manual_order",
                summary="a manual paper order did not return a readable order",
                expected="HTTP 201 with an order body",
                actual=f"HTTP {status_code}",
            )
        ]
    reason = _text(order.get("reason")) or ""
    if any(code in reason for code in KILL_SWITCH_REASONS):
        return [
            Finding(
                code="KILLSWITCH_MANUAL_ORDER_BLOCKED",
                scenario=scenario,
                invariant="killswitch.allows_manual_order",
                summary="the kill switch blocked a manual paper order",
                expected="manual orders are outside the strategy-scoped switch",
                actual=f"reason={reason!r}",
            )
        ]
    return []


def check_killswitch_state(
    state: Mapping[str, object] | None,
    *,
    expected_active: bool,
    scenario: str,
    restored: bool = False,
) -> list[Finding]:
    """The switch must be exactly where the harness expects it (armed, or restored)."""
    if state is None:
        return [
            Finding(
                code="KILLSWITCH_STATE_UNREADABLE",
                scenario=scenario,
                invariant="killswitch.restored" if restored else "killswitch.blocks_strategy",
                summary="the kill switch state could not be read",
                expected=f"active={expected_active}",
                actual="no readable state",
            )
        ]
    active = state.get("active")
    if active is expected_active:
        return []
    return [
        Finding(
            code="KILLSWITCH_NOT_RESTORED" if restored else "KILLSWITCH_NOT_ARMED",
            scenario=scenario,
            invariant="killswitch.restored" if restored else "killswitch.blocks_strategy",
            summary=(
                "the kill switch was not restored to its previous state"
                if restored
                else "the kill switch did not arm"
            ),
            expected=f"active={expected_active}",
            actual=f"active={active!r}",
        )
    ]


def check_position_closed(
    quantity: Decimal | None,
    *,
    scenario: str,
    label: str,
    tolerance: Decimal = QUANTITY_TOLERANCE,
) -> list[Finding]:
    """After selling the full position, no quantity may remain (``None`` means gone)."""
    remaining = Decimal(0) if quantity is None else quantity
    if abs(remaining) <= tolerance:
        return []
    return [
        Finding(
            code="MATRIX_POSITION_NOT_CLOSED",
            scenario=scenario,
            invariant="matrix.chain_flow",
            summary=f"the position {label} did not close after a full-size sell",
            expected="quantity 0",
            actual=str(remaining),
        )
    ]


def self_test() -> None:
    """Fast, network-free sanity check used by ``--self-test``."""
    order = {"id": "o1", "status": "filled", "side": "buy", "amount_usd": "1.00"}
    fill = {"id": "f1", "order_id": "o1"}
    assert check_orders_and_fills([order], [fill], scenario="self") == []
    assert check_orders_and_fills([order], [fill, {"id": "f2", "order_id": "o1"}], scenario="self")
    position = {
        "chain": "base",
        "token_address": "0xabc",
        "quantity": "2",
        "avg_cost_usd": "1",
        "mark_usd": "1.5",
        "market_value_usd": "3",
        "unrealized_usd": "1",
    }
    account = {
        "cash_usd": "97",
        "equity_usd": "100",
        "realized_usd": "0",
        "unrealized_usd": "1",
        "positions": [position],
    }
    assert check_positions([position], scenario="self") == []
    assert check_account(account, scenario="self") == []
    unpriced = {**position, "mark_usd": None, "market_value_usd": None, "unrealized_usd": None}
    withheld = {
        "cash_usd": "97",
        "equity_usd": None,
        "realized_usd": "0",
        "unrealized_usd": None,
        "complete": False,
        "marks_unknown": True,
        "known_market_value_usd": "0",
        "positions": [unpriced],
    }
    assert check_account(withheld, scenario="self") == []
    assert check_account({**withheld, "equity_usd": "97"}, scenario="self")
    assert check_reconciliation(200, {"ok": True}, scenario="self") == []
    # The endpoint is mandatory: a 404 is a failure, not a skip.
    missing = check_reconciliation(404, None, scenario="self")
    assert missing and missing[0].invariant == "reconciliation.missing"
    assert check_idempotency({"id": "o1"}, {"id": "o1"}, [fill], scenario="self") == []

    assert (
        check_oversell(
            scenario="self",
            held_quantity=Decimal("2"),
            sold_quantity=Decimal("2"),
            final_quantity=None,
            cash_usd=Decimal("97"),
        )
        == []
    )
    assert check_oversell(
        scenario="self",
        held_quantity=Decimal("2"),
        sold_quantity=Decimal("4"),
        final_quantity=Decimal("-2"),
        cash_usd=Decimal("-1"),
    )
    blocked_run = {
        "id": "r1",
        "action": "hold",
        "reason": "KILL_SWITCH_ACTIVE",
        "inputs": {"error_code": "KILL_SWITCH_ACTIVE"},
    }
    assert (
        check_killswitch_run(blocked_run, persisted_runs=[], orders_created=0, scenario="self")
        == []
    )
    assert check_killswitch_run(
        {"id": "r2", "action": "buy", "reason": "DCA_BUY"},
        persisted_runs=[],
        orders_created=1,
        scenario="self",
    )
    assert check_killswitch_state({"active": True}, expected_active=True, scenario="self") == []
    assert (
        check_killswitch_state(
            {"active": False}, expected_active=False, scenario="self", restored=True
        )
        == []
    )
    assert check_position_closed(Decimal("0"), scenario="self", label="base:0xaa") == []
    assert check_position_closed(Decimal("0.5"), scenario="self", label="base:0xaa")
