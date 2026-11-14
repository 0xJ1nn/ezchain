"""Live pump.fun ingest wiring: the package gate, the core contract, and the sinks.

Every test drives a deterministic fake stream. No test opens a socket, reads a
provider key, or depends on the wall clock: the staleness gate is advanced
explicitly, so a gap is reproducible.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pumpfire.health import StaleStream
from sqlalchemy.orm import Session, sessionmaker

from ezchain_api.db import Base
from ezchain_api.models import MarketEventRow
from ezchain_core import MarketEvent
from ezchain_ingest import FirehoseSource, load_events, read_events

FIXTURES_DIR = Path(__file__).parents[1] / "adapters" / "fixtures"
STALE_AFTER = 60.0


class FakeStream:
    """A stand-in for ``pumpfire.Firehose``: listeners in, decoded events out."""

    def __init__(self) -> None:
        self.listeners: list[Callable[[object], object]] = []

    def add_listener(self, callback: Callable[[object], object]) -> None:
        self.listeners.append(callback)

    def emit(self, raw: object) -> None:
        for listener in self.listeners:
            listener(raw)


@pytest.fixture
def raw_events() -> list[dict[str, Any]]:
    loaded = json.loads((FIXTURES_DIR / "firehose_sample.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, list)
    return loaded


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


def test_attach_registers_on_the_client_and_normalises_to_the_contract(
    raw_events: list[dict[str, Any]],
) -> None:
    stream = FakeStream()
    source = FirehoseSource(stream, stale_after_s=STALE_AFTER)
    assert len(stream.listeners) == 1

    stream.emit(raw_events[1])
    (entry,) = source.pull(now=1.0)

    assert isinstance(entry.event, MarketEvent)
    assert entry.kind == "trade"
    assert entry.pair == raw_events[1]["mint"]
    assert entry.source.name == "pumpfun-firehose"
    assert entry.chain == "solana"


def test_entry_is_refused_while_the_stream_is_stale(
    raw_events: list[dict[str, Any]],
) -> None:
    """The AC the package could not close: the app's entry point enforces require_live."""
    source = FirehoseSource(stale_after_s=STALE_AFTER)
    assert source.receive(raw_events[1], now=0.0) is not None

    with pytest.raises(StaleStream) as raised:
        source.pull(now=STALE_AFTER + 1)

    assert raised.value.decision.stale is True
    assert raised.value.decision.reason == "stale"
    # A refused entry is not silently drained: the observation is still buffered.
    assert source.census().depth == 1
    # At the budget the stream is still live, so the same entry passes.
    assert len(source.pull(now=STALE_AFTER)) == 1


def test_a_stale_stream_refuses_store_entries_and_writes_nothing(
    raw_events: list[dict[str, Any]], store_session: Session
) -> None:
    source = FirehoseSource(stale_after_s=STALE_AFTER)
    assert source.receive(raw_events[1], now=0.0) is not None

    with pytest.raises(StaleStream):
        source.record(store_session, now=STALE_AFTER + 1)
    store_session.commit()
    assert _rows(store_session) == 0

    assert source.record(store_session, now=STALE_AFTER) == 1
    store_session.commit()
    (stored,) = load_events(store_session)
    assert stored.pair == raw_events[1]["mint"]


def test_the_contract_reads_back_unchanged_from_the_store(
    raw_events: list[dict[str, Any]], store_session: Session
) -> None:
    source = FirehoseSource()
    event = source.receive(raw_events[4], now=0.0)
    assert event is not None

    assert source.record(store_session, now=1.0) == 1
    store_session.commit()

    (stored,) = load_events(store_session)
    assert stored == event.event
    assert stored.venue == "pumpswap"


def test_both_venues_take_the_same_path(
    raw_events: list[dict[str, Any]],
) -> None:
    """pumpfun and PumpSwap are data, not branches: one adapter, one gate, one contract."""
    source = FirehoseSource(stale_after_s=STALE_AFTER)
    assert source.receive(raw_events[1], now=0.0) is not None
    assert source.receive(raw_events[4], now=1.0) is not None

    entries = source.pull(now=2.0)

    assert {entry.venue for entry in entries} == {"pumpfun", "pumpswap"}
    assert {entry.chain for entry in entries} == {"solana"}


def test_the_recording_is_the_contract_without_a_freshness_verdict(
    raw_events: list[dict[str, Any]], tmp_path: Path
) -> None:
    path = tmp_path / "firehose.jsonl"
    source = FirehoseSource(stale_after_s=STALE_AFTER)
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
    source = FirehoseSource(stale_after_s=STALE_AFTER, pending_limit=2)
    for index in range(3):
        assert source.receive({**raw_events[1], "signature": f"sig{index}"}, now=float(index))

    health = source.health(now=3.0)

    assert health["gate"]["live"] is True
    assert health["gate"]["stale_after_s"] == STALE_AFTER
    assert health["queue"]["maxsize"] == 2
    assert health["queue"]["depth"] == 2
    assert health["queue"]["dropped"] == 1
    assert health["received"] == 3

    stale = source.health(now=STALE_AFTER + 3.0)
    assert stale["gate"]["live"] is False
    assert stale["gate"]["reason"] == "stale"


def test_a_rejected_payload_proves_life_but_never_becomes_an_entry() -> None:
    source = FirehoseSource(stale_after_s=STALE_AFTER)
    assert source.receive({"kind": "trade", "mint": ""}, now=0.0) is None

    assert source.pull(now=1.0) == ()
    health = source.health(now=1.0)
    assert health["received"] == 0
    assert health["rejected"] == 1
    # The message still refreshed the socket's liveness, as the package's reader does.
    assert health["gate"]["live"] is True
