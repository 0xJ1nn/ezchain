"""Scenario orchestration for the PAPER stress harness.

Every mode shares the same safety preflight and the same invariant snapshot. The modes
differ only in how they generate load:

* ``smoke`` — one full flow, then low-rate reads for the duration.
* ``soak`` — concurrent buy/sell/read workers for the duration.
* ``idempotency`` — the same key twice, sequentially and concurrently.
* ``chaos`` — retry/backoff against an invalid path and an unreachable loopback port.

Nothing here kills a process, injects a server fault or talks to a live venue.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from urllib.parse import quote as urlquote

from .checks import (
    IDEMPOTENCY_INVARIANTS,
    MARKET_INVARIANTS,
    STRATEGY_INVARIANTS,
    Finding,
    check_account,
    check_chains,
    check_idempotency,
    check_markets,
    check_order,
    check_quote,
    check_strategy,
    check_strategy_run,
    check_strategy_run_orders,
    check_token,
)
from .config import RunConfig
from .http import Harness
from .report import build_report, write_reports

CHAOS_SCENARIO_NOTE = (
    "chaos never injects a 5xx or kills a process; it only retries an invalid path and an "
    "unreachable loopback port, then proves the paper account is still readable"
)


class PreflightError(RuntimeError):
    """The API is unreachable or did not prove paper mode. Nothing was mutated."""


@dataclass(frozen=True)
class ResolvedToken:
    chain: str
    address: str
    symbol: str
    price_usd: Decimal | None

    def as_dict(self) -> dict[str, object]:
        return {
            "chain": self.chain,
            "address": self.address,
            "symbol": self.symbol,
            "price_usd": None if self.price_usd is None else str(self.price_usd),
        }


@dataclass(frozen=True)
class ExecutionResult:
    report: dict[str, object]
    json_path: Path
    markdown_path: Path
    findings: tuple[Finding, ...]


def _dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _rows(value: object) -> Sequence[object]:
    if isinstance(value, list):
        return cast(Sequence[object], value)
    return []


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight(harness: Harness) -> None:
    """Refuse anything that is not paper mode. Raises before any mutation."""
    health = harness.request("GET", "/health", scenario="preflight")
    health_payload = harness.json_object(health, scenario="preflight", label="health")
    if health is None or health.status_code != 200 or health_payload is None:
        raise PreflightError("PREFLIGHT_FAILED: GET /health is unreachable or not 200")
    if health_payload.get("status") != "ok":
        raise PreflightError(
            f"PREFLIGHT_FAILED: /health status is {health_payload.get('status')!r}, not 'ok'"
        )

    account = harness.request("GET", "/api/v1/paper/account", scenario="preflight")
    account_payload = harness.json_object(account, scenario="preflight", label="paper account")
    if account is None or account.status_code != 200 or account_payload is None:
        raise PreflightError(
            "REFUSING_NON_PAPER: GET /api/v1/paper/account did not return a readable 200"
        )
    mode = account_payload.get("mode")
    if mode != "paper":
        raise PreflightError(
            f"REFUSING_NON_PAPER: /api/v1/paper/account reported mode={mode!r}; "
            "the stress harness only runs against mode='paper'"
        )
    harness.register_invariants("preflight.health", "preflight.paper_mode")
    harness.add_findings(check_account(account_payload, scenario="preflight"))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _read_chains(harness: Harness, *, scenario: str) -> Sequence[Mapping[str, object]]:
    response = harness.request("GET", "/api/v1/chains", scenario=scenario)
    rows = harness.json_list(response, scenario=scenario, label="chains")
    if rows is None:
        return []
    harness.add_findings(check_chains(rows, scenario=scenario))
    harness.register_invariants("chains.shape")
    return rows


def _read_markets(
    harness: Harness,
    *,
    chain: str,
    limit: int,
    scenario: str,
) -> dict[str, object] | None:
    response = harness.request(
        "GET",
        "/api/v1/markets",
        scenario=scenario,
        params={"chain": chain, "limit": limit},
        expected=(200, 429),
        allow_status=(429,),
    )
    if response is None:
        return None
    if response.status_code == 429:
        harness.note(f"markets rate limited (HTTP 429) for chain={chain}")
        harness.skip_invariant("markets.shape")
        return None
    if response.status_code != 200:
        return None
    payload = harness.json_object(response, scenario=scenario, label="markets")
    if payload is None:
        return None
    harness.add_findings(check_markets(payload, scenario=scenario))
    harness.register_invariants("markets.shape")
    harness.unskip_invariant("markets.shape")
    return payload


def _read_overview(harness: Harness, *, scenario: str) -> None:
    response = harness.request(
        "GET",
        "/api/v1/markets/overview",
        scenario=scenario,
        expected=(200, 404),
        allow_status=(404,),
    )
    harness.register_invariants("markets.overview")
    if response is None:
        return
    if response.status_code == 404:
        harness.skip_invariant("markets.overview")
        harness.note("GET /api/v1/markets/overview is absent (404); skipped")
        return
    if response.status_code == 200:
        payload = harness.json_object(response, scenario=scenario, label="markets overview")
        harness.unskip_invariant("markets.overview")
        if payload is None:
            harness.add_finding(
                Finding(
                    code="OVERVIEW_SHAPE",
                    scenario=scenario,
                    invariant="markets.overview",
                    summary="markets overview returned a non-object body",
                    expected="a JSON object",
                    actual="unreadable body",
                )
            )


def _read_token(
    harness: Harness, token: ResolvedToken, *, scenario: str
) -> dict[str, object] | None:
    path = f"/api/v1/tokens/{urlquote(token.chain, safe='')}/{urlquote(token.address, safe='')}"
    response = harness.request(
        "GET", path, scenario=scenario, expected=(200, 404), allow_status=(404,)
    )
    if response is None:
        return None
    if response.status_code == 404:
        harness.add_finding(
            Finding(
                code="TOKEN_LOOKUP_FAILED",
                scenario=scenario,
                invariant="token.shape",
                summary="the discovered token is not visible on the token endpoint",
                expected="HTTP 200 with token details",
                actual="HTTP 404",
                evidence=response.text[:500],
            )
        )
        return None
    if response.status_code != 200:
        return None
    payload = harness.json_object(response, scenario=scenario, label="token")
    if payload is None:
        return None
    harness.add_findings(
        check_token(payload, scenario=scenario, chain=token.chain, address=token.address)
    )
    harness.register_invariants("token.shape")
    return payload


def _read_quote(
    harness: Harness,
    token: ResolvedToken,
    *,
    side: str,
    amount_usd: Decimal,
    quantity: Decimal | None,
    scenario: str,
) -> dict[str, object] | None:
    params: dict[str, str | int | float] = {
        "chain": token.chain,
        "token": token.address,
        "side": side,
        "amount_usd": str(amount_usd),
    }
    if quantity is not None:
        params["quantity"] = str(quantity)
    response = harness.request(
        "GET",
        "/api/v1/quotes",
        scenario=scenario,
        params=params,
        expected=(200,),
        allow_status=(409, 429),
    )
    if response is None:
        return None
    if response.status_code in {409, 429}:
        harness.note(f"{side} quote unavailable (HTTP {response.status_code}); fail-closed")
        return None
    if response.status_code != 200:
        return None
    payload = harness.json_object(response, scenario=scenario, label=f"{side} quote")
    if payload is None:
        return None
    harness.add_findings(check_quote(payload, scenario=scenario))
    harness.register_invariants("quote.shape")
    harness.unskip_invariant("quote.shape")
    return payload


def _positions(harness: Harness, *, scenario: str) -> list[dict[str, object]]:
    response = harness.request("GET", "/api/v1/paper/positions", scenario=scenario)
    return harness.json_list(response, scenario=scenario, label="paper positions") or []


def _position_for(
    harness: Harness, token: ResolvedToken, *, scenario: str
) -> dict[str, object] | None:
    for position in _positions(harness, scenario=scenario):
        if position.get("chain") == token.chain and position.get("token_address") == token.address:
            return position
    return None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _post_order(
    harness: Harness,
    token: ResolvedToken,
    *,
    side: str,
    amount_usd: Decimal,
    idempotency_key: str,
    scenario: str,
    reserve_slot: bool = True,
) -> dict[str, object] | None:
    if reserve_slot and not harness.reserve_order_slot():
        return None
    amount = max(Decimal("0.01"), amount_usd).quantize(Decimal("0.01"))
    body: dict[str, object] = {
        "chain": token.chain,
        "token_address": token.address,
        "side": side,
        "amount_usd": str(amount),
        "idempotency_key": idempotency_key,
    }
    response = harness.request(
        "POST",
        "/api/v1/paper/orders",
        scenario=scenario,
        json_body=body,
        expected=(201,),
        allow_status=(409,),
    )
    if response is None:
        return None
    if response.status_code == 409:
        harness.note(f"{side} order refused with HTTP 409 (fail-closed route/size)")
        return None
    if response.status_code != 201:
        return None
    order = harness.json_object(response, scenario=scenario, label="paper order")
    if order is None:
        return None
    order_id = _str(order.get("id"))
    if order_id is not None:
        harness.note_order(order_id)
    harness.note_order_summary(order)
    harness.add_findings(check_order(order, scenario=scenario))
    return order


def _maybe_sell(
    harness: Harness, token: ResolvedToken, *, scenario: str
) -> dict[str, object] | None:
    position = _position_for(harness, token, scenario=scenario)
    if position is None:
        harness.note("sell skipped: no position in the resolved token")
        return None
    quantity = _dec(position.get("quantity"))
    market_value = _dec(position.get("market_value_usd"))
    if quantity is None or quantity <= 0:
        harness.note("sell skipped: position quantity is not positive")
        return None
    amount = harness.config.order_usd
    if market_value is not None and market_value > 0:
        amount = min(amount, market_value)
    if amount < Decimal("0.01"):
        harness.note("sell skipped: position value below $0.01")
        return None
    return _post_order(
        harness,
        token,
        side="sell",
        amount_usd=amount,
        idempotency_key=harness.next_id(f"{scenario}-sell"),
        scenario=scenario,
    )


def _market_checks(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    _read_markets(harness, chain="all", limit=50, scenario=scenario)
    _read_overview(harness, scenario=scenario)
    token_payload = _read_token(harness, token, scenario=scenario)
    buy_quote: dict[str, object] | None = None
    if token_payload is not None:
        buy_quote = _read_quote(
            harness,
            token,
            side="buy",
            amount_usd=harness.config.order_usd,
            quantity=None,
            scenario=scenario,
        )
    position = _position_for(harness, token, scenario=scenario)
    sell_quote: dict[str, object] | None = None
    if position is not None:
        quantity = _dec(position.get("quantity"))
        market_value = _dec(position.get("market_value_usd"))
        if quantity is not None and quantity > 0:
            amount = harness.config.order_usd
            if market_value is not None and market_value > 0:
                amount = min(amount, market_value)
            sell_quote = _read_quote(
                harness,
                token,
                side="sell",
                amount_usd=amount,
                quantity=quantity,
                scenario=scenario,
            )
    harness.register_invariants(*MARKET_INVARIANTS)
    if buy_quote is None and sell_quote is None:
        harness.skip_invariant("quote.shape")


def _strategy_params(kind: str, price: Decimal | None, order_usd: Decimal) -> dict[str, object]:
    reference = price if price is not None and price > 0 else Decimal("1")
    common: dict[str, object] = {
        "order_usd": str(order_usd),
        "interval_minutes": 1,
        "max_position_usd": str(max(order_usd * 10, Decimal("10"))),
    }
    if kind == "dca":
        return common
    if kind == "momentum":
        return {
            **common,
            "lookback": "1h",
            "entry_change_pct": "5",
            "exit_change_pct": "-3",
            "take_profit_pct": "20",
            "stop_loss_pct": "8",
        }
    if kind == "dip":
        return {
            **common,
            "lookback": "1h",
            "entry_change_pct": "5",
            "take_profit_pct": "20",
            "stop_loss_pct": "8",
        }
    quantum = Decimal("0.00000001")
    buy_below = max(quantum, (reference * 2).quantize(quantum))
    sell_above = max(buy_below * 2, (reference * 4).quantize(quantum))
    return {
        **common,
        "buy_below_usd": str(buy_below),
        "sell_above_usd": str(sell_above),
    }


def _delete_strategy(harness: Harness, strategy_id: str, *, scenario: str) -> bool:
    response = harness.request(
        "DELETE",
        f"/api/v1/strategies/{urlquote(strategy_id, safe='')}",
        scenario=scenario,
        expected=(204, 404),
        allow_status=(404,),
    )
    if response is None:
        return False
    if response.status_code in {204, 404}:
        harness.note_strategy_deleted(strategy_id)
        return True
    return False


def _strategy_lifecycle(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    harness.register_invariants(*STRATEGY_INVARIANTS)
    name = f"stress-{harness.config.mode}-{harness.config.seed}-{harness.run_tag}"[:120]
    body: dict[str, object] = {
        "name": name,
        "chain": token.chain,
        "token_address": token.address,
        "token_symbol": token.symbol or "STRESS",
        "kind": harness.config.strategy_kind,
        "params": _strategy_params(
            harness.config.strategy_kind, token.price_usd, harness.config.strategy_order_usd
        ),
        "status": "active",
    }
    created = harness.request(
        "POST",
        "/api/v1/strategies",
        scenario=scenario,
        json_body=body,
        expected=(201,),
    )
    strategy = harness.json_object(created, scenario=scenario, label="strategy")
    if strategy is None:
        harness.skip_invariant("strategy.run_log_non_empty")
        harness.add_finding(
            Finding(
                code="STRATEGY_CREATE_FAILED",
                scenario=scenario,
                invariant="strategies.crud",
                summary="strategy creation did not return a strategy",
                expected="HTTP 201 with a strategy id",
                actual="no readable body",
            )
        )
        return
    strategy_id = _str(strategy.get("id"))
    if strategy_id is None:
        harness.add_finding(
            Finding(
                code="STRATEGY_ID_MISSING",
                scenario=scenario,
                invariant="strategies.crud",
                summary="created strategy has no id",
                expected="non-empty id",
                actual="missing",
            )
        )
        return
    harness.note_strategy(strategy_id)
    harness.add_findings(check_strategy(strategy, scenario=scenario, expected_status="active"))
    path = f"/api/v1/strategies/{urlquote(strategy_id, safe='')}"

    try:
        fetched = harness.request("GET", path, scenario=scenario)
        fetched_payload = harness.json_object(fetched, scenario=scenario, label="strategy")
        if fetched_payload is not None:
            harness.add_findings(
                check_strategy(fetched_payload, scenario=scenario, expected_status="active")
            )

        paused = harness.request(
            "PATCH",
            path,
            scenario=scenario,
            json_body={"status": "paused"},
        )
        paused_payload = harness.json_object(paused, scenario=scenario, label="strategy patch")
        if paused_payload is not None:
            harness.add_findings(
                check_strategy(paused_payload, scenario=scenario, expected_status="paused")
            )

        paused_run = harness.request(
            "POST",
            f"{path}/run",
            scenario=scenario,
            expected=(409,),
            allow_status=(409,),
        )
        if paused_run is not None and paused_run.status_code != 409:
            harness.add_finding(
                Finding(
                    code="STRATEGY_PAUSED_RUN_ALLOWED",
                    scenario=scenario,
                    invariant="strategies.crud",
                    summary="a paused strategy accepted a manual run",
                    expected="HTTP 409 STRATEGY_PAUSED",
                    actual=f"HTTP {paused_run.status_code}",
                )
            )

        resumed = harness.request(
            "PATCH",
            path,
            scenario=scenario,
            json_body={"status": "active"},
        )
        resumed_payload = harness.json_object(resumed, scenario=scenario, label="strategy patch")
        if resumed_payload is not None:
            harness.add_findings(
                check_strategy(resumed_payload, scenario=scenario, expected_status="active")
            )

        run_response = harness.request("POST", f"{path}/run", scenario=scenario)
        run_payload = harness.json_object(run_response, scenario=scenario, label="strategy run")
        if run_payload is not None:
            action = _str(run_payload.get("action"))
            if action not in {"buy", "sell", "hold"}:
                harness.add_finding(
                    Finding(
                        code="STRATEGY_RUN_SHAPE",
                        scenario=scenario,
                        invariant="strategy.run_log_non_empty",
                        summary="strategy run returned an unknown action",
                        expected="buy, sell or hold",
                        actual=repr(run_payload.get("action")),
                    )
                )
            if not _str(run_payload.get("id")):
                harness.add_finding(
                    Finding(
                        code="STRATEGY_RUN_SHAPE",
                        scenario=scenario,
                        invariant="strategy.run_log_non_empty",
                        summary="strategy run returned no run id",
                        expected="non-empty run id",
                        actual=repr(run_payload.get("id")),
                    )
                )

        runs_response = harness.request("GET", f"{path}/runs", scenario=scenario)
        runs = harness.json_list(runs_response, scenario=scenario, label="strategy runs") or []
        harness.add_findings(check_strategy_run(runs, scenario=scenario))
        orders_response = harness.request(
            "GET", "/api/v1/paper/orders?limit=200", scenario=scenario
        )
        orders = harness.json_list(orders_response, scenario=scenario, label="paper orders") or []
        harness.add_findings(check_strategy_run_orders(runs, orders, scenario=scenario))
    finally:
        _delete_strategy(harness, strategy_id, scenario=scenario)
        gone = harness.request(
            "GET",
            path,
            scenario=scenario,
            expected=(404,),
            allow_status=(404,),
        )
        if gone is not None and gone.status_code != 404:
            harness.add_finding(
                Finding(
                    code="STRATEGY_DELETE_NOT_EFFECTIVE",
                    scenario=scenario,
                    invariant="strategies.crud",
                    summary="deleted strategy is still readable",
                    expected="HTTP 404 after DELETE",
                    actual=f"HTTP {gone.status_code}",
                )
            )


def cleanup_strategies(harness: Harness, *, scenario: str) -> None:
    """Best-effort cleanup for anything a crash left behind."""
    for strategy_id in harness.created_strategy_ids:
        if strategy_id in harness.deleted_strategy_ids:
            continue
        if not _delete_strategy(harness, strategy_id, scenario=scenario):
            harness.add_finding(
                Finding(
                    code="STRATEGY_CLEANUP_FAILED",
                    scenario=scenario,
                    invariant="strategies.crud",
                    summary="the harness could not delete a strategy it created",
                    expected="DELETE returns 204 or 404",
                    actual=strategy_id,
                )
            )


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def _full_flow(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    _market_checks(harness, token, scenario=scenario)
    order = _post_order(
        harness,
        token,
        side="buy",
        amount_usd=harness.config.order_usd,
        idempotency_key=harness.next_id(f"{scenario}-buy"),
        scenario=scenario,
    )
    if order is not None:
        _maybe_sell(harness, token, scenario=scenario)
    _strategy_lifecycle(harness, token, scenario=scenario)
    harness.check_paper_snapshot(scenario=scenario)


def _smoke(harness: Harness, token: ResolvedToken) -> None:
    scenario = "smoke"
    _full_flow(harness, token, scenario=scenario)
    deadline = harness.started_monotonic + harness.config.duration
    while time.monotonic() < deadline:
        _read_markets(harness, chain="all", limit=5, scenario=scenario)
        _read_quote(
            harness,
            token,
            side="buy",
            amount_usd=harness.config.order_usd,
            quantity=None,
            scenario=scenario,
        )
        harness.request("GET", "/api/v1/paper/account", scenario=scenario)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1.0, remaining))
    harness.check_paper_snapshot(scenario=scenario)


def _soak_worker(harness: Harness, token: ResolvedToken, worker: int, deadline: float) -> None:
    scenario = "soak"
    rng = random.Random(harness.config.seed * 1000 + worker)
    try:
        while time.monotonic() < deadline:
            if harness.reserve_order_slot():
                side = "buy"
                position = _position_for(harness, token, scenario=scenario)
                if position is not None and rng.random() < 0.5:
                    side = "sell"
                amount = harness.config.order_usd
                if side == "sell" and position is not None:
                    market_value = _dec(position.get("market_value_usd"))
                    if market_value is not None and market_value > 0:
                        amount = min(amount, market_value)
                _post_order(
                    harness,
                    token,
                    side=side,
                    amount_usd=amount,
                    idempotency_key=harness.next_id(f"soak-{worker}-{side}"),
                    scenario=scenario,
                )
            if rng.random() < 0.3:
                _read_quote(
                    harness,
                    token,
                    side="buy",
                    amount_usd=harness.config.order_usd,
                    quantity=None,
                    scenario=scenario,
                )
            if rng.random() < 0.2:
                _read_markets(harness, chain="all", limit=5, scenario=scenario)
            time.sleep(rng.uniform(0.05, 0.2))
    finally:
        harness.close_thread_clients()


def _fills_for_order(harness: Harness, order_id: str, *, scenario: str) -> list[dict[str, object]]:
    response = harness.request(
        "GET",
        "/api/v1/paper/fills",
        scenario=scenario,
        params={"limit": 200},
    )
    rows = harness.json_list(response, scenario=scenario, label="paper fills") or []
    return [row for row in rows if row.get("order_id") == order_id]


def _check_idempotent_pair(
    harness: Harness,
    first: Mapping[str, object] | None,
    second: Mapping[str, object] | None,
    *,
    scenario: str,
) -> None:
    if first is None or second is None:
        harness.skip_invariant("idempotency.same_order")
        harness.skip_invariant("idempotency.at_most_one_fill")
        return
    order_id = _str(first.get("id"))
    fills = _fills_for_order(harness, order_id, scenario=scenario) if order_id else []
    harness.add_findings(check_idempotency(first, second, fills, scenario=scenario))
    harness.register_invariants(*IDEMPOTENCY_INVARIANTS)
    harness.unskip_invariant("idempotency.same_order")
    harness.unskip_invariant("idempotency.at_most_one_fill")


def _idempotency_worker(
    harness: Harness, token: ResolvedToken, worker: int, deadline: float
) -> None:
    scenario = "idempotency"
    rng = random.Random(harness.config.seed * 1000 + 500 + worker)
    try:
        iteration = 0
        while time.monotonic() < deadline:
            if not harness.reserve_order_slot():
                time.sleep(0.2)
                continue
            key = harness.next_id(f"idem-{worker}-{iteration}")
            if iteration == 0:
                # Race the same key from two clients: exactly one order may exist.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_future = pool.submit(
                        _post_order,
                        harness,
                        token,
                        side="buy",
                        amount_usd=harness.config.order_usd,
                        idempotency_key=key,
                        scenario=scenario,
                        reserve_slot=False,
                    )
                    second_future = pool.submit(
                        _post_order,
                        harness,
                        token,
                        side="buy",
                        amount_usd=harness.config.order_usd,
                        idempotency_key=key,
                        scenario=scenario,
                        reserve_slot=False,
                    )
                    _check_idempotent_pair(
                        harness, first_future.result(), second_future.result(), scenario=scenario
                    )
            else:
                first = _post_order(
                    harness,
                    token,
                    side="buy",
                    amount_usd=harness.config.order_usd,
                    idempotency_key=key,
                    scenario=scenario,
                    reserve_slot=False,
                )
                second = _post_order(
                    harness,
                    token,
                    side="buy",
                    amount_usd=harness.config.order_usd,
                    idempotency_key=key,
                    scenario=scenario,
                    reserve_slot=False,
                )
                _check_idempotent_pair(harness, first, second, scenario=scenario)
            iteration += 1
            harness.request("GET", "/api/v1/paper/account", scenario=scenario)
            time.sleep(rng.uniform(0.05, 0.15))
    finally:
        harness.close_thread_clients()


def _chaos_worker(harness: Harness, worker: int, deadline: float) -> None:
    scenario = "chaos"
    try:
        while time.monotonic() < deadline:
            for attempt in range(3):
                response = harness.request(
                    "GET",
                    "/api/v1/__stress_invalid__",
                    scenario=scenario,
                    expected=(404,),
                    allow_status=(404,),
                    path_ok=True,
                )
                if response is not None and response.status_code == 404:
                    break
                time.sleep(0.05 * (2**attempt))

            # A closed loopback port: connection refused, retried with backoff. This is the
            # only time a bad origin is used, and the error is recorded as expected.
            for attempt in range(3):
                response = harness.request(
                    "GET",
                    "/health",
                    scenario=scenario,
                    origin="http://127.0.0.1:1",
                    timeout_s=0.5,
                    allow_error=True,
                    path_ok=True,
                )
                if response is not None:
                    harness.add_finding(
                        Finding(
                            code="CHAOS_BAD_URL_SUCCEEDED",
                            scenario=scenario,
                            invariant="chaos.bad_url",
                            summary="the unreachable control URL unexpectedly answered",
                            expected="connection failure",
                            actual=f"HTTP {response.status_code}",
                        )
                    )
                    break
                time.sleep(0.05 * (2**attempt))

            for _ in range(3):
                harness.request("GET", "/health", scenario=scenario)
                harness.request("GET", "/api/v1/paper/account", scenario=scenario)
            time.sleep(0.1 + worker * 0.01)
    finally:
        harness.close_thread_clients()


def _run_workers(
    harness: Harness,
    worker: Callable[[Harness, int, float], None],
    *,
    count: int,
    deadline: float,
    scenario: str,
) -> None:
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures: dict[Future[None], int] = {
            pool.submit(worker, harness, index, deadline): index for index in range(count)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - record, do not lose the run
                harness.add_finding(
                    Finding(
                        code="WORKER_EXCEPTION",
                        scenario=scenario,
                        invariant="harness.completed",
                        summary=f"worker {index} raised an exception",
                        expected="workers run to completion",
                        actual=f"{type(exc).__name__}: {exc}",
                    )
                )


def _soak(harness: Harness, token: ResolvedToken) -> None:
    _market_checks(harness, token, scenario="soak")
    _strategy_lifecycle(harness, token, scenario="soak")
    deadline = harness.started_monotonic + harness.config.duration
    _run_workers(
        harness,
        lambda h, worker, until: _soak_worker(h, token, worker, until),
        count=harness.config.concurrency,
        deadline=deadline,
        scenario="soak",
    )
    harness.check_paper_snapshot(scenario="soak")


def _idempotency(harness: Harness, token: ResolvedToken) -> None:
    harness.register_invariants(*IDEMPOTENCY_INVARIANTS)
    _market_checks(harness, token, scenario="idempotency")
    _strategy_lifecycle(harness, token, scenario="idempotency")
    deadline = harness.started_monotonic + harness.config.duration
    _run_workers(
        harness,
        lambda h, worker, until: _idempotency_worker(h, token, worker, until),
        count=harness.config.concurrency,
        deadline=deadline,
        scenario="idempotency",
    )
    harness.check_paper_snapshot(scenario="idempotency")


def _chaos(harness: Harness, token: ResolvedToken) -> None:
    harness.register_invariants("chaos.invalid_path", "chaos.bad_url", "chaos.state_intact")
    harness.note(CHAOS_SCENARIO_NOTE)
    _market_checks(harness, token, scenario="chaos")
    _post_order(
        harness,
        token,
        side="buy",
        amount_usd=harness.config.order_usd,
        idempotency_key=harness.next_id("chaos-buy"),
        scenario="chaos",
    )
    deadline = harness.started_monotonic + harness.config.duration
    _run_workers(
        harness,
        lambda h, worker, until: _chaos_worker(h, worker, until),
        count=harness.config.concurrency,
        deadline=deadline,
        scenario="chaos",
    )
    harness.check_paper_snapshot(scenario="chaos")
    account_response = harness.request("GET", "/api/v1/paper/account", scenario="chaos")
    account = harness.json_object(
        account_response, scenario="chaos", label="paper account after chaos"
    )
    if account is not None and account.get("mode") != "paper":
        harness.add_finding(
            Finding(
                code="CHAOS_STATE_CHANGED",
                scenario="chaos",
                invariant="chaos.state_intact",
                summary="paper mode changed during the chaos scenario",
                expected="mode='paper' after the failure windows",
                actual=repr(account.get("mode")),
            )
        )


# ---------------------------------------------------------------------------
# Token discovery and run entrypoint
# ---------------------------------------------------------------------------


def resolve_token(harness: Harness) -> ResolvedToken | None:
    scenario = harness.config.mode
    chains = _read_chains(harness, scenario=scenario)
    paper_chains: set[str] = set()
    for chain_row in chains:
        capabilities = _mapping(chain_row.get("capabilities"))
        chain_id = _str(chain_row.get("id"))
        if chain_id is not None and capabilities.get("paper_execute") is True:
            paper_chains.add(chain_id)

    chain_query = harness.config.chain or "all"
    payload = _read_markets(harness, chain=chain_query, limit=50, scenario=scenario)
    rows = _rows(payload.get("rows")) if payload is not None else []
    candidates: list[tuple[str, str, str, Decimal | None]] = []
    for item in rows:
        row = _mapping(item)
        row_chain = _str(row.get("chain"))
        row_address = _str(row.get("base_token"))
        if row_chain is None or row_address is None or len(row_address) < 8:
            continue
        row_price = _dec(row.get("price_usd"))
        candidates.append((row_chain, row_address, _str(row.get("symbol")) or "STRESS", row_price))

    if harness.config.token is not None:
        assert harness.config.chain is not None
        configured_address = harness.config.token
        configured_symbol = harness.config.token_symbol
        configured_price: Decimal | None = None
        for candidate_chain, candidate, candidate_symbol, candidate_price in candidates:
            if candidate_chain == harness.config.chain and candidate == configured_address:
                configured_symbol = configured_symbol or candidate_symbol
                configured_price = candidate_price
                break
        return ResolvedToken(
            chain=harness.config.chain,
            address=configured_address,
            symbol=configured_symbol or "STRESS",
            price_usd=configured_price,
        )

    preferred = [row for row in candidates if row[0] in paper_chains]
    chosen = preferred[0] if preferred else (candidates[0] if candidates else None)
    if chosen is None:
        harness.add_finding(
            Finding(
                code="TOKEN_DISCOVERY",
                scenario=scenario,
                invariant="markets.shape",
                summary="no usable token could be discovered from the market feed",
                expected="at least one market row with a chain, base_token and price",
                actual=f"{len(candidates)} usable row(s) from {len(rows)} row(s)",
            )
        )
        return None
    chain, address, symbol, price = chosen
    token_symbol = harness.config.token_symbol or symbol
    return ResolvedToken(chain=chain, address=address, symbol=token_symbol, price_usd=price)


def execute(config: RunConfig, *, command: str) -> ExecutionResult:
    """Run one scenario, write evidence, and return the report and its artifacts."""
    harness = Harness(config)
    started_at = datetime.now(UTC)
    token: ResolvedToken | None = None
    error: str | None = None

    try:
        preflight(harness)
    except PreflightError:
        harness.close_thread_clients()
        raise

    try:
        token = resolve_token(harness)
        if token is None:
            pass
        elif config.mode == "smoke":
            _smoke(harness, token)
        elif config.mode == "soak":
            _soak(harness, token)
        elif config.mode == "idempotency":
            _idempotency(harness, token)
        elif config.mode == "chaos":
            _chaos(harness, token)
        else:  # pragma: no cover - RunConfig rejects unknown modes
            raise ValueError(f"unknown mode {config.mode}")
    except Exception as exc:  # noqa: BLE001 - the report is the evidence, keep going
        error = f"{type(exc).__name__}: {exc}"
        harness.add_finding(
            Finding(
                code="HARNESS_EXCEPTION",
                scenario=config.mode,
                invariant="harness.completed",
                summary="the harness stopped on an unexpected exception",
                expected="the scenario completes",
                actual=error,
            )
        )
    finally:
        try:
            cleanup_strategies(harness, scenario=config.mode)
        except Exception as exc:  # noqa: BLE001 - cleanup must not hide the original error
            harness.add_finding(
                Finding(
                    code="STRATEGY_CLEANUP_FAILED",
                    scenario=config.mode,
                    invariant="strategies.crud",
                    summary="cleanup raised an exception",
                    expected="cleanup completes",
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )

    harness.finish_checks(scenario=config.mode)
    harness.close_thread_clients()
    finished_at = datetime.now(UTC)
    report = build_report(
        config=config,
        harness=harness,
        token_info=token.as_dict() if token is not None else None,
        command=command,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
    )
    json_path, markdown_path = write_reports(report, config.report_dir)
    return ExecutionResult(
        report=report,
        json_path=json_path,
        markdown_path=markdown_path,
        findings=tuple(harness.findings),
    )
