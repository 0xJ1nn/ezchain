"""Operational metrics: real freshness, breaker, queue and drift state on /metrics.

The alert rules in ``infra/prometheus/alerts.yml`` must be backed by numbers the process
can prove. Each family is exercised through failure and recovery: a breaker that opens
moves the gauge, a source that goes stale raises its age past the budget, a queue that
backs up reports its depth, and a broken ledger reports its failing checks. The file also
holds the alerts file to the implementation: every family an expression names must appear
in a rendered body.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.orm import Session

from ezchain_adapters import ProviderBudgetManager, ProviderLimit
from ezchain_api.models import (
    LedgerEntryRow,
    MarketCandleRow,
    MarketOutboxRow,
    MarketSnapshotRow,
    PaperOrderRow,
)
from ezchain_api.observability import (
    BREAKER_STATE_VALUES,
    HttpMetrics,
    OpsSnapshot,
    SourceFreshnessSample,
    render_ops,
    render_prometheus,
)
from ezchain_api.routers import system
from ezchain_api.services.market_monitor import MarketMonitor
from ezchain_api.services.ops_signals import (
    MARKET_OUTBOX_QUEUE,
    build_ops_snapshot,
)

ALERTS = Path(__file__).resolve().parents[2] / "infra" / "prometheus" / "alerts.yml"
MARK_BUDGET_SECONDS = 60.0


def _value(body: str, name: str, **labels: str) -> float:
    """Parse the one sample named by ``name`` and its labels."""
    wanted = name
    if labels:
        rendered = ",".join(f'{key}="{value}"' for key, value in sorted(labels.items()))
        wanted = f"{name}{{{rendered}}}"
    for line in body.splitlines():
        if line.startswith(wanted + " ") or line == wanted:
            return float(line[len(wanted) :].strip())
    raise AssertionError(f"{wanted} not emitted:\n{body}")


def _client(session: Session, app_state: dict[str, Any] | None = None) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ezchain_api.deps import get_session

    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[get_session] = lambda: session
    for key, value in (app_state or {}).items():
        setattr(app.state, key, value)
    return TestClient(app)


def _snapshot_row(session: Session, *, source: str, observed_at: datetime) -> None:
    session.add(
        MarketSnapshotRow(
            chain="ethereum",
            token_address="0x0000000000000000000000000000000000000abc",
            symbol="WETH",
            name="Wrapped Ether",
            price_usd=Decimal("2000"),
            source=source,
            observed_at=observed_at,
        )
    )
    session.commit()


def _candle_row(session: Session, *, source: str, fetched_at: datetime) -> None:
    open_time = fetched_at - timedelta(minutes=1)
    session.add(
        MarketCandleRow(
            chain="ethereum",
            token_address="0x0000000000000000000000000000000000000abc",
            pool_address="0xpool",
            timeframe="1m",
            open_time=open_time,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            source=source,
            fetched_at=fetched_at,
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Source freshness
# ---------------------------------------------------------------------------


def test_a_fresh_source_reports_its_real_age(session: Session) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now - timedelta(seconds=12))

    snapshot = build_ops_snapshot(session, now=lambda: now)
    assert snapshot is not None
    body = "\n".join(render_ops(snapshot))

    age = _value(
        body, "ezchain_source_freshness_seconds", source="dexscreener", source_class="mark"
    )
    assert 11.0 <= age <= 13.0
    assert age <= MARK_BUDGET_SECONDS


def test_a_stale_source_reports_an_age_past_its_budget(session: Session) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now - timedelta(seconds=600))

    body = "\n".join(render_ops(build_ops_snapshot(session, now=lambda: now)))  # type: ignore[arg-type]

    age = _value(
        body, "ezchain_source_freshness_seconds", source="dexscreener", source_class="mark"
    )
    budget = _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark")
    assert age == pytest.approx(600.0, abs=1.0)
    assert age > budget, "the alert's age > budget comparison must fire"


def test_recovery_lowers_the_age_and_failure_raises_it_again(session: Session) -> None:
    """The same source, through a stale window and back: the gauge follows the rows."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now - timedelta(seconds=900))
    stale = build_ops_snapshot(session, now=lambda: now)
    stale_age = _value(
        "\n".join(render_ops(stale)),
        "ezchain_source_freshness_seconds",
        source="dexscreener",
        source_class="mark",
    )  # type: ignore[arg-type]
    assert stale_age > MARK_BUDGET_SECONDS

    session.query(MarketSnapshotRow).delete()
    _snapshot_row(session, source="dexscreener", observed_at=now - timedelta(seconds=1))
    fresh = build_ops_snapshot(session, now=lambda: now)
    fresh_age = _value(
        "\n".join(render_ops(fresh)),
        "ezchain_source_freshness_seconds",
        source="dexscreener",
        source_class="mark",
    )  # type: ignore[arg-type]
    assert fresh_age <= 2.0
    assert fresh_age < stale_age


def test_a_future_dated_observation_reports_an_unknown_age_not_zero(session: Session) -> None:
    """A clock-faulted row must not read as maximum freshness."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now + timedelta(minutes=5))

    body = "\n".join(render_ops(build_ops_snapshot(session, now=lambda: now)))  # type: ignore[arg-type]

    assert 'source="dexscreener",source_class="mark"} NaN' in body


def test_candles_are_a_separate_class_with_its_own_budget(session: Session) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _candle_row(session, source="geckoterminal", fetched_at=now - timedelta(seconds=120))

    body = "\n".join(render_ops(build_ops_snapshot(session, now=lambda: now)))  # type: ignore[arg-type]

    age = _value(
        body, "ezchain_source_freshness_seconds", source="geckoterminal", source_class="candle"
    )
    budget = _value(body, "ezchain_source_freshness_budget_seconds", source_class="candle")
    assert age == pytest.approx(120.0, abs=1.0)
    assert age < budget, "a 120 s candle is inside its 300 s budget"


# ---------------------------------------------------------------------------
# Breaker state
# ---------------------------------------------------------------------------


class _Breaker:
    def __init__(self, state: str) -> None:
        self.state = state


class _Provider:
    def __init__(self, state: str) -> None:
        self.breaker_state = state


class _Router:
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def breakers(self) -> dict[str, str]:
        return dict(self._states)


def test_breaker_gauge_reports_closed_then_open_then_half_open() -> None:
    states = {"jupiter": "closed", "kyberswap": "closed"}
    snapshot = OpsSnapshot(breakers=states)
    assert (
        _value(
            "\n".join(render_ops(snapshot)),
            "ezchain_breaker_state",
            provider="jupiter",
            state="closed",
        )
        == 0.0
    )

    states["jupiter"] = "open"
    body = "\n".join(render_ops(OpsSnapshot(breakers=states)))
    assert _value(body, "ezchain_breaker_state", provider="jupiter", state="open") == 2.0

    states["jupiter"] = "half_open"
    body = "\n".join(render_ops(OpsSnapshot(breakers=states)))
    assert _value(body, "ezchain_breaker_state", provider="jupiter", state="half_open") == 1.0


def test_breaker_gauge_follows_a_real_circuit_through_failure_and_recovery(
    session: Session,
) -> None:
    """The state comes from the live CircuitBreaker, not a copy."""
    from ezchain_adapters import CircuitBreaker

    clock = {"now": 1000.0}
    breaker = CircuitBreaker(cooldown_seconds=30.0, failure_threshold=1, clock=lambda: clock["now"])
    # The sampler reads the router's live view, which is the breaker's own state.
    router_breakers = type("Live", (), {"breakers": lambda self: {"jupiter": breaker.state}})()

    closed = build_ops_snapshot(session, quote_router=router_breakers)
    assert closed is not None and closed.breakers["jupiter"] == "closed"

    breaker.record_failure()
    opened = build_ops_snapshot(session, quote_router=router_breakers)
    assert opened is not None
    body = "\n".join(render_ops(opened))
    assert _value(body, "ezchain_breaker_state", provider="jupiter", state="open") == 2.0

    clock["now"] += 31.0
    half = build_ops_snapshot(session, quote_router=router_breakers)
    assert half is not None and half.breakers["jupiter"] == "half_open"

    breaker.record_success()
    recovered = build_ops_snapshot(session, quote_router=router_breakers)
    assert recovered is not None
    assert (
        _value(
            "\n".join(render_ops(recovered)),
            "ezchain_breaker_state",
            provider="jupiter",
            state="closed",
        )
        == 0.0
    )


def test_the_market_monitor_breaker_is_sampled_under_its_provider_name(
    session: Session,
) -> None:
    monitor = MarketMonitor.__new__(MarketMonitor)
    monitor._provider_name = "dexscreener"
    monitor._breaker = _Breaker("open")

    snapshot = build_ops_snapshot(session, market_monitor=monitor)

    assert snapshot is not None
    assert snapshot.breakers["dexscreener"] == "open"


def test_the_state_vocabulary_matches_the_state_machine() -> None:
    from ezchain_adapters.breaker import BreakerState

    assert set(BREAKER_STATE_VALUES) == {state.value for state in BreakerState}


def test_an_unknown_breaker_state_is_omitted_not_guessed() -> None:
    body = "\n".join(render_ops(OpsSnapshot(breakers={"jupiter": "confused"})))

    assert "ezchain_breaker_state{" not in body
    assert "# TYPE ezchain_breaker_state gauge" in body


# ---------------------------------------------------------------------------
# Queue depth
# ---------------------------------------------------------------------------


def test_pending_outbox_rows_are_the_market_queue_depth(session: Session) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for index in range(3):
        session.add(
            MarketOutboxRow(
                id=f"00000000-0000-0000-0000-00000000000{index}",
                topic="market.event",
                payload={},
                created_at=now,
            )
        )
    session.add(
        MarketOutboxRow(
            id="00000000-0000-0000-0000-000000000099",
            topic="market.event",
            payload={},
            created_at=now,
            published_at=now,
        )
    )
    session.commit()

    body = "\n".join(render_ops(build_ops_snapshot(session)))  # type: ignore[arg-type]

    assert _value(body, "ezchain_queue_depth", queue=MARKET_OUTBOX_QUEUE) == 3.0


def test_a_published_row_leaves_the_queue(session: Session) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    row = MarketOutboxRow(
        id="00000000-0000-0000-0000-000000000001",
        topic="market.event",
        payload={},
        created_at=now,
    )
    session.add(row)
    session.commit()
    assert (
        _value(
            "\n".join(render_ops(build_ops_snapshot(session))),  # type: ignore[arg-type]
            "ezchain_queue_depth",
            queue=MARKET_OUTBOX_QUEUE,
        )
        == 1.0
    )

    row.published_at = now
    session.commit()

    assert (
        _value(
            "\n".join(render_ops(build_ops_snapshot(session))),  # type: ignore[arg-type]
            "ezchain_queue_depth",
            queue=MARKET_OUTBOX_QUEUE,
        )
        == 0.0
    )


async def test_provider_budget_queue_depth_is_the_waiting_callers(session: Session) -> None:
    """A caller parked on the budget shows up as depth until it is served."""
    import asyncio

    gate = asyncio.Event()

    class Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def __call__(self) -> float:
            return self.now

        async def sleep(self, seconds: float) -> None:
            await gate.wait()
            self.now += seconds

    clock = Clock()
    manager = ProviderBudgetManager(
        limits=[
            ProviderLimit(
                "jupiter", requests_per_second=1.0, burst=1, max_queue=4, max_wait_seconds=30.0
            )
        ],
        clock=clock,
        sleep=clock.sleep,
    )
    await manager.acquire("jupiter")  # take the only token

    waiter = asyncio.create_task(manager.acquire("jupiter"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    body = "\n".join(render_ops(build_ops_snapshot(session, provider_budget=manager)))  # type: ignore[arg-type]
    assert _value(body, "ezchain_queue_depth", queue="provider:jupiter") >= 1.0

    gate.set()
    await waiter
    body = "\n".join(render_ops(build_ops_snapshot(session, provider_budget=manager)))  # type: ignore[arg-type]
    assert _value(body, "ezchain_queue_depth", queue="provider:jupiter") == 0.0


def test_a_provider_with_no_queue_is_absent_not_zero() -> None:
    body = "\n".join(render_ops(OpsSnapshot()))

    assert 'ezchain_queue_depth{queue="provider:' not in body


# ---------------------------------------------------------------------------
# Ledger drift
# ---------------------------------------------------------------------------


def test_a_clean_ledger_reports_zero_drift(session: Session) -> None:
    from ezchain_api.services.paper import ensure_genesis

    ensure_genesis(session, Decimal("10000"))

    body = "\n".join(render_ops(build_ops_snapshot(session)))  # type: ignore[arg-type]

    assert _value(body, "ezchain_ledger_drift_entries") == 0.0


def test_an_unbalanced_journal_raises_the_drift_gauge(session: Session) -> None:
    from ezchain_api.services.paper import ensure_genesis

    ensure_genesis(session, Decimal("10000"))
    # A fill row with no journal behind it: reconciliation's group-link check fails.
    session.add(
        PaperOrderRow(
            id="order-without-journal",
            idempotency_key="drift-order",
            chain="ethereum",
            token_address="0xtoken",
            side="buy",
            amount_usd=Decimal("10"),
            status="filled",
        )
    )
    session.commit()

    drift = build_ops_snapshot(session)
    assert drift is not None
    body = "\n".join(render_ops(drift))
    assert _value(body, "ezchain_ledger_drift_entries") >= 1.0


def test_drift_reports_how_many_checks_fail(session: Session) -> None:
    """The gauge is the count of failing checks, not a boolean."""
    from ezchain_api.services.paper import ensure_genesis
    from ezchain_core.ledger import cash_account

    ensure_genesis(session, Decimal("10000"))
    session.add(
        LedgerEntryRow(
            id="unbalanced-1",
            seq=9001,
            group_id="unbalanced-group",
            account=cash_account(),
            asset="USD",
            amount=Decimal("5"),
            usd_value=Decimal("5"),
            kind="buy",
        )
    )
    session.commit()

    drift = build_ops_snapshot(session)

    assert drift is not None
    assert drift.ledger_drift_entries >= 1


# ---------------------------------------------------------------------------
# Readiness and honesty
# ---------------------------------------------------------------------------


def test_the_emitted_budget_is_the_configured_one(session: Session) -> None:
    """The alert threshold is the gate's own budget, not the library default."""
    from ezchain_core import FreshnessPolicy

    configured = FreshnessPolicy(max_age_by_class={"mark": 7.0, "candle": 300.0})

    body = "\n".join(
        render_ops(build_ops_snapshot(session, policy=configured))  # type: ignore[arg-type]
    )

    assert _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark") == 7.0


def test_an_unreadable_database_omits_the_ops_families(session: Session) -> None:
    """No database, no ops series: absence beats a confident zero."""

    class BrokenSession:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("database is gone")

        def rollback(self) -> None:
            pass

    snapshot = build_ops_snapshot(BrokenSession())  # type: ignore[arg-type]

    assert snapshot is None
    body = render_prometheus(HttpMetrics(), up=True, paper_orders={}, paper_fills=0, ops=snapshot)
    assert "ezchain_ledger_drift_entries" not in body
    assert "ezchain_breaker_state" not in body


def test_metrics_endpoint_renders_the_ops_families(session: Session) -> None:
    now = datetime.now(UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now)
    session.add(
        MarketOutboxRow(
            id="00000000-0000-0000-0000-000000000007",
            topic="market.event",
            payload={},
            created_at=now,
        )
    )
    session.commit()

    response = _client(session, {"quote_router": _Router({"jupiter": "open"})}).get("/metrics")

    assert response.status_code == 200
    body = response.text
    assert "ezchain_source_freshness_seconds{" in body
    assert "ezchain_source_freshness_budget_seconds{" in body
    assert 'ezchain_breaker_state{provider="jupiter",state="open"} 2' in body
    assert f'ezchain_queue_depth{{queue="{MARKET_OUTBOX_QUEUE}"}} 1' in body
    assert "ezchain_ledger_drift_entries" in body


def test_the_metric_families_are_gauge_typed(session: Session) -> None:
    body = "\n".join(render_ops(build_ops_snapshot(session)))  # type: ignore[arg-type]

    for family in (
        "ezchain_source_freshness_seconds",
        "ezchain_source_freshness_budget_seconds",
        "ezchain_breaker_state",
        "ezchain_queue_depth",
        "ezchain_ledger_drift_entries",
    ):
        assert f"# TYPE {family} gauge" in body


# ---------------------------------------------------------------------------
# The alert rules and the implementation
# ---------------------------------------------------------------------------


def _alert_expressions() -> list[tuple[str, str]]:
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return [
        (rule["alert"], rule["expr"]) for group in document["groups"] for rule in group["rules"]
    ]


def _ops_body() -> str:
    snapshot = OpsSnapshot(
        source_freshness=(SourceFreshnessSample("dexscreener", "mark", 900.0),),
        freshness_budgets={"mark": 60.0, "candle": 300.0},
        breakers={"jupiter": "open"},
        queues={"market_outbox": 0, "provider:jupiter": 0},
        ledger_drift_entries=0,
    )
    return render_prometheus(
        HttpMetrics(), up=True, paper_orders={"filled": 0}, paper_fills=0, ops=snapshot
    )


def test_every_metric_an_alert_names_is_emitted() -> None:
    """No rule may reference a family this process does not expose."""
    body = _ops_body()
    builtin = {"up"}
    referenced: set[str] = set()
    for _name, expr in _alert_expressions():
        referenced.update(re.findall(r"\b(ezchain_[a-z_]+|up)\b", expr))

    missing = sorted(
        name for name in referenced - builtin if f"{name}{{" not in body and f"{name} " not in body
    )
    assert not missing, f"alerts reference unemitted metrics: {missing}"


def test_alert_labels_match_the_emitted_labels() -> None:
    """A selector on a label the series never carries would silently never fire."""
    body = _ops_body()
    expressions = dict(_alert_expressions())

    assert 'state="open"' in expressions["CircuitBreakerOpen"]
    assert 'state="open"' in body and 'provider="jupiter"' in body
    assert 'state="half_open"' in expressions["CircuitBreakerHalfOpenTooLong"]
    # The freshness rule joins the two families on source_class; both must carry it.
    assert "on(source_class)" in expressions["SourceStale"]
    assert 'source_class="mark"' in body
    assert 'ezchain_source_freshness_budget_seconds{source_class="mark"}' in body


def test_the_stale_expression_fires_for_a_stale_source_and_not_a_fresh_one() -> None:
    """Evaluate the rule's comparison on the emitted samples, without a Prometheus."""
    budget = 60.0
    stale = OpsSnapshot(
        source_freshness=(SourceFreshnessSample("dexscreener", "mark", 900.0),),
        freshness_budgets={"mark": budget},
    )
    fresh = OpsSnapshot(
        source_freshness=(SourceFreshnessSample("dexscreener", "mark", 5.0),),
        freshness_budgets={"mark": budget},
    )

    def exceeds_budget(snapshot: OpsSnapshot) -> bool:
        # The rule is `freshness > on(source_class) budget`, per sample.
        body = "\n".join(render_ops(snapshot))
        age = _value(
            body, "ezchain_source_freshness_seconds", source="dexscreener", source_class="mark"
        )
        cap = _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark")
        return age > cap

    assert exceeds_budget(stale)
    assert not exceeds_budget(fresh)


def test_no_alert_is_documented_for_an_absent_deployable() -> None:
    """Executor and backup alerts stay out until those deployables expose a metric."""
    expressions = dict(_alert_expressions())

    assert "ExecutorUnreachable" not in expressions
    assert not any("backup" in name.lower() for name in expressions)
