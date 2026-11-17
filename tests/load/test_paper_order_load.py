"""Measured latency of the in-process paper order path.

This drives the order service directly through its one write path, against in-memory
SQLite. It is a PAPER, single-worker measurement: no HTTP server, no venue and no key. The
report line is the evidence quoted in the pull request.
"""

from __future__ import annotations

import json
import math
import random
import time
from decimal import Decimal

from sqlalchemy import func, select

from ezchain_api.models import LedgerEntryRow, PaperFillRow, PaperOrderRow
from ezchain_api.services import paper
from ezchain_core.ledger import entry_from_row, replay_balances
from tests.paper.conftest import make_request, place

ORDERS = 180


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


def test_paper_order_path_load(session, settings, dex, jupiter) -> None:
    paper.ensure_genesis(session, settings.paper_starting_cash_usd)
    rng = random.Random(7)
    latencies_ms: list[float] = []
    outcomes: dict[str, int] = {}
    errors: list[str] = []

    for index in range(ORDERS):
        request = make_request(
            amount_usd=str(rng.randint(5, 15)),
            key=f"load-{index:04d}",
        )
        started = time.perf_counter()
        try:
            order = place(session, settings, dex, jupiter, request)
        except Exception as exc:  # noqa: BLE001 - an exception here is a load finding
            errors.append(f"{index}: {type(exc).__name__}: {exc}")
            continue
        latencies_ms.append((time.perf_counter() - started) * 1000)
        outcomes[order.status] = outcomes.get(order.status, 0) + 1

    result = {
        "orders": ORDERS,
        "completed": len(latencies_ms),
        "filled": outcomes.get("filled", 0),
        "rejected": outcomes.get("rejected", 0),
        "errors": len(errors),
        "p50_ms": _percentile(latencies_ms, 0.50),
        "p99_ms": _percentile(latencies_ms, 0.99),
    }
    print("LOAD_RESULT " + json.dumps(result, sort_keys=True))
    if errors:
        print("LOAD_ERRORS " + json.dumps(errors[:10], sort_keys=True))

    assert result["errors"] == 0, errors[:10]
    assert result["completed"] == ORDERS
    assert result["filled"] > 0
    assert result["p99_ms"] < 5000

    filled = session.scalar(
        select(func.count()).select_from(PaperOrderRow).where(PaperOrderRow.status == "filled")
    )
    assert filled == session.scalar(select(func.count()).select_from(PaperFillRow))
    rows = session.scalars(select(LedgerEntryRow)).all()
    balances = replay_balances([entry_from_row(row) for row in rows])
    assert sum(balance.usd_minor for balance in balances) == 0
    assert Decimal(str(paper.account_snapshot(session)["cash_usd"])) >= 0
