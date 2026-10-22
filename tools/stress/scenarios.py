"""Scenario orchestration for the PAPER stress harness.

Every mode shares the same safety preflight and the same invariant snapshot. The modes
differ only in how they generate load:

* ``smoke`` — one full flow, then low-rate reads for the duration.
* ``soak`` — concurrent buy/sell/read workers for the duration.
* ``idempotency`` — the same key twice, sequentially and concurrently.
* ``chaos`` — retry/backoff against an invalid path and an unreachable loopback port.
* ``matrix`` — one buy/sell round trip on every chain that advertises paper execution.
* ``fuzz`` — invalid mutations must be refused, never 5xx and never a fill.
* ``race`` — concurrent full-size sells may never oversell a position.
* ``killswitch`` — the operator switch blocks strategies, spares manual orders, and is
  restored to its previous state.
* ``all`` — smoke, idempotency, fuzz, race, killswitch and matrix in one run. ``soak`` and
  ``chaos`` stay explicit because they are long and deliberately degrade.

Nothing here kills a process, injects a server fault or talks to a live venue. Every run
also recomputes its own ledger effect from its own fills (:mod:`tools.stress.ledger`).
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from urllib.parse import quote as urlquote

import httpx

from .checks import (
    AUTH_INVARIANTS,
    FUZZ_INVARIANTS,
    IDEMPOTENCY_INVARIANTS,
    KILLSWITCH_INVARIANTS,
    LEDGER_INVARIANTS,
    MARKET_INVARIANTS,
    MATRIX_INVARIANTS,
    QUANTITY_TOLERANCE,
    RACE_INVARIANTS,
    STRATEGY_INVARIANTS,
    Finding,
    check_account,
    check_chains,
    check_idempotency,
    check_killswitch_allows_manual_order,
    check_killswitch_run,
    check_killswitch_state,
    check_markets,
    check_order,
    check_oversell,
    check_position_closed,
    check_quote,
    check_strategy,
    check_strategy_run,
    check_strategy_run_orders,
    check_token,
)
from .config import RunConfig
from .fuzz import (
    FuzzCase,
    check_auth_control,
    check_auth_response,
    check_delete_twice,
    check_fuzz_response,
    order_fuzz_cases,
    parse_error_payload,
    strategy_fuzz_cases,
)
from .http import AuthMode, Harness
from .ledger import (
    SKIP_CONCURRENT_WRITER,
    PositionKey,
    check_ledger_recompute,
    position_quantities,
)
from .ledger import (
    summarize as summarize_ledger,
)
from .matrix import (
    STATUS_FAILED,
    STATUS_NO_DATA,
    STATUS_OK,
    ChainRow,
    documented_rejection,
    order_outcome,
    paper_chain_ids,
    pick_most_liquid,
    quote_kind_for,
    rejected_skip_status,
)
from .report import build_report, write_reports

CHAOS_SCENARIO_NOTE = (
    "chaos never injects a 5xx or kills a process; it only retries an invalid path and an "
    "unreachable loopback port, then proves the paper account is still readable"
)

# ``--mode all``. Order matters: the cheap deterministic checks run before the matrix,
# which is the slowest because it talks to every chain's market data.
ALL_MODE_SEQUENCE = ("smoke", "idempotency", "fuzz", "race", "killswitch", "matrix")
STRATEGY_KIND_SEQUENCE = ("momentum", "dip", "dca", "threshold")
# Backoff for an upstream 429 (venue adapter throttle). Three attempts, ~3.5s total, then
# the throttle is reported instead of being retried forever.
THROTTLE_BACKOFF_S = (0.5, 1.0, 2.0)


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
    harness.register_invariants(
        "preflight.health", "preflight.paper_mode", "preflight.state_readable"
    )
    harness.add_findings(check_account(account_payload, scenario="preflight"))

    # Fail closed on an unreadable paper state: a corrupted account (for example a negative
    # position left by an earlier oversell) makes every later read a 5xx, which would drown
    # the run in cascade findings instead of naming the real problem.
    for path, label in (
        ("/api/v1/paper/positions", "positions"),
        ("/api/v1/paper/fills", "fills"),
    ):
        response = harness.request(
            "GET",
            path,
            scenario="preflight",
            params={"limit": 200} if label == "fills" else None,
        )
        rows = harness.json_list(response, scenario="preflight", label=f"paper {label}")
        if rows is None:
            harness.skip_invariant("preflight.state_readable")
            raise PreflightError(
                f"PREFLIGHT_FAILED: GET {path} did not return a readable JSON list; the "
                "paper account is inconsistent (see GET /api/v1/paper/reconciliation) and "
                "no stress run can be trusted against it"
            )


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
        allow_status=(409, 429),
    )
    # 429 here is the venue adapter reporting an upstream throttle, not a rejected order.
    # The same idempotency key is retried with backoff so a throttle is not reported as a
    # product defect. A throttle that outlives the backoff is left in the metrics.
    for delay in THROTTLE_BACKOFF_S:
        if response is None or response.status_code != 429:
            break
        harness.note(f"paper {side} throttled upstream (HTTP 429); retrying in {delay}s")
        time.sleep(delay)
        response = harness.request(
            "POST",
            "/api/v1/paper/orders",
            scenario=scenario,
            json_body=body,
            expected=(201,),
            allow_status=(409, 429),
        )
    if response is None:
        return None
    if response.status_code == 429:
        harness.note(
            f"paper {side} still throttled upstream after {len(THROTTLE_BACKOFF_S)} retries"
        )
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


def _sell_full_quantity_amount(position: Mapping[str, object]) -> Decimal | None:
    """The notional that closes a position: its mark value, rounded *up* to cents.

    ``PaperOrderIn`` quantizes ``amount_usd`` to cents, so rounding down would leave dust
    behind and the position would never close. Ceiling is the only safe direction.
    """
    quantity = _dec(position.get("quantity"))
    if quantity is None or quantity <= 0:
        return None
    value = _dec(position.get("market_value_usd"))
    if value is None or value <= 0:
        mark = _dec(position.get("mark_usd"))
        if mark is None or mark <= 0:
            return None
        value = quantity * mark
    cents = value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return max(Decimal("0.01"), cents)


def _sell_full_position(
    harness: Harness,
    token: ResolvedToken,
    *,
    scenario: str,
    idempotency_key: str,
    reserve_slot: bool = True,
) -> dict[str, object] | None:
    """Sell the whole position observed for ``token``, or return ``None``."""
    position = _position_for(harness, token, scenario=scenario)
    if position is None:
        harness.note(f"sell skipped: no open position in {token.chain}:{token.address}")
        return None
    amount = _sell_full_quantity_amount(position)
    if amount is None:
        harness.note(f"sell skipped: position {token.chain}:{token.address} has no usable mark")
        return None
    return _post_order(
        harness,
        token,
        side="sell",
        amount_usd=amount,
        idempotency_key=idempotency_key,
        scenario=scenario,
        reserve_slot=reserve_slot,
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


def killswitch_params(
    price: Decimal | None,
    order_usd: Decimal,
    *,
    holding: bool,
) -> dict[str, object]:
    """Threshold params that make the strategy *want* to trade, so the switch is exercised.

    The engine only reaches the kill-switch guard when the strategy decides ``buy`` or
    ``sell``; a threshold strategy with no signal holds with ``THRESHOLD_WAIT`` and proves
    nothing. With a position open the strategy must want to sell (``sell_above_usd`` below
    the mark), with no position it must want to buy (``buy_below_usd`` above the mark).
    ``sell_above_usd`` is always greater than ``buy_below_usd`` because the API rejects
    anything else.
    """
    reference = price if price is not None and price > 0 else Decimal("1")
    quantum = Decimal("0.00000001")
    if holding:
        buy_below = max(quantum, (reference / 4).quantize(quantum))
        sell_above = max(buy_below * 2, (reference / 2).quantize(quantum))
    else:
        buy_below = max(quantum, (reference * 2).quantize(quantum))
        sell_above = max(buy_below * 2, (reference * 4).quantize(quantum))
    return {
        "order_usd": str(order_usd),
        "interval_minutes": 1,
        "max_position_usd": str(max(order_usd * 10, Decimal("10"))),
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


def _strategy_lifecycle(
    harness: Harness, token: ResolvedToken, *, scenario: str, kind: str
) -> None:
    harness.register_invariants(*STRATEGY_INVARIANTS)
    name = f"stress-{scenario}-{kind}-{harness.config.seed}-{harness.run_tag}"[:120]
    body: dict[str, object] = {
        "name": name,
        "chain": token.chain,
        "token_address": token.address,
        "token_symbol": token.symbol or "STRESS",
        "kind": kind,
        "params": _strategy_params(kind, token.price_usd, harness.config.strategy_order_usd),
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
        _note_strategy_order_ids(harness, runs)
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


def _note_strategy_order_ids(harness: Harness, runs: Sequence[Mapping[str, object]]) -> None:
    """Attribute strategy-originated orders to this run.

    A strategy run places its order *inside* the API, so the harness never sees that order
    id in a response to a request it made itself. The run log carries it, and the ledger
    recompute needs it to tell this run's fills apart from a stranger's.
    """
    for run in runs:
        order_id = _str(run.get("order_id"))
        if order_id is not None:
            harness.note_order(order_id)


def cleanup_strategies(harness: Harness, *, scenario: str) -> None:
    """Best-effort cleanup for anything a crash left behind."""
    for strategy_id in harness.created_strategy_ids:
        if strategy_id in harness.deleted_strategy_ids:
            continue
        # Attribute any order the strategy placed (run-now or the inline scheduler) before
        # the row is gone: the run log is the only place its order id is visible.
        path = f"/api/v1/strategies/{urlquote(strategy_id, safe='')}/runs"
        runs_response = harness.request(
            "GET", path, scenario=scenario, allow_status=(404,), allow_error=True
        )
        runs = harness.json_list(runs_response, scenario=scenario, label="strategy runs") or []
        _note_strategy_order_ids(harness, runs)
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


def strategy_kinds(harness: Harness) -> tuple[str, ...]:
    """``--strategy-kind all`` covers every rule kind in one run."""
    configured = harness.config.strategy_kind
    return STRATEGY_KIND_SEQUENCE if configured == "all" else (configured,)


def _strategy_lifecycles(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    for kind in strategy_kinds(harness):
        _strategy_lifecycle(harness, token, scenario=scenario, kind=kind)


# ---------------------------------------------------------------------------
# Ledger window: an independent recompute from this run's own fills
# ---------------------------------------------------------------------------


@dataclass
class _Observation:
    positions: dict[PositionKey, Decimal] = field(default_factory=dict)
    fills: list[dict[str, object]] = field(default_factory=list)
    fill_ids: set[str] = field(default_factory=set)
    filled_order_ids: set[str] = field(default_factory=set)
    cash: Decimal | None = None


class LedgerWindow:
    """Observe paper state before and after the run, then reconcile it independently.

    ``open`` records the position quantities, the fills and the cash balance. ``close``
    reads them again, attributes the difference to this run's fills and fails the run when
    the arithmetic does not hold. When another writer is visible in the window the
    recompute is skipped explicitly instead of reporting someone else's money as a bug.
    """

    def __init__(self, harness: Harness, *, scenario: str) -> None:
        self.harness = harness
        self.scenario = scenario
        self._before: _Observation | None = None

    def _observe(self) -> _Observation:
        harness = self.harness
        observation = _Observation()
        positions_response = harness.request(
            "GET", "/api/v1/paper/positions", scenario=self.scenario
        )
        positions = harness.json_list(
            positions_response, scenario=self.scenario, label="paper positions"
        )
        observation.positions = position_quantities(positions or [])

        fills_response = harness.request(
            "GET", "/api/v1/paper/fills", scenario=self.scenario, params={"limit": 200}
        )
        observation.fills = (
            harness.json_list(fills_response, scenario=self.scenario, label="paper fills") or []
        )
        observation.fill_ids = {
            fill_id for fill in observation.fills if (fill_id := _str(fill.get("id"))) is not None
        }

        orders_response = harness.request(
            "GET", "/api/v1/paper/orders", scenario=self.scenario, params={"limit": 200}
        )
        orders = (
            harness.json_list(orders_response, scenario=self.scenario, label="paper orders") or []
        )
        observation.filled_order_ids = {
            order_id
            for order in orders
            if _str(order.get("status")) == "filled"
            and (order_id := _str(order.get("id"))) is not None
        }

        account_response = harness.request("GET", "/api/v1/paper/account", scenario=self.scenario)
        account = harness.json_object(
            account_response, scenario=self.scenario, label="paper account"
        )
        if account is not None:
            observation.cash = _dec(account.get("cash_usd"))
        return observation

    def open(self) -> None:
        self._before = self._observe()

    def close(self) -> None:
        if self._before is None:  # pragma: no cover - close() always follows open()
            return
        harness = self.harness
        broken = harness.paper_state_broken
        if broken is not None:
            harness.register_invariants(*LEDGER_INVARIANTS)
            for identifier in LEDGER_INVARIANTS:
                harness.skip_invariant(identifier, f"paper state is inconsistent: {broken}")
            harness.note(f"ledger recompute skipped: paper state is inconsistent — {broken}")
            harness.record_row(
                "ledger",
                {
                    "skip": "paper_state_broken",
                    "detail": broken,
                    "before": {
                        f"{chain}:{token}": str(q)
                        for (chain, token), q in self._before.positions.items()
                    },
                },
            )
            return
        before = self._before
        after = self._observe()
        run_order_ids = self.harness.created_order_ids
        missing_fill = {
            order_id
            for order_id in after.filled_order_ids & run_order_ids
            if not any(fill.get("order_id") == order_id for fill in after.fills)
        }
        verdict = check_ledger_recompute(
            scenario=self.scenario,
            before=before.positions,
            after=after.positions,
            fills=after.fills,
            run_order_ids=run_order_ids,
            cash_before=before.cash,
            cash_after=after.cash,
            new_fill_ids=after.fill_ids - before.fill_ids,
            filled_orders_missing_fill=missing_fill,
        )
        self.harness.register_invariants(*LEDGER_INVARIANTS)
        self.harness.add_findings(verdict.findings)
        if verdict.skip is not None:
            for identifier in LEDGER_INVARIANTS:
                harness.skip_invariant(identifier, f"ledger recompute skipped: {verdict.skip}")
            hint = ""
            if verdict.skip == SKIP_CONCURRENT_WRITER:
                hint = (
                    "; if the API runs its engine inline (EZCHAIN_ENGINE_INLINE=true), "
                    "strategies created by this run can place their own orders and the "
                    "harness cannot attribute them — rerun with the inline scheduler "
                    "disabled for an isolated recompute"
                )
            harness.note(f"ledger recompute skipped: {verdict.skip} — {verdict.detail}{hint}")
        self.harness.record_row(
            "ledger",
            {
                "skip": verdict.skip,
                "detail": verdict.detail,
                "before": {
                    f"{chain}:{token}": str(q) for (chain, token), q in before.positions.items()
                },
                "after": {
                    f"{chain}:{token}": str(q) for (chain, token), q in after.positions.items()
                },
                "cash_before": None if before.cash is None else str(before.cash),
                "cash_after": None if after.cash is None else str(after.cash),
                **summarize_ledger(verdict),
            },
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
    _strategy_lifecycles(harness, token, scenario=scenario)
    harness.check_paper_snapshot(scenario=scenario)


def _smoke(harness: Harness, token: ResolvedToken) -> None:
    scenario = "smoke"
    _full_flow(harness, token, scenario=scenario)
    deadline = time.monotonic() + harness.config.duration
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
    _strategy_lifecycles(harness, token, scenario="soak")
    deadline = time.monotonic() + harness.config.duration
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
    _strategy_lifecycles(harness, token, scenario="idempotency")
    deadline = time.monotonic() + harness.config.duration
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
    deadline = time.monotonic() + harness.config.duration
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
# Fuzz: invalid mutations must be refused, never 5xx and never a fill
# ---------------------------------------------------------------------------

KILL_SWITCH_PATH = "/api/v1/system/kill-switch"
KILL_SWITCH_ARM_REASON = "stress harness: killswitch scenario (arming to prove the block)"
KILL_SWITCH_RESTORE_REASON = "stress harness: restoring the state observed before the run"


def _order_count(harness: Harness, *, scenario: str) -> int:
    """How many order rows the newest-200 window currently holds."""
    response = harness.request(
        "GET", "/api/v1/paper/orders", scenario=scenario, params={"limit": 200}
    )
    rows = harness.json_list(response, scenario=scenario, label="paper orders") or []
    return len(rows)


def _send_fuzz_case(harness: Harness, case: FuzzCase, *, scenario: str) -> httpx.Response | None:
    """Issue one fuzz request without letting the generic status check double-report."""
    return harness.request(
        case.method,
        case.path,
        scenario=scenario,
        json_body=case.body,
        content=case.raw_body,
        content_type=case.content_type if case.raw_body is not None else None,
        expected=(200, 201, 204),
        allow_status=tuple(range(400, 500)),
    )


def _fuzz_delete_twice(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    """Create, delete, delete again: the second delete must be a clean 404."""
    body: dict[str, object] = {
        "name": f"stress-fuzz-delete-{harness.run_tag}"[:120],
        "chain": token.chain,
        "token_address": token.address,
        "token_symbol": token.symbol or "STRESS",
        "kind": "dca",
        "params": {"order_usd": "1", "interval_minutes": 1},
        "status": "active",
    }
    created = harness.request(
        "POST", "/api/v1/strategies", scenario=scenario, json_body=body, expected=(201,)
    )
    strategy = harness.json_object(created, scenario=scenario, label="strategy")
    strategy_id = None if strategy is None else _str(strategy.get("id"))
    if strategy_id is None:
        harness.note("fuzz delete-twice skipped: the throwaway strategy was not created")
        harness.skip_invariant("fuzz.delete_twice")
        return
    harness.note_strategy(strategy_id)
    path = f"/api/v1/strategies/{urlquote(strategy_id, safe='')}"
    first = harness.request("DELETE", path, scenario=scenario, expected=(204,), allow_status=(404,))
    second = harness.request(
        "DELETE", path, scenario=scenario, expected=(404,), allow_status=(404,)
    )
    if first is not None and first.status_code in {200, 204}:
        harness.note_strategy_deleted(strategy_id)
    first_status = None if first is None else first.status_code
    second_status = None if second is None else second.status_code
    harness.add_findings(check_delete_twice(first_status, second_status, scenario=scenario))
    harness.record_row(
        "fuzz",
        {
            "case_id": "strategy_delete_twice",
            "description": "DELETE the same strategy twice",
            "expected": "204 then 404",
            "status": first_status,
            "ok": first_status in {200, 204} and second_status == 404,
            "detail": f"first HTTP {first_status}, second HTTP {second_status}",
        },
    )


def _fuzz(harness: Harness, token: ResolvedToken, *, scenario: str = "fuzz") -> None:
    harness.register_invariants(*FUZZ_INVARIANTS)
    key_prefix = f"stress-{harness.run_tag}"
    cases: list[FuzzCase] = [
        *order_fuzz_cases(chain=token.chain, token=token.address, key_prefix=key_prefix),
        *strategy_fuzz_cases(
            chain=token.chain,
            token=token.address,
            symbol=token.symbol or "STRESS",
            key_prefix=key_prefix,
        ),
    ]
    for case in cases:
        before = _order_count(harness, scenario=scenario)
        response = _send_fuzz_case(harness, case, scenario=scenario)
        after = _order_count(harness, scenario=scenario)
        status = None if response is None else response.status_code
        raw = "" if response is None else response.text
        payload = parse_error_payload(raw) if raw else None
        outcome, findings = check_fuzz_response(
            case,
            status=status,
            payload=payload,
            raw_text=raw,
            orders_before=before,
            orders_after=after,
            scenario=scenario,
        )
        harness.add_findings(findings)
        harness.record_row("fuzz", outcome.to_dict())
    _fuzz_delete_twice(harness, token, scenario=scenario)
    _auth_negatives(harness, token, scenario=scenario)


def _auth_negatives(harness: Harness, token: ResolvedToken, *, scenario: str) -> None:
    """The same valid mutation without/with a wrong token must be refused."""
    harness.register_invariants(*AUTH_INVARIANTS)
    if not harness.api_token_configured:
        for identifier in AUTH_INVARIANTS:
            harness.skip_invariant(identifier, "EZCHAIN_STRESS_API_TOKEN is not configured")
        harness.note(
            "auth negatives skipped: EZCHAIN_STRESS_API_TOKEN is not configured, so the "
            "harness cannot claim the API requires authentication"
        )
        harness.record_row(
            "auth",
            {
                "case": "missing",
                "status": None,
                "ok": True,
                "detail": "skipped: no EZCHAIN_STRESS_API_TOKEN configured",
            },
        )
        return

    def body(name: str) -> dict[str, object]:
        return {
            "chain": token.chain,
            "token_address": token.address,
            "side": "buy",
            "amount_usd": str(max(Decimal("0.01"), harness.config.order_usd)),
            "idempotency_key": harness.next_id(f"auth-{name}"),
        }

    attempts: tuple[tuple[str, AuthMode], ...] = (("missing", "omit"), ("wrong", "wrong"))
    for kind, auth in attempts:
        before = _order_count(harness, scenario=scenario)
        response = harness.request(
            "POST",
            "/api/v1/paper/orders",
            scenario=scenario,
            json_body=body(kind),
            expected=(401,),
            allow_status=(401, 403),
            auth=auth,
        )
        after = _order_count(harness, scenario=scenario)
        status = None if response is None else response.status_code
        raw = "" if response is None else response.text
        harness.add_findings(
            check_auth_response(
                kind,
                status=status,
                payload=parse_error_payload(raw) if raw else None,
                raw_text=raw,
                orders_before=before,
                orders_after=after,
                scenario=scenario,
            )
        )
        harness.record_row(
            "auth",
            {
                "case": kind,
                "status": status,
                "ok": status == 401 if kind == "missing" else status in {401, 403},
                "detail": f"HTTP {status}",
            },
        )

    # The control proves the body itself was valid, so a 401 was about the header. An
    # upstream throttle (429) says nothing about auth, so it is retried and then skipped
    # rather than reported as an auth failure.
    control = harness.request(
        "POST",
        "/api/v1/paper/orders",
        scenario=scenario,
        json_body=body("control"),
        expected=(201,),
        allow_status=(409, 429),
    )
    for delay in THROTTLE_BACKOFF_S:
        if control is None or control.status_code != 429:
            break
        harness.note(f"auth control throttled upstream (HTTP 429); retrying in {delay}s")
        time.sleep(delay)
        control = harness.request(
            "POST",
            "/api/v1/paper/orders",
            scenario=scenario,
            json_body=body("control"),
            expected=(201,),
            allow_status=(409, 429),
        )
    control_status = None if control is None else control.status_code
    if control_status == 429:
        harness.skip_invariant("auth.control_valid")
        harness.note(
            "auth control skipped: the API kept returning 429 (upstream throttle) after "
            "backoff, which cannot distinguish a valid token from a broken one"
        )
        harness.record_row(
            "auth",
            {
                "case": "control",
                "status": control_status,
                "ok": True,
                "detail": "skipped: upstream 429 after backoff",
            },
        )
        return
    harness.add_findings(check_auth_control(control_status, scenario=scenario))
    if control_status is not None and 200 <= control_status <= 299:
        control_payload = harness.json_object(control, scenario=scenario, label="control order")
        control_id = None if control_payload is None else _str(control_payload.get("id"))
        if control_id is not None:
            harness.note_order(control_id)
    harness.record_row(
        "auth",
        {
            "case": "control",
            "status": control_status,
            "ok": control_status is not None and 200 <= control_status <= 299,
            "detail": "same body with the configured token",
        },
    )


# ---------------------------------------------------------------------------
# Race: concurrent full-size sells may not oversell
# ---------------------------------------------------------------------------


def _paper_fills(harness: Harness, *, scenario: str) -> list[dict[str, object]]:
    response = harness.request(
        "GET", "/api/v1/paper/fills", scenario=scenario, params={"limit": 200}
    )
    return harness.json_list(response, scenario=scenario, label="paper fills") or []


def _race(harness: Harness, token: ResolvedToken, *, scenario: str = "race") -> None:
    harness.register_invariants(*RACE_INVARIANTS)
    position = _position_for(harness, token, scenario=scenario)
    if position is None:
        if not harness.reserve_race_slot():
            for identifier in RACE_INVARIANTS:
                harness.skip_invariant(identifier, "race order allowance exhausted")
            harness.note("race skipped: the race order allowance is exhausted")
            return
        _post_order(
            harness,
            token,
            side="buy",
            amount_usd=harness.config.order_usd,
            idempotency_key=harness.next_id("race-seed-buy"),
            scenario=scenario,
            reserve_slot=False,
        )
        position = _position_for(harness, token, scenario=scenario)
    held = None if position is None else _dec(position.get("quantity"))
    amount = None if position is None else _sell_full_quantity_amount(position)
    if position is None or held is None or held <= 0 or amount is None:
        for identifier in RACE_INVARIANTS:
            harness.skip_invariant(identifier, "no open position could be established")
        harness.note("race skipped: no open position could be established to sell")
        harness.record_row(
            "race",
            {"held_quantity": None, "sold_quantity": None, "ok": True, "detail": "skipped"},
        )
        return

    workers = max(2, min(harness.config.concurrency, 8))
    keys = [harness.next_id(f"race-sell-{index}") for index in range(workers)]
    # A race test that only sometimes lines its requests up is not a test. Every worker
    # opens its own connection first, then waits at a barrier so all sells are in flight
    # at the same instant.
    barrier = threading.Barrier(workers)

    def sell(index: int) -> dict[str, object] | None:
        harness.request("GET", "/api/v1/paper/account", scenario=scenario)
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:  # pragma: no cover - a worker died first
            harness.note("race: barrier broke; sells were not perfectly aligned")
        if not harness.reserve_race_slot():
            return None
        return _post_order(
            harness,
            token,
            side="sell",
            amount_usd=amount,
            idempotency_key=keys[index],
            scenario=scenario,
            reserve_slot=False,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(sell, index) for index in range(workers)]
        responses = [future.result() for future in futures]
    sell_order_ids = {
        order_id
        for order in responses
        if order is not None and (order_id := _str(order.get("id"))) is not None
    }
    if not sell_order_ids:
        # Nothing was even accepted: passing "no oversell" here would be a false green.
        for identifier in RACE_INVARIANTS:
            harness.skip_invariant(identifier, "no concurrent sell produced an order row")
        harness.note(
            "race skipped: none of the concurrent sells produced an order row (API refusal "
            "or harness allowance), so oversell was not actually exercised"
        )
        harness.record_row(
            "race",
            {
                "held_quantity": str(held),
                "concurrent_sells": workers,
                "filled_sell_orders": 0,
                "ok": True,
                "detail": "skipped: no sell order was created",
            },
        )
        harness.check_paper_snapshot(scenario=scenario)
        return
    sold = Decimal(0)
    for fill in _paper_fills(harness, scenario=scenario):
        if _str(fill.get("order_id")) in sell_order_ids:
            quantity = _dec(fill.get("quantity"))
            if quantity is not None:
                sold += quantity

    after_position = _position_for(harness, token, scenario=scenario)
    final_quantity = None if after_position is None else _dec(after_position.get("quantity"))
    account_response = harness.request("GET", "/api/v1/paper/account", scenario=scenario)
    account = harness.json_object(
        account_response, scenario=scenario, label="paper account after race"
    )
    cash = None if account is None else _dec(account.get("cash_usd"))

    harness.add_findings(
        check_oversell(
            scenario=scenario,
            held_quantity=held,
            sold_quantity=sold,
            final_quantity=final_quantity,
            cash_usd=cash,
        )
    )
    if (
        (final_quantity is not None and final_quantity < 0)
        or (cash is not None and cash < 0)
        or sold - held > QUANTITY_TOLERANCE
    ):
        # More was sold than was ever held, so the ledger now replays a negative position
        # and the API answers every later read with a 5xx. Further scenarios are skipped
        # with this reason instead of piling cascade findings on top of the real one.
        harness.mark_paper_state_broken(
            "the oversell race sold more than the held quantity; the paper ledger now "
            "contains a negative position and cannot be read consistently"
        )
    harness.record_row(
        "race",
        {
            "held_quantity": str(held),
            "attempted_sell_notional": str(amount),
            "concurrent_sells": workers,
            "filled_sell_orders": len(sell_order_ids),
            "sold_quantity": str(sold),
            "final_quantity": None if final_quantity is None else str(final_quantity),
            "cash_usd": None if cash is None else str(cash),
            "ok": sold - held <= Decimal("0.00000001"),
        },
    )
    harness.check_paper_snapshot(scenario=scenario)


# ---------------------------------------------------------------------------
# Kill switch: strategy blocked, manual order allowed, previous state restored
# ---------------------------------------------------------------------------


def _kill_switch_state(harness: Harness, *, scenario: str) -> dict[str, object] | None:
    response = harness.request("GET", KILL_SWITCH_PATH, scenario=scenario, expected=(200,))
    return harness.json_object(response, scenario=scenario, label="kill switch")


def _set_kill_switch(
    harness: Harness, *, active: bool, reason: str, scenario: str
) -> dict[str, object] | None:
    response = harness.request(
        "POST",
        KILL_SWITCH_PATH,
        scenario=scenario,
        json_body={"active": active, "reason": reason},
        expected=(200,),
    )
    return harness.json_object(response, scenario=scenario, label="kill switch update")


def _restore_kill_switch(
    harness: Harness,
    *,
    previous: Mapping[str, object],
    scenario: str,
) -> None:
    """Put the switch back exactly where it was, then prove it."""
    was_active = previous.get("active") is True
    previous_reason = _str(previous.get("reason")) or KILL_SWITCH_RESTORE_REASON
    state = _set_kill_switch(harness, active=was_active, reason=previous_reason, scenario=scenario)
    harness.add_findings(
        check_killswitch_state(state, expected_active=was_active, scenario=scenario, restored=True)
    )
    harness.record_row(
        "killswitch",
        {
            "step": "restored",
            "previous_active": was_active,
            "previous_reason": previous_reason,
            "restored_active": None if state is None else state.get("active"),
        },
    )


def _killswitch(harness: Harness, token: ResolvedToken, *, scenario: str = "killswitch") -> None:
    harness.register_invariants(*KILLSWITCH_INVARIANTS)
    previous = _kill_switch_state(harness, scenario=scenario)
    if previous is None:
        for identifier in KILLSWITCH_INVARIANTS:
            harness.skip_invariant(identifier, "kill switch state was unreadable")
        harness.note(
            "killswitch scenario skipped: GET /api/v1/system/kill-switch was unreadable, "
            "so the previous state could not be restored safely"
        )
        return

    previous_active = previous.get("active") is True
    previous_reason = _str(previous.get("reason"))
    harness.record_row(
        "killswitch",
        {
            "step": "observed",
            "previous_active": previous_active,
            "previous_reason": previous_reason,
        },
    )

    strategy_id: str | None = None
    try:
        armed = _set_kill_switch(
            harness, active=True, reason=KILL_SWITCH_ARM_REASON, scenario=scenario
        )
        harness.add_findings(check_killswitch_state(armed, expected_active=True, scenario=scenario))

        # The guard is only reached when the strategy decides to trade, so derive
        # thresholds from the live mark and the observed position: sell if we hold, buy if
        # we do not. Anything else logs THRESHOLD_WAIT and proves nothing about the switch.
        position = _position_for(harness, token, scenario=scenario)
        held_before = None if position is None else _dec(position.get("quantity"))
        holding = held_before is not None and held_before > 0
        body: dict[str, object] = {
            "name": f"stress-killswitch-{harness.run_tag}"[:120],
            "chain": token.chain,
            "token_address": token.address,
            "token_symbol": token.symbol or "STRESS",
            "kind": "threshold",
            "params": killswitch_params(
                token.price_usd, harness.config.strategy_order_usd, holding=holding
            ),
            "status": "active",
        }
        created = harness.request(
            "POST", "/api/v1/strategies", scenario=scenario, json_body=body, expected=(201,)
        )
        strategy = harness.json_object(created, scenario=scenario, label="strategy")
        strategy_id = None if strategy is None else _str(strategy.get("id"))
        if strategy_id is None:
            harness.add_finding(
                Finding(
                    code="KILLSWITCH_STRATEGY_MISSING",
                    scenario=scenario,
                    invariant="killswitch.blocks_strategy",
                    summary="the killswitch scenario could not create a strategy to block",
                    expected="HTTP 201 with a strategy id",
                    actual=f"HTTP {None if created is None else created.status_code}",
                )
            )
            return
        harness.note_strategy(strategy_id)
        path = f"/api/v1/strategies/{urlquote(strategy_id, safe='')}"

        orders_before = _order_count(harness, scenario=scenario)
        run_response = harness.request("POST", f"{path}/run", scenario=scenario)
        run_payload = harness.json_object(
            run_response, scenario=scenario, label="strategy run while armed"
        )
        runs_response = harness.request("GET", f"{path}/runs", scenario=scenario)
        persisted = harness.json_list(runs_response, scenario=scenario, label="strategy runs") or []
        orders_after = _order_count(harness, scenario=scenario)
        harness.add_findings(
            check_killswitch_run(
                run_payload,
                persisted_runs=persisted,
                orders_created=max(0, orders_after - orders_before),
                scenario=scenario,
            )
        )
        harness.record_row(
            "killswitch",
            {
                "step": "blocked",
                "action": None if run_payload is None else run_payload.get("action"),
                "reason": None if run_payload is None else run_payload.get("reason"),
                "attempted_action": None
                if run_payload is None
                else (_mapping(run_payload.get("inputs")) or {}).get("attempted_action"),
                "holding_before": None if held_before is None else str(held_before),
                "orders_created": max(0, orders_after - orders_before),
            },
        )

        manual_key = harness.next_id("killswitch-manual-buy")
        manual_amount = max(Decimal("0.01"), harness.config.order_usd).quantize(Decimal("0.01"))
        manual_response = harness.request(
            "POST",
            "/api/v1/paper/orders",
            scenario=scenario,
            json_body={
                "chain": token.chain,
                "token_address": token.address,
                "side": "buy",
                "amount_usd": str(manual_amount),
                "idempotency_key": manual_key,
            },
            expected=(201,),
            allow_status=(409,),
        )
        manual_status = None if manual_response is None else manual_response.status_code
        manual_order = (
            harness.json_object(manual_response, scenario=scenario, label="manual paper order")
            if manual_status == 201
            else None
        )
        if manual_order is not None:
            manual_order_id = _str(manual_order.get("id"))
            if manual_order_id is not None:
                harness.note_order(manual_order_id)
            harness.note_order_summary(manual_order)
            harness.add_findings(check_order(manual_order, scenario=scenario))
        harness.add_findings(
            check_killswitch_allows_manual_order(
                manual_order, status_code=manual_status, scenario=scenario
            )
        )
        harness.record_row(
            "killswitch",
            {
                "step": "manual_order",
                "status": None if manual_order is None else manual_order.get("status"),
                "reason": None if manual_order is None else manual_order.get("reason"),
                "status_code": manual_status,
                "detail": "manual orders are outside the strategy-scoped switch",
            },
        )
        harness.check_paper_snapshot(scenario=scenario)
    finally:
        if strategy_id is not None:
            _delete_strategy(harness, strategy_id, scenario=scenario)
        _restore_kill_switch(harness, previous=previous, scenario=scenario)


# ---------------------------------------------------------------------------
# Matrix: one buy/sell round trip per paper-capable chain
# ---------------------------------------------------------------------------


def _matrix_chain(
    harness: Harness,
    chain: str,
    chains: Sequence[Mapping[str, object]],
    *,
    scenario: str,
) -> ChainRow:
    quote_kind = quote_kind_for(chains, chain)
    payload = _read_markets(harness, chain=chain, limit=5, scenario=scenario)
    if payload is None:
        harness.note(f"matrix: markets unavailable for chain={chain} (recorded as no_data)")
        return ChainRow(
            chain=chain,
            status=STATUS_NO_DATA,
            quote_kind=quote_kind,
            reason="markets unavailable",
        )
    rows = [row for item in _rows(payload.get("rows")) if (row := _mapping(item))]
    if not rows:
        harness.note(f"matrix: no market rows for chain={chain} (recorded as no_data)")
        return ChainRow(
            chain=chain,
            status=STATUS_NO_DATA,
            quote_kind=quote_kind,
            reason="no market rows",
        )
    chosen = pick_most_liquid(rows)
    address = "" if chosen is None else (_str(chosen.get("base_token")) or "")
    if not address:
        return ChainRow(
            chain=chain,
            status=STATUS_NO_DATA,
            quote_kind=quote_kind,
            reason="no usable market row",
        )
    symbol = _str(chosen.get("symbol")) if chosen is not None else None
    resolved = ResolvedToken(
        chain=chain,
        address=address,
        symbol=symbol or "STRESS",
        price_usd=None if chosen is None else _dec(chosen.get("price_usd")),
    )
    label = f"{chain}:{address}"

    if _read_token(harness, resolved, scenario=scenario) is None:
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            reason="token read failed",
        )
    if (
        _read_quote(
            harness,
            resolved,
            side="buy",
            amount_usd=harness.config.order_usd,
            quantity=None,
            scenario=scenario,
        )
        is None
    ):
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            reason="no buy quote",
        )

    if not harness.reserve_matrix_slot():
        harness.note(f"matrix: matrix order allowance exhausted before chain={chain}")
        return ChainRow(
            chain=chain,
            status=STATUS_NO_DATA,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            reason="matrix allowance exhausted",
        )
    buy = _post_order(
        harness,
        resolved,
        side="buy",
        amount_usd=harness.config.order_usd,
        idempotency_key=harness.next_id(f"matrix-{chain}-buy"),
        scenario=scenario,
        reserve_slot=False,
    )
    buy_status, buy_reason = order_outcome(buy)
    if buy is None:
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy=buy_status,
            sell="not_attempted",
            reason="buy request failed",
        )
    if buy_status == "rejected":
        if documented_rejection(buy_reason):
            harness.note(f"matrix: buy on {label} rejected (documented): {buy_reason}")
            return ChainRow(
                chain=chain,
                status=rejected_skip_status(buy_reason),
                token=address,
                symbol=resolved.symbol,
                quote_kind=quote_kind,
                buy="rejected",
                sell="skipped",
                reason=buy_reason,
            )
        harness.add_finding(
            Finding(
                code="MATRIX_UNEXPLAINED_REJECTION",
                scenario=scenario,
                invariant="matrix.chain_flow",
                summary=f"the buy on {label} was rejected for an undocumented reason",
                expected="a filled order, or a documented risk reason",
                actual=f"reason={buy_reason!r}",
            )
        )
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy="rejected",
            sell="skipped",
            reason=buy_reason,
        )
    if buy_status != "filled":
        harness.add_finding(
            Finding(
                code="MATRIX_BUY_NOT_FILLED",
                scenario=scenario,
                invariant="matrix.chain_flow",
                summary=f"the buy on {label} did not fill",
                expected="status=filled",
                actual=f"status={buy_status!r} reason={buy_reason!r}",
            )
        )
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy=buy_status,
            sell="not_attempted",
            reason=buy_reason,
        )

    position = _position_for(harness, resolved, scenario=scenario)
    quantity = None if position is None else _dec(position.get("quantity"))
    if position is None or quantity is None or quantity <= 0:
        harness.add_finding(
            Finding(
                code="MATRIX_POSITION_MISSING",
                scenario=scenario,
                invariant="matrix.chain_flow",
                summary=f"the fill on {label} did not produce a position",
                expected="a position with quantity > 0 after a filled buy",
                actual="no position row",
            )
        )
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy="filled",
            sell="not_attempted",
            reason="no position after fill",
        )

    quote_amount = _sell_full_quantity_amount(position)
    if quote_amount is not None:
        _read_quote(
            harness,
            resolved,
            side="sell",
            amount_usd=quote_amount,
            quantity=quantity,
            scenario=scenario,
        )

    sell = _sell_full_position(
        harness,
        resolved,
        scenario=scenario,
        idempotency_key=harness.next_id(f"matrix-{chain}-sell"),
        reserve_slot=False,
    )
    sell_status, sell_reason = order_outcome(sell)
    if sell_status == "rejected" and documented_rejection(sell_reason):
        harness.note(f"matrix: sell on {label} rejected (documented): {sell_reason}")
        return ChainRow(
            chain=chain,
            status=rejected_skip_status(sell_reason),
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy="filled",
            sell="rejected",
            reason=sell_reason,
        )
    if sell_status != "filled":
        harness.add_finding(
            Finding(
                code="MATRIX_SELL_NOT_FILLED",
                scenario=scenario,
                invariant="matrix.chain_flow",
                summary=f"the full-size sell on {label} did not fill",
                expected="status=filled (a held position must always be exitable)",
                actual=f"status={sell_status!r} reason={sell_reason!r}",
            )
        )
        return ChainRow(
            chain=chain,
            status=STATUS_FAILED,
            token=address,
            symbol=resolved.symbol,
            quote_kind=quote_kind,
            buy="filled",
            sell=sell_status,
            reason=sell_reason or "sell did not fill",
        )

    after = _position_for(harness, resolved, scenario=scenario)
    final_quantity = None if after is None else _dec(after.get("quantity"))
    closed = check_position_closed(final_quantity, scenario=scenario, label=label)
    harness.add_findings(closed)
    return ChainRow(
        chain=chain,
        status=STATUS_OK if not closed else STATUS_FAILED,
        token=address,
        symbol=resolved.symbol,
        quote_kind=quote_kind,
        buy="filled",
        sell="filled",
        reason="" if not closed else f"position still holds {final_quantity}",
    )


def _matrix(harness: Harness, token: ResolvedToken | None, *, scenario: str = "matrix") -> None:
    harness.register_invariants(*MATRIX_INVARIANTS)
    chains = [_mapping(row) for row in _read_chains(harness, scenario=scenario)]
    chain_ids = paper_chain_ids(chains)
    if not chain_ids:
        harness.skip_invariant("matrix.chain_flow", "no chain advertises paper_execute")
        harness.note("matrix skipped: no chain advertises capabilities.paper_execute")
        return
    harness.note(f"matrix: {len(chain_ids)} paper-capable chain(s); one buy/sell round trip each")
    for chain in chain_ids:
        row = _matrix_chain(harness, chain, chains, scenario=scenario)
        harness.note(
            f"matrix {chain}: {row.status} buy={row.buy} sell={row.sell}"
            + (f" reason={row.reason}" if row.reason else "")
        )
        harness.record_row("matrix", row.to_dict())
    harness.check_paper_snapshot(scenario=scenario)


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


MODE_RUNNERS: dict[str, Callable[[Harness, ResolvedToken], None]] = {
    "smoke": _smoke,
    "soak": _soak,
    "idempotency": _idempotency,
    "chaos": _chaos,
    "fuzz": _fuzz,
    "race": _race,
    "killswitch": _killswitch,
}


def _mode_sequence(mode: str) -> tuple[str, ...]:
    """``--mode all`` is a fixed sequence; every other mode is itself."""
    return ALL_MODE_SEQUENCE if mode == "all" else (mode,)


def _dispatch(harness: Harness, token: ResolvedToken, mode: str) -> None:
    if mode == "matrix":
        _matrix(harness, token)
        return
    runner = MODE_RUNNERS.get(mode)
    if runner is None:  # pragma: no cover - RunConfig rejects unknown modes
        raise ValueError(f"unknown mode {mode}")
    runner(harness, token)


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

    window = LedgerWindow(harness, scenario=config.mode)
    try:
        token = resolve_token(harness)
        if token is not None:
            window.open()
            for mode in _mode_sequence(config.mode):
                broken = harness.paper_state_broken
                if broken is not None:
                    # A later scenario cannot read paper state, so it is skipped with the
                    # reason instead of adding cascade findings.
                    harness.note(f"{mode} skipped: {broken}")
                    continue
                _dispatch(harness, token, mode)
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
        try:
            window.close()
        except Exception as exc:  # noqa: BLE001 - the ledger verdict must not mask errors
            harness.add_finding(
                Finding(
                    code="LEDGER_WINDOW_FAILED",
                    scenario=config.mode,
                    invariant="ledger.positions_match_fills",
                    summary="the independent ledger recompute could not complete",
                    expected="the before/after observations are comparable",
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
