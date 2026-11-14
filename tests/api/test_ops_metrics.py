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

from ezchain_adapters import (
    JUPITER_LIMIT,
    HttpQuoteProvider,
    JupiterClient,
    KyberSwapClient,
    ProviderBudgetManager,
    ProviderLimit,
    QuoteRouter,
)
from ezchain_api.config import Settings
from ezchain_api.models import (
    LedgerEntryRow,
    MarketCandleRow,
    MarketOutboxRow,
    MarketSnapshotRow,
    PaperOrderRow,
)
from ezchain_api.observability import (
    BREAKER_STATE_VALUES,
    MAX_REJECT_LABELS,
    REJECT_LABEL_VALUES,
    VENUE_LATENCY,
    VENUE_LATENCY_BUCKETS_SECONDS,
    HttpMetrics,
    OpsSnapshot,
    RejectMetrics,
    VenueLatencyMetrics,
    VenueLatencyProvider,
    reject_label,
    render_ops,
    render_prometheus,
)
from ezchain_api.routers import system
from ezchain_api.services.market_monitor import MarketMonitor
from ezchain_api.services.ops_signals import (
    MARKET_OUTBOX_QUEUE,
    build_ops_snapshot,
)
from ezchain_core import FreshnessPolicy

ALERTS = Path(__file__).resolve().parents[2] / "infra" / "prometheus" / "alerts.yml"
MARK_BUDGET_SECONDS = 60.0
POLICY = FreshnessPolicy()


class _NoDex:
    """The alert fixtures never quote; a real router still needs a market client."""

    async def get_token(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the alert fixture never quotes")


def _snapshot(session: Any, **overrides: Any) -> OpsSnapshot | None:
    """Sample with the policy the order gate classifies with, unless a test overrides it."""
    return build_ops_snapshot(session, policy=POLICY, **overrides)


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
    from ezchain_api.observability import ObservabilityMiddleware

    app = FastAPI()
    # The real app counts every request, so the scrape itself emits the request counter
    # the alerts read instead of the test inventing a sample.
    app.add_middleware(ObservabilityMiddleware)
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


def test_without_a_router_the_budget_still_comes_from_the_gates_settings(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No quote router is not "use a library default": derive it as the gate's router does."""
    from ezchain_api.services import ops_signals
    from ezchain_api.services.paper import freshness_from_settings

    settings = Settings(
        environment="test",
        database_url="sqlite+pysqlite://",
        max_mark_age_seconds=11,
        max_candle_age_seconds=222,
    )
    monkeypatch.setattr(ops_signals, "get_settings", lambda: settings)

    body = "\n".join(render_ops(build_ops_snapshot(session)))  # type: ignore[arg-type]

    expected = freshness_from_settings(settings).max_age_by_class
    assert expected["mark"] == 11.0
    assert _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark") == 11.0
    assert _value(body, "ezchain_source_freshness_budget_seconds", source_class="candle") == 222.0


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
        "ezchain_queue_capacity",
        "ezchain_ledger_drift_entries",
    ):
        assert f"# TYPE {family} gauge" in body


# ---------------------------------------------------------------------------
# The alert rules and the implementation
# ---------------------------------------------------------------------------


def _alert_rules() -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    return {rule["alert"]: rule for group in document["groups"] for rule in group["rules"]}


def _alert_expressions() -> list[tuple[str, str]]:
    return [(name, str(rule["expr"])) for name, rule in _alert_rules().items()]


def _real_router(clock: dict[str, float]) -> tuple[QuoteRouter, JupiterClient]:
    """A router with its real providers, so the breaker states are the ones it reports."""
    jupiter = JupiterClient(clock=lambda: clock["now"])
    router = QuoteRouter(
        providers=[jupiter, KyberSwapClient()],
        dexscreener=_NoDex(),  # type: ignore[arg-type]
        freshness=POLICY,
    )
    return router, jupiter


def _live_body(session: Session) -> str:
    """The real /metrics body, from live state, with every family its alerts read."""
    now = datetime.now(UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now - timedelta(seconds=900))
    _candle_row(session, source="geckoterminal", fetched_at=now - timedelta(seconds=120))
    session.add(
        MarketOutboxRow(
            id="00000000-0000-0000-0000-0000000000aa",
            topic="market.event",
            payload={},
            created_at=now,
        )
    )
    session.commit()

    clock = {"now": 1000.0}
    router, jupiter = _real_router(clock)
    jupiter.record_timeout()
    # The same manager the router's provider is budgeted against, so the emitted depth
    # and its capacity come from the live limit.
    budget = ProviderBudgetManager()
    client = _client(session, {"quote_router": router, "provider_budget": budget})
    # The middleware counts a request after its body is rendered, so the second scrape
    # is the first one that also carries the request counter its alerts read.
    assert client.get("/metrics").status_code == 200
    response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def _emitted_labels(body: str) -> dict[str, set[str]]:
    """Every label key each emitted family actually carries, from a rendered body."""
    labels: dict[str, set[str]] = {"up": {"job", "instance"}}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name = re.split(r"[{ ]", line, maxsplit=1)[0]
        labels.setdefault(name, set()).update(re.findall(r'([a-z_]+)="', line))
    return labels


def _selectors(expr: str) -> list[tuple[str, dict[str, str]]]:
    """``metric{labels}`` selectors in one expression, with their matchers."""
    return [
        (metric, dict(re.findall(r'([a-z_]+)\s*(?:=~|!~|!=|=)\s*"([^"]*)"', braces)))
        for metric, braces in re.findall(r"\b(ezchain_[a-z_]+|up)\s*\{([^}]*)\}", expr)
    ]


def test_every_metric_an_alert_names_is_emitted(session: Session) -> None:
    """No rule may reference a family this process does not expose."""
    body = _live_body(session)
    emitted = _emitted_labels(body)
    referenced: set[str] = set()
    for _name, expr in _alert_expressions():
        referenced.update(re.findall(r"\b(ezchain_[a-z_]+|up)\b", expr))

    missing = sorted(referenced - set(emitted))
    assert not missing, f"alerts reference unemitted metrics: {missing}"


def test_every_alert_selector_matches_the_emitted_labels(session: Session) -> None:
    """A selector on a label the series never carries would silently never fire."""
    emitted = _emitted_labels(_live_body(session))
    mismatched: list[str] = []
    for name, expr in _alert_expressions():
        for metric, matchers in _selectors(expr):
            if metric not in emitted:
                continue
            unknown = set(matchers) - emitted[metric]
            if unknown:
                mismatched.append(f"{name}: {metric} has no {sorted(unknown)}")
    assert not mismatched, mismatched

    # The freshness rule joins the two families on ``source_class``; both carry it.
    assert "on(source_class)" in dict(_alert_expressions())["SourceStale"]
    assert "source_class" in emitted["ezchain_source_freshness_seconds"]
    assert "source_class" in emitted["ezchain_source_freshness_budget_seconds"]


def test_the_queue_threshold_is_the_queue_own_bound(session: Session) -> None:
    """The provider budget refuses a waiter at max_queue, so the rule must compare with it."""
    expr = str(_alert_rules()["ProviderQueueSaturated"]["expr"])

    assert ">=" in expr, "a depth capped at max_queue can never exceed it"
    assert "ezchain_queue_capacity" in expr, "the bound must be the emitted one, not a copy"
    assert "on(queue)" in expr
    assert ">= 4" not in expr and "> 4" not in expr

    outbox = str(_alert_rules()["QueueDepthHigh"]["expr"])
    assert 'queue="market_outbox"' in outbox


def test_the_emitted_capacity_is_the_limit_the_manager_enforces(session: Session) -> None:
    """The capacity series is read from the same ProviderLimit that refuses the caller."""
    manager = ProviderBudgetManager(
        limits=[
            ProviderLimit(
                "jupiter", requests_per_second=1.0, burst=1, max_queue=7, max_wait_seconds=1.0
            )
        ]
    )

    body = "\n".join(render_ops(_snapshot(session, provider_budget=manager)))

    assert _value(body, "ezchain_queue_capacity", queue="provider:jupiter") == 7.0
    assert 7.0 == manager.limit("jupiter").max_queue
    # The outbox is unbounded, so it has a depth but no fabricated capacity.
    assert 'ezchain_queue_capacity{queue="market_outbox"}' not in body
    assert JUPITER_LIMIT.max_queue == ProviderBudgetManager().limit("jupiter").max_queue


def test_the_breaker_alert_can_fire_inside_the_cooldown() -> None:
    """``for`` plus one evaluation interval must fit inside the cooldown.

    Prometheus marks an alert firing only after it has been pending for ``for``. If the
    hold plus an evaluation reaches the cooldown, the open sample has already become
    half_open by the second look and the alert never fires.
    """
    rule = _alert_rules()["CircuitBreakerOpen"]
    match = re.fullmatch(r"(\d+)([sm])", str(rule["for"]).strip())
    assert match is not None
    hold = int(match.group(1)) * (60 if match.group(2) == "m" else 1)

    prometheus = yaml.safe_load((ALERTS.parent / "prometheus.yml").read_text(encoding="utf-8"))
    interval = int(str(prometheus["global"]["evaluation_interval"]).rstrip("s"))

    assert hold + interval <= HttpQuoteProvider.breaker_cooldown_seconds
    assert hold < 60, "the default cooldown is 60 s; a 1m hold never sees the state"


def test_a_future_dated_source_is_visible_to_its_own_unknown_age_alert(
    session: Session,
) -> None:
    """``NaN`` escapes both ``>`` and ``absent()``; ``x != x`` is what selects it."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    _snapshot_row(session, source="dexscreener", observed_at=now + timedelta(minutes=5))
    body = "\n".join(render_ops(_snapshot(session, now=lambda: now)))
    age = _value(
        body, "ezchain_source_freshness_seconds", source="dexscreener", source_class="mark"
    )
    budget = _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark")

    assert age != age, "the emitted age must be NaN, not a confident zero"
    assert not age > budget, "SourceStale cannot see a NaN age"
    assert age != age, "the x != x rule keeps exactly this sample"

    expr = str(_alert_rules()["SourceFreshnessUnknown"]["expr"])
    assert expr.strip() == ("ezchain_source_freshness_seconds != ezchain_source_freshness_seconds")
    assert 'source="dexscreener"' in body, "the family is still emitted, so absent() is zero"


def test_each_absent_guard_defers_to_the_process_that_owns_the_outage(
    session: Session,
) -> None:
    """An absent guard pages only when the process is otherwise healthy.

    The four ops families are omitted when the database cannot be read, so their guard
    keys off ``ezchain_up`` and lets ``ApiUnhealthy`` own that outage. The HTTP counter is
    database-independent, so it keys off the scrape target instead.
    """
    body = _live_body(session)
    assert "ezchain_up 1" in body

    guards = {
        name: expr for name, expr in _alert_expressions() if expr.strip().startswith("absent(")
    }
    assert guards, "every family keeps its absence guard"
    assert 'up{job="api"} == 1' in guards["ApiMetricsMissing"]
    for name, expr in guards.items():
        if name == "ApiMetricsMissing":
            continue
        assert "ezchain_up == 1" in expr, f"{name} fires while the API reports itself down"


def test_the_real_router_reports_its_providers_live_breaker_state() -> None:
    """Drive ``QuoteRouter.breakers()`` and ``HttpQuoteProvider.breaker_state`` directly."""
    clock = {"now": 1000.0}
    router, jupiter = _real_router(clock)

    assert router.breakers() == {"jupiter": "closed", "kyberswap": "closed"}
    assert jupiter.breaker_state == "closed"

    jupiter.record_timeout()
    assert jupiter.breaker_state == "open"
    assert router.breakers()["jupiter"] == "open"

    clock["now"] += HttpQuoteProvider.breaker_cooldown_seconds + 1.0
    assert router.breakers()["jupiter"] == "half_open"

    jupiter._breaker.record_success()  # noqa: SLF001 - the live breaker behind the property
    assert router.breakers()["jupiter"] == "closed"


def test_the_alert_parser_dependency_is_declared() -> None:
    """``import yaml`` in this file must be a declared, installable dependency."""
    requirements = (ALERTS.parents[2] / "requirements.txt").read_text(encoding="utf-8")

    assert re.search(r"(?im)^pyyaml\b", requirements)


def test_the_stale_expression_fires_for_a_stale_source_and_not_a_fresh_one(
    session: Session,
) -> None:
    """Evaluate the rule's comparison on samples sampled from live rows, no Prometheus."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def exceeds_budget(age_offset: timedelta) -> bool:
        session.query(MarketSnapshotRow).delete()
        _snapshot_row(session, source="dexscreener", observed_at=now - age_offset)
        body = "\n".join(render_ops(_snapshot(session, now=lambda: now)))
        # The rule is `freshness > on(source_class) budget`, per sample.
        age = _value(
            body, "ezchain_source_freshness_seconds", source="dexscreener", source_class="mark"
        )
        cap = _value(body, "ezchain_source_freshness_budget_seconds", source_class="mark")
        return age > cap

    assert exceeds_budget(timedelta(seconds=900))
    assert not exceeds_budget(timedelta(seconds=5))


def test_no_alert_is_documented_for_an_absent_deployable() -> None:
    """Executor and backup alerts stay out until those deployables expose a metric."""
    expressions = dict(_alert_expressions())

    assert "ExecutorUnreachable" not in expressions
    assert not any("backup" in name.lower() for name in expressions)


# ---------------------------------------------------------------------------
# Why was this order rejected
# ---------------------------------------------------------------------------


def _rejected(session: Session, key: str, reason: str | None) -> None:
    session.add(
        PaperOrderRow(
            idempotency_key=key,
            chain="ethereum",
            token_address="0xtoken",
            side="buy",
            amount_usd=Decimal("10"),
            status="rejected",
            reason=reason,
        )
    )
    session.commit()


def test_persisted_rejections_are_counted_by_their_reason_code(session: Session) -> None:
    """The counter is the reason the order row persisted, not a second opinion."""
    _rejected(session, "r-1", "SNAPSHOT_STALE")
    _rejected(session, "r-2", "SNAPSHOT_STALE")
    _rejected(session, "r-3", "INSUFFICIENT_CASH")

    body = _client(session).get("/metrics").text

    assert 'ezchain_paper_rejects_total{reason="SNAPSHOT_STALE"} 2' in body
    assert 'ezchain_paper_rejects_total{reason="INSUFFICIENT_CASH"} 1' in body


def test_a_rejection_with_no_code_is_unknown_not_zero(session: Session) -> None:
    """A row with no reason is a refusal whose cause is unrecorded, not an absence."""
    _rejected(session, "r-null", None)

    body = _client(session).get("/metrics").text

    assert 'ezchain_paper_rejects_total{reason="UNKNOWN"} 1' in body


def test_the_reject_label_set_is_bounded_and_contains_no_caller_string(session: Session) -> None:
    """A caller cannot buy a series: outside the taxonomy is one ``OTHER`` bucket.

    The candidates are the shapes that would blow up a label set if they were trusted: a
    code nobody defined, a 90-character token-like string, a base58 address and a hex
    pair address. None may reach the exposition, and the family's label count must stay
    inside the taxonomy's own size.
    """
    absurd = "TOTALLY_MADE_UP_REASON"
    token_like = "So11111111111111111111111111111111111111112" * 2
    address = "0x9f8a2b7c4d1e6f3a5b0c8d7e2f4a6b1c3d5e7f90"
    quoted = 'stale "batch"\nSNAPSHOT_STALE'
    for index, candidate in enumerate((absurd, token_like, address, quoted)):
        _rejected(session, f"r-hostile-{index}", candidate)
    _rejected(session, "r-real", "ORDER_LIMIT")

    body = _client(session).get("/metrics").text

    for candidate in (absurd, token_like, address, "batch"):
        assert candidate not in body
    rejects = [
        line for line in body.splitlines() if line.startswith("ezchain_paper_rejects_total{")
    ]
    assert rejects == [
        'ezchain_paper_rejects_total{reason="ORDER_LIMIT"} 1',
        'ezchain_paper_rejects_total{reason="OTHER"} 4',
    ]
    assert len(rejects) <= MAX_REJECT_LABELS


def test_every_reject_label_is_member_of_the_declared_vocabulary() -> None:
    """The bound is a property of the label set, not a cap that silently drops samples."""
    assert reject_label("SNAPSHOT_STALE") == "SNAPSHOT_STALE"
    assert reject_label(None) == "UNKNOWN"
    assert reject_label("") == "UNKNOWN"
    assert reject_label("TOKEN_MINT") == "OTHER"
    assert reject_label(42) == "OTHER"  # type: ignore[arg-type]

    metrics = RejectMetrics()
    metrics.record("SNAPSHOT_STALE")
    metrics.record("x" * 500)
    metrics.record(None)
    assert set(metrics.snapshot()) <= REJECT_LABEL_VALUES
    assert MAX_REJECT_LABELS == len(REJECT_LABEL_VALUES)


def test_an_unreadable_database_omits_the_reject_family(session: Session) -> None:
    """No database is not "no rejects": the family is absent and ``ezchain_up 0`` says why."""

    class BrokenSession:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("database is gone")

        def rollback(self) -> None:
            pass

    body = _client(BrokenSession()).get("/metrics").text  # type: ignore[arg-type]

    assert "ezchain_up 0" in body
    assert "ezchain_paper_rejects_total" not in body


# ---------------------------------------------------------------------------
# Venue latency
# ---------------------------------------------------------------------------


def test_venue_latency_is_a_histogram_a_percentile_can_be_read_from() -> None:
    """Fixed buckets, cumulative counts and a matching ``_sum``/``_count`` pair."""
    latency = VenueLatencyMetrics()
    for seconds in (0.01, 0.2, 0.3, 4.0):
        latency.observe("jupiter", seconds)

    body = "\n".join(latency.render())

    assert "# TYPE ezchain_venue_latency_seconds histogram" in body
    assert 'ezchain_venue_latency_seconds_bucket{venue="jupiter",le="0.05"} 1' in body
    assert 'ezchain_venue_latency_seconds_bucket{venue="jupiter",le="0.25"} 2' in body
    assert 'ezchain_venue_latency_seconds_bucket{venue="jupiter",le="0.5"} 3' in body
    assert 'ezchain_venue_latency_seconds_bucket{venue="jupiter",le="5"} 4' in body
    assert 'ezchain_venue_latency_seconds_bucket{venue="jupiter",le="+Inf"} 4' in body
    assert 'ezchain_venue_latency_seconds_count{venue="jupiter"} 4' in body
    assert 'ezchain_venue_latency_seconds_sum{venue="jupiter"} 4.510000' in body


def test_the_latency_buckets_cover_the_routers_own_deadline() -> None:
    """The last finite bucket is past the quote deadline, so a timeout is not ``+Inf``."""
    from ezchain_adapters import quotes

    assert max(VENUE_LATENCY_BUCKETS_SECONDS) > quotes.QUOTE_DEADLINE_SECONDS
    assert min(VENUE_LATENCY_BUCKETS_SECONDS) <= 0.05


def test_an_unknown_venue_and_an_unmeasurable_call_do_not_create_series() -> None:
    latency = VenueLatencyMetrics()
    latency.observe("some-new-venue", 0.2)
    latency.observe("jupiter", float("nan"))

    body = "\n".join(latency.render())

    assert 'ezchain_venue_latency_seconds_count{venue="other"} 1' in body
    assert "some-new-venue" not in body
    assert 'venue="jupiter"' not in body, "NaN is not a zero-second sample"


class _FakeVenue:
    kind = "kyberswap"
    breaker_state = "closed"
    calls = 0

    def supports(self, chain: object) -> bool:
        return True

    async def quote(self, request: object) -> str:
        _FakeVenue.calls += 1
        return "quoted"


async def test_the_provider_wrapper_times_the_venue_it_wraps() -> None:
    """The wrapper delegates identity and times the call the router already awaits."""
    latency = VenueLatencyMetrics()
    wrapped = VenueLatencyProvider(_FakeVenue(), histogram=latency)

    result = await wrapped.quote(object())

    assert result == "quoted"
    assert wrapped.kind == "kyberswap"
    assert wrapped.breaker_state == "closed"
    assert wrapped.supports(object()) is True
    assert 'ezchain_venue_latency_seconds_count{venue="kyberswap"} 1' in "\n".join(latency.render())


def test_the_real_router_still_reads_its_provider_through_the_wrapper() -> None:
    """Breaker state and timeout notification stay the provider's, not the wrapper's."""
    clock = {"now": 1000.0}
    jupiter = JupiterClient(clock=lambda: clock["now"])
    wrapped = VenueLatencyProvider(jupiter)
    router = QuoteRouter(
        providers=[wrapped, KyberSwapClient()],
        dexscreener=_NoDex(),  # type: ignore[arg-type]
        freshness=POLICY,
    )

    assert router.breakers() == {"jupiter": "closed", "kyberswap": "closed"}
    wrapped.record_timeout()
    assert jupiter.breaker_state == "open"
    assert router.breakers()["jupiter"] == "open"


async def test_the_router_ladder_times_each_venue_it_asks() -> None:
    """The router awaits ``provider.quote``; the wrapper times exactly that call.

    A buy is two venue calls -- the entry and its full-size exit -- so the count is a
    property of the router's own ladder, not of the wrapper inventing samples.
    """
    from ezchain_core import SourceRef, TokenSnapshot, utcnow

    latency = VenueLatencyMetrics()
    venue = _LadderVenue()

    class _Dex:
        async def get_token(self, chain: str, address: str, **_: Any) -> TokenSnapshot:
            return TokenSnapshot(
                chain=chain,
                address=address,
                symbol="TEST",
                name="Test",
                price_usd=Decimal("2"),
                liquidity_usd=Decimal("50000"),
                volume_24h_usd=Decimal("1000"),
                price_change_m5=None,
                price_change_h1=Decimal("1"),
                price_change_h6=None,
                price_change_h24=None,
                pairs=(),
                source=SourceRef("dexscreener", None, utcnow()),
                pair_created_at=None,
            )

    router = QuoteRouter(
        providers=[VenueLatencyProvider(venue, histogram=latency)],
        dexscreener=_Dex(),  # type: ignore[arg-type]
        freshness=POLICY,
    )

    quote = await router.quote(
        chain="base", token_address="0xtoken", side="buy", amount_usd=Decimal("10")
    )

    assert quote.kind == "kyberswap"
    body = "\n".join(latency.render())
    assert 'ezchain_venue_latency_seconds_count{venue="kyberswap"} 2' in body


class _LadderVenue:
    kind = "kyberswap"

    def supports(self, chain: Any) -> bool:
        return chain.id == "base"

    async def quote(self, request: Any) -> Any:
        from ezchain_core import Quote, SourceRef, utcnow

        price = Decimal("2")
        if request.side == "buy":
            amount_in, amount_out = request.amount_usd, request.amount_usd / price
        else:
            amount_in = request.quantity or Decimal("0")
            amount_out = amount_in * price
        return Quote(
            chain="base",
            token_in="0xusdc",
            token_out=request.token_address,
            side=request.side,
            amount_in=amount_in,
            amount_out=amount_out,
            price=price,
            price_impact_pct=Decimal("0"),
            fee_usd=Decimal("0"),
            kind="kyberswap",
            route="Test",
            source=SourceRef("kyberswap", None, utcnow()),
        )


def test_the_metrics_endpoint_renders_the_venue_histogram(session: Session) -> None:
    VENUE_LATENCY.reset()
    VENUE_LATENCY.observe("jupiter", 0.42)

    body = _client(session).get("/metrics").text

    assert 'ezchain_venue_latency_seconds_count{venue="jupiter"} 1' in body
    assert "ezchain_venue_latency_seconds_bucket{" in body
