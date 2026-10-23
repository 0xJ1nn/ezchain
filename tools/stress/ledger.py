"""Independent ledger recompute for one stress run.

The API's ``GET /api/v1/paper/reconciliation`` proves the journal against itself. This
module proves it against the run's *own* observations, so a bug that makes the read model
and the consensus agree on the same wrong answer is still visible:

* per token, ``sum(buy quantity) - sum(sell quantity)`` must equal the difference between
  the position quantity observed before and after the run;
* ``cash delta`` must equal ``sum(sell notional) - sum(buy notional) - sum(fees)``.

Everything here is pure: plain JSON rows in, :class:`Finding` values out. Scoping matters.
The paper account can be shared with another writer, so a fill this run did not create
aborts the recompute with an explicit ``concurrent_writer_suspected`` skip rather than
reporting arithmetic that is not this run's.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .checks import QUANTITY_TOLERANCE, TOLERANCE, Finding, _decimal, _text

PositionKey = tuple[str, str]

SKIP_CONCURRENT_WRITER = "concurrent_writer_suspected"
SKIP_FILLS_WINDOW = "fills_window_incomplete"


def position_key(row: Mapping[str, object]) -> PositionKey | None:
    """Identity of a position/fill row: ``(chain, token_address)``."""
    chain = _text(row.get("chain"))
    token = _text(row.get("token_address")) or _text(row.get("token"))
    if chain is None or token is None:
        return None
    return (chain, token)


def position_quantities(
    positions: Sequence[Mapping[str, object]],
) -> dict[PositionKey, Decimal]:
    """Map each listed position to its quantity. A listed row always has a positive one."""
    quantities: dict[PositionKey, Decimal] = {}
    for position in positions:
        key = position_key(position)
        quantity = _decimal(position.get("quantity"))
        if key is None or quantity is None:
            continue
        quantities[key] = quantities.get(key, Decimal(0)) + quantity
    return quantities


@dataclass(frozen=True)
class RunFlow:
    """What this run's fills did, per token and in total."""

    bought: Mapping[PositionKey, Decimal]
    sold: Mapping[PositionKey, Decimal]
    buy_notional: Decimal
    sell_notional: Decimal
    fees: Decimal
    fill_count: int

    @property
    def tokens(self) -> set[PositionKey]:
        return set(self.bought) | set(self.sold)

    @property
    def expected_cash_delta(self) -> Decimal:
        return self.sell_notional - self.buy_notional - self.fees


def run_flow(
    fills: Sequence[Mapping[str, object]],
    *,
    run_order_ids: Collection[str],
) -> RunFlow:
    """Aggregate the fills that belong to orders this run created.

    A fill whose order is not in ``run_order_ids`` is another writer's and is ignored
    here; :func:`check_ledger_recompute` refuses to draw conclusions in that case.
    """
    bought: dict[PositionKey, Decimal] = {}
    sold: dict[PositionKey, Decimal] = {}
    buy_notional = Decimal(0)
    sell_notional = Decimal(0)
    fees = Decimal(0)
    counted = 0
    for fill in fills:
        order_id = _text(fill.get("order_id"))
        if order_id is None or order_id not in run_order_ids:
            continue
        key = position_key(fill)
        if key is None:
            continue
        quantity = _decimal(fill.get("quantity"))
        notional = _decimal(fill.get("notional_usd"))
        fee = _decimal(fill.get("fee_usd")) or Decimal(0)
        side = _text(fill.get("side"))
        if quantity is None or notional is None or side not in {"buy", "sell"}:
            continue
        counted += 1
        fees += fee
        if side == "buy":
            bought[key] = bought.get(key, Decimal(0)) + quantity
            buy_notional += notional
        else:
            sold[key] = sold.get(key, Decimal(0)) + quantity
            sell_notional += notional
    return RunFlow(
        bought=bought,
        sold=sold,
        buy_notional=buy_notional,
        sell_notional=sell_notional,
        fees=fees,
        fill_count=counted,
    )


def foreign_fills(
    fills: Sequence[Mapping[str, object]],
    *,
    run_order_ids: Collection[str],
    new_fill_ids: Collection[str] | None = None,
) -> list[str]:
    """Order ids of fills that appeared in the window and are not this run's."""
    seen = None if new_fill_ids is None else set(new_fill_ids)
    found: list[str] = []
    for fill in fills:
        fill_id = _text(fill.get("id"))
        if seen is not None and (fill_id is None or fill_id not in seen):
            continue
        order_id = _text(fill.get("order_id"))
        if order_id is None or order_id in run_order_ids:
            continue
        if order_id not in found:
            found.append(order_id)
    return found


@dataclass(frozen=True)
class LedgerVerdict:
    """Outcome of the recompute: findings, an optional skip and a per-token table."""

    findings: tuple[Finding, ...]
    skip: str | None
    detail: str
    table: tuple[dict[str, object], ...]

    @property
    def ok(self) -> bool:
        return not self.findings and self.skip is None


def check_ledger_recompute(
    *,
    scenario: str,
    before: Mapping[PositionKey, Decimal],
    after: Mapping[PositionKey, Decimal],
    fills: Sequence[Mapping[str, object]],
    run_order_ids: Collection[str],
    cash_before: Decimal | None,
    cash_after: Decimal | None,
    new_fill_ids: Collection[str] | None = None,
    filled_orders_missing_fill: Collection[str] = (),
    tolerance: Decimal = TOLERANCE,
) -> LedgerVerdict:
    """Recompute the run's position and cash effect from its own fills."""
    flow = run_flow(fills, run_order_ids=run_order_ids)
    order_ids = set(run_order_ids)

    if filled_orders_missing_fill:
        missing = ", ".join(sorted(filled_orders_missing_fill)[:5])
        return LedgerVerdict(
            findings=(),
            skip=SKIP_FILLS_WINDOW,
            detail=(
                "a filled order created by this run has no fill inside the newest "
                f"200-row fill window ({missing})"
            ),
            table=(),
        )

    intruders = foreign_fills(fills, run_order_ids=order_ids, new_fill_ids=new_fill_ids)
    if intruders:
        return LedgerVerdict(
            findings=(),
            skip=SKIP_CONCURRENT_WRITER,
            detail=(
                "fills from another writer appeared inside the measurement window "
                f"({', '.join(intruders[:5])}); the position and cash deltas are not "
                "attributable to this run"
            ),
            table=(),
        )

    findings: list[Finding] = []
    table: list[dict[str, object]] = []
    for key in sorted(flow.tokens):
        chain, token = key
        bought = flow.bought.get(key, Decimal(0))
        sold = flow.sold.get(key, Decimal(0))
        expected = bought - sold
        observed = after.get(key, Decimal(0)) - before.get(key, Decimal(0))
        ok = abs(expected - observed) <= QUANTITY_TOLERANCE
        table.append(
            {
                "chain": chain,
                "token_address": token,
                "buy_quantity": str(bought),
                "sell_quantity": str(sold),
                "expected_delta": str(expected),
                "observed_delta": str(observed),
                "ok": ok,
            }
        )
        if not ok:
            findings.append(
                Finding(
                    code="LEDGER_POSITION_DELTA_MISMATCH",
                    scenario=scenario,
                    invariant="ledger.positions_match_fills",
                    summary=(
                        f"position quantity delta for {chain}:{token} disagrees with "
                        "this run's fills"
                    ),
                    expected=f"{expected} (sum of buys minus sum of sells)",
                    actual=f"{observed} (after snapshot minus before snapshot)",
                )
            )

    if cash_before is not None and cash_after is not None:
        observed_cash = cash_after - cash_before
        expected_cash = flow.expected_cash_delta
        if abs(expected_cash - observed_cash) > tolerance:
            findings.append(
                Finding(
                    code="LEDGER_CASH_DELTA_MISMATCH",
                    scenario=scenario,
                    invariant="ledger.cash_matches_fills",
                    summary="cash delta disagrees with this run's fills",
                    expected=(
                        f"{expected_cash} (sell notional {flow.sell_notional} - buy "
                        f"notional {flow.buy_notional} - fees {flow.fees})"
                    ),
                    actual=f"{observed_cash} (after snapshot minus before snapshot)",
                )
            )

    detail = (
        f"{flow.fill_count} fill(s) across {len(flow.tokens)} token(s); "
        f"expected cash delta {flow.expected_cash_delta}"
    )
    return LedgerVerdict(
        findings=tuple(findings),
        skip=None,
        detail=detail,
        table=tuple(table),
    )


def summarize(verdict: LedgerVerdict) -> dict[str, object]:
    """Report-shaped summary of one ledger recompute."""
    return {
        "skip": verdict.skip,
        "detail": verdict.detail,
        "tokens": [dict(row) for row in verdict.table],
        "findings": len(verdict.findings),
    }


__all__ = [
    "QUANTITY_TOLERANCE",
    "SKIP_CONCURRENT_WRITER",
    "SKIP_FILLS_WINDOW",
    "LedgerVerdict",
    "PositionKey",
    "RunFlow",
    "check_ledger_recompute",
    "foreign_fills",
    "position_key",
    "position_quantities",
    "run_flow",
    "summarize",
]
