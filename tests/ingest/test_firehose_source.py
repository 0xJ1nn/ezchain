"""Live pump.fun ingest wiring: the gate, the entry rule, the contract and the sinks.

Two layers, deliberately separated:

* The app-side entry rule — liveness observed only on a decoded event, and every
  drained observation aged on its own handoff clock — is driven through the
  injected :class:`~ezchain_ingest.source.StreamGate` and
  :class:`~ezchain_ingest.source.ObservationQueue` seams. Those tests need no
  optional package and always run.
* The same wiring against the real ``pumpfire.health`` gate and queue is marked
  :data:`requires_extra`: it runs only where ``ezchain-ingest[firehose]`` is
  installed, and skips otherwise.

Every test drives a deterministic fake stream. No test opens a socket, reads a
provider key, or depends on the wall clock: the clock is passed explicitly, so a
gap is reproducible.
"""

from __future__ import annotations

import importlib.util
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from ezchain_adapters.firehose import normalize_firehose_event
from ezchain_api.db import Base
from ezchain_api.models import MarketEventRow
from ezchain_core import MarketEvent
from ezchain_ingest import FirehoseSource, load_events, read_events
from ezchain_ingest.handoff import PendingObservation, split_stale

FIXTURES_DIR = Path(__file__).parents[1] / "adapters" / "fixtures"
STALE_AFTER = 60.0


def _have_extra() -> bool:
    try:
        return importlib.util.find_spec("pumpfire.health") is not None
    except ModuleNotFoundError:
        return False


HAVE_EXTRA = _have_extra()

requires_extra = pytest.mark.skipif(
    not HAVE_EXTRA, reason="the pumpfun-firehose extra is not installed"
)


class FakeStream:
    """A stand-in for ``pumpfire.Firehose``: listeners in, decoded events out."""

    def __init__(self) -> None:
        self.listeners: list[Callable[[object], object]] = []

    def add_listener(self, callback: Callable[[object], object]) -> None:
        self.listeners.append(callback)

    def emit(self, raw: object) -> None:
        for listener in self.listeners:
            listener(raw)


@dataclass(frozen=True)
class FakeDecision:
    """``pumpfire.health.GateDecision``'s shape, without the package."""

    live: bool
    stale: bool
    age_s: float | None
    reason: str
    stale_after_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "live": self.live,
            "stale": self.stale,
            "age_s": self.age_s,
            "reason": self.reason,
            "stale_after_s": self.stale_after_s,
        }


class FakeStaleStream(RuntimeError):
    """``pumpfire.health.StaleStream``'s contract: a refusal carries the decision."""

    def __init__(self, decision: FakeDecision) -> None:
        self.decision = decision
        super().__init__(f"stream is not live ({decision.reason})")


class FakeGate:
    """The package gate's contract, restated here so the rule runs without it.

    Age is measured from the last :meth:`observe` on the caller's clock, exactly as
    the package's monotonic gate does; only the clock source differs (explicit
    rather than ``time.monotonic``).
    """

    def __init__(self, stale_after_s: float) -> None:
        self.stale_after_s = float(stale_after_s)
        self.observations: list[float] = []
        self._last: float | None = None

    def observe(self, now: float | None = None) -> None:
        self._last = time.monotonic() if now is None else now
        self.observations.append(self._last)

    def check(self, now: float | None = None) -> FakeDecision:
        if self._last is None:
            return FakeDecision(False, True, None, "no-event", self.stale_after_s)
        clock = time.monotonic() if now is None else now
        age = max(0.0, clock - self._last)
        stale = age > self.stale_after_s
        return FakeDecision(
            not stale, stale, age, "stale" if stale else "fresh", self.stale_after_s
        )

    def require_live(self, now: float | None = None) -> FakeDecision:
        decision = self.check(now)
        if not decision.live:
            raise FakeStaleStream(decision)
        return decision


@dataclass(frozen=True)
class FakeCensus:
    """``pumpfire.health.QueueCensus``'s shape, without the package."""

    depth: int
    maxsize: int
    enqueued: int
    dropped: int

    def as_dict(self) -> dict[str, int]:
        return {
            "depth": self.depth,
            "maxsize": self.maxsize,
            "enqueued": self.enqueued,
            "dropped": self.dropped,
        }


class FakeQueue:
    """``pumpfire.health.EventQueue``'s contract: bounded, lossy, counted, sync census."""

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.enqueued = 0
        self.dropped = 0
        self._items: list[PendingObservation] = []

    def put_nowait(self, item: PendingObservation) -> bool:
        lost = False
        if len(self._items) >= self.maxsize:
            self._items.pop(0)
            self.dropped += 1
            lost = True
        self._items.append(item)
        self.enqueued += 1
        return lost

    def get_nowait(self) -> PendingObservation:
        return self._items.pop(0)

    def empty(self) -> bool:
        return not self._items

    def census(self) -> FakeCensus:
        return FakeCensus(len(self._items), self.maxsize, self.enqueued, self.dropped)


@pytest.fixture
def gate() -> FakeGate:
    return FakeGate(STALE_AFTER)


@pytest.fixture
def source(gate: FakeGate) -> FirehoseSource:
    """The seam wired with the fake gate and queue: no optional package needed."""
    return FirehoseSource(gate=gate, queue=FakeQueue(10_000))


@pytest.fixture
def raw_events() -> list[dict[str, Any]]:
    loaded = json.loads((FIXTURES_DIR / "firehose_sample.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded["events"]


@pytest.fixture
def store_session(tmp_path: Path) -> Iterator[Session]:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            yield session
    finally:
        engine.dispose()


def _rows(session: Session) -> int:
    return int(session.execute(sa.select(sa.func.count()).select_from(MarketEventRow)).scalar_one())


def _event(pair: str) -> Any:
    event = normalize_firehose_event(
        {"kind": "trade", "mint": pair, "timestamp": 1760000060, "price_sol": 1, "sol": 1}
    )
    assert event is not None
    return event


def test_attach_registers_on_the_client_and_normalises_to_the_contract(
    raw_events: list[dict[str, Any]],
) -> None:
    stream = FakeStream()
    source = FirehoseSource(stream, gate=FakeGate(STALE_AFTER), queue=FakeQueue(100))
    assert len(stream.listeners) == 1

    stream.emit(raw_events[1])
    (entry,) = source.pull(now=1.0)

    assert isinstance(entry.event, MarketEvent)
    assert entry.kind == "trade"
    assert entry.pair == raw_events[1]["mint"]
    assert entry.source.name == "pumpfun-firehose"
    assert entry.chain == "solana"


def test_entry_is_refused_while_the_stream_is_stale(
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
) -> None:
    """A gap is untradeable: the entry read refuses and drains nothing."""
    assert source.receive(raw_events[1], now=0.0) is not None

    with pytest.raises(FakeStaleStream) as raised:
        source.pull(now=STALE_AFTER + 1)

    assert raised.value.decision.stale is True
    assert raised.value.decision.reason == "stale"
    # A refused entry is not silently drained: the observation is still buffered.
    assert source.census().depth == 1
    # At the budget the stream is still live, so the same entry passes.
    assert len(source.pull(now=STALE_AFTER)) == 1


def test_a_stale_stream_refuses_store_entries_and_writes_nothing(
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
    store_session: Session,
) -> None:
    assert source.receive(raw_events[1], now=0.0) is not None

    with pytest.raises(FakeStaleStream):
        source.record(store_session, now=STALE_AFTER + 1)
    store_session.commit()
    assert _rows(store_session) == 0

    assert source.record(store_session, now=STALE_AFTER) == 1
    store_session.commit()
    (stored,) = load_events(store_session)
    assert stored.pair == raw_events[1]["mint"]


def test_the_contract_reads_back_unchanged_from_the_store(
    gate: FakeGate,
    raw_events: list[dict[str, Any]],
    store_session: Session,
) -> None:
    source = FirehoseSource(gate=gate, queue=FakeQueue(100))
    event = source.receive(raw_events[4], now=0.0)
    assert event is not None

    assert source.record(store_session, now=1.0) == 1
    store_session.commit()

    (stored,) = load_events(store_session)
    assert stored == event.event
    assert stored.venue == "pumpswap"


def test_both_venues_take_the_same_path(
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
) -> None:
    """pumpfun and PumpSwap are data, not branches: one adapter, one gate, one contract."""
    assert source.receive(raw_events[1], now=0.0) is not None
    assert source.receive(raw_events[4], now=1.0) is not None

    entries = source.pull(now=2.0)

    assert {entry.venue for entry in entries} == {"pumpfun", "pumpswap"}
    assert {entry.chain for entry in entries} == {"solana"}


def test_the_recording_is_the_contract_without_a_freshness_verdict(
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    path = tmp_path / "firehose.jsonl"
    assert source.receive(raw_events[1], now=0.0) is not None
    assert source.append_log(path, now=1.0) == 1

    assert source.receive(raw_events[2], now=2.0) is not None
    assert source.append_log(path, now=3.0) == 1

    recorded = read_events(path)
    assert [event.direction for event in recorded] == ["buy", "sell"]
    assert "stale" not in path.read_text(encoding="utf-8")


def test_health_surfaces_the_gate_and_backpressure(
    raw_events: list[dict[str, Any]],
) -> None:
    source = FirehoseSource(gate=FakeGate(STALE_AFTER), queue=FakeQueue(2))
    for index in range(3):
        assert source.receive({**raw_events[1], "signature": f"sig{index}"}, now=float(index))

    health = source.health(now=3.0)

    assert health["gate"]["live"] is True
    assert health["gate"]["stale_after_s"] == STALE_AFTER
    assert health["gate"]["age_s"] == pytest.approx(1.0)
    assert health["queue"]["maxsize"] == 2
    assert health["queue"]["depth"] == 2
    assert health["queue"]["dropped"] == 1
    assert health["dropped"] == 1
    assert health["received"] == 3
    assert health["decoded_ratio"] == 1.0

    stale = source.health(now=STALE_AFTER + 3.0)
    assert stale["gate"]["live"] is False
    assert stale["gate"]["reason"] == "stale"


def test_health_reports_the_decode_ratio_with_no_traffic_as_unknown(
    source: FirehoseSource,
) -> None:
    idle = source.health(now=0.0)

    assert idle["received"] == 0
    assert idle["decoded_ratio"] is None
    assert idle["rejected_ratio"] is None
    assert idle["gate"]["age_s"] is None

    assert source.receive({"kind": "trade", "mint": ""}, now=1.0) is None
    assert source.receive({"kind": "trade", "mint": "m", "timestamp": 1760000060}, now=2.0)
    mixed = source.health(now=3.0)
    assert mixed["received"] == 1
    assert mixed["rejected"] == 1
    assert mixed["decoded_ratio"] == 0.5
    assert mixed["rejected_ratio"] == 0.5


def test_a_stream_that_only_delivers_rejects_goes_stale_and_serves_nothing(
    gate: FakeGate,
    source: FirehoseSource,
) -> None:
    """Liveness is trade data, not socket traffic: a rejected payload is not life.

    Refreshing the gate before decoding let an adapter-level reject mark the stream
    live, and :meth:`pull` then handed out a pre-gap backlog. Here the stream only
    ever delivers rejects, so it never becomes live and the entry read refuses.
    """
    for second in range(0, 1800, 300):
        assert source.receive({"kind": "trade", "mint": ""}, now=float(second)) is None

    assert gate.observations == []
    health = source.health(now=1800.0)
    assert health["gate"]["live"] is False
    assert health["gate"]["reason"] == "no-event"
    assert health["received"] == 0
    assert health["rejected"] == 6
    with pytest.raises(FakeStaleStream):
        source.pull(now=1800.0)


def test_only_a_decoded_event_is_the_proof_of_life(
    gate: FakeGate,
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
) -> None:
    assert source.receive({"kind": "trade", "mint": ""}, now=0.0) is None
    assert gate.observations == []

    assert source.receive(raw_events[1], now=5.0) is not None
    assert gate.observations == [5.0]
    assert source.health(now=10.0)["gate"]["live"] is True


def test_a_recovered_stream_serves_only_what_it_can_defend(
    source: FirehoseSource,
    raw_events: list[dict[str, Any]],
) -> None:
    """The reproduced failure: an 1800 s gap, one fresh event, and the old entry served.

    The gate is live again because of the fresh event. The observation buffered
    before the gap is still 1800 s old on its own handoff clock, so the entry read
    refuses it, counts it, and returns only the fresh observation.
    """
    buffered = source.receive(raw_events[1], now=0.0)
    assert buffered is not None
    fresh = source.receive(
        {"kind": "trade", "mint": "FreshMint111111111111111111111111111111111", "timestamp": 1},
        now=1800.0,
    )
    assert fresh is not None

    entries = source.pull(now=1800.0)

    assert [entry.pair for entry in entries] == [fresh.pair]
    assert buffered.pair not in {entry.pair for entry in entries}
    assert source.health(now=1800.0)["refused_stale"] == 1
    assert source.census().depth == 0


def test_the_split_stale_rule_ages_every_item_independently() -> None:
    """The one entry rule, stated on its own: order kept, each refusal counted."""
    items = [
        PendingObservation(event=_event("stale"), clock=0.0),
        PendingObservation(event=_event("edge"), clock=60.0),
        PendingObservation(event=_event("fresh"), clock=120.0),
    ]

    fresh, refused = split_stale(items, now=120.0, stale_after_s=60.0)

    # Exactly the budget is still servable; past it is not.
    assert [event.pair for event in fresh] == ["edge", "fresh"]
    assert refused == 1


@requires_extra
def test_the_real_package_gate_refuses_a_stale_stream(raw_events: list[dict[str, Any]]) -> None:
    """The package's own gate and queue, through the same seam (needs the extra)."""
    from pumpfire.health import StaleStream

    source = FirehoseSource(stale_after_s=STALE_AFTER)
    assert source.receive(raw_events[1], now=0.0) is not None

    with pytest.raises(StaleStream) as raised:
        source.pull(now=STALE_AFTER + 1)
    assert raised.value.decision.reason == "stale"
    assert len(source.pull(now=STALE_AFTER)) == 1


@requires_extra
def test_the_real_package_queue_drops_and_counts(raw_events: list[dict[str, Any]]) -> None:
    source = FirehoseSource(stale_after_s=STALE_AFTER, pending_limit=2)
    for index in range(3):
        assert source.receive({**raw_events[1], "signature": f"sig{index}"}, now=float(index))

    health = source.health(now=3.0)

    assert health["queue"] == {"depth": 2, "maxsize": 2, "enqueued": 3, "dropped": 1}
    assert health["dropped"] == 1
