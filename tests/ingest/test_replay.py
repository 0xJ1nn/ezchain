"""Replay a recorded market-event log and derive the same state twice.

The recording is the single source of truth: one JSONL line per normalised event, no
staleness stamped at write time. Replaying the same file with the same clock produces
identical derived state; replaying with a later clock ages the observations without
rewriting a byte. A corrupt, wrongly-versioned or unit-less recording is refused.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ezchain_core import (
    DEFAULT_STALENESS_SECONDS,
    MARKET_EVENT_SCHEMA_VERSION,
    MarketEvent,
    SourceRef,
)
from ezchain_ingest import EventLogError, ReplayHarness, read_events, write_events

BASE = 1_760_000_000


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _event(
    *,
    pair: str = "Mint1111111111111111111111111111111111111111",
    source_at: int = BASE,
    observed_at: int = BASE + 1,
    price: str = "2.5",
    direction: str | None = "buy",
) -> MarketEvent:
    return MarketEvent(
        chain="solana",
        venue="pumpfun",
        pair=pair,
        price=Decimal(price),
        size=Decimal("1.25"),
        direction=direction,  # type: ignore[arg-type]
        units="SOL",
        source=SourceRef("pumpfun-firehose", "https://example.test/firehose", _at(source_at)),
        observed_at=_at(observed_at),
    )


def _record(tmp_path: Path, events: list[MarketEvent]) -> Path:
    path = tmp_path / "market-events.jsonl"
    assert write_events(path, events) == len(events)
    return path


def test_recorded_market_events_replay_to_identical_state_twice(tmp_path: Path) -> None:
    path = _record(
        tmp_path,
        [
            _event(source_at=BASE),
            _event(source_at=BASE + 5, observed_at=BASE + 6, price="2.75"),
            _event(
                pair="Mint2222222222222222222222222222222222222222",
                source_at=BASE + 2,
                observed_at=BASE + 3,
            ),
        ],
    )
    now = _at(BASE + 30)
    first = ReplayHarness(path).derive(now)
    second = ReplayHarness(path).derive(now)

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.observed == 3
    assert first.duplicates == 0
    assert first.late == 0
    assert len(first.pairs) == 2
    assert first.to_dict()["pairs"][0]["event"]["source"]["name"] == "pumpfun-firehose"
    assert first.to_dict()["pairs"][0]["age_seconds"] == first.pairs[0].age_seconds

    for line in path.read_text(encoding="utf-8").splitlines():
        body = json.loads(line)
        assert body["schema_version"] == MARKET_EVENT_SCHEMA_VERSION
        # The recording is write-time clean; freshness is re-derived at read time.
        assert "age_seconds" not in body
        assert "stale" not in body


def test_replay_ages_observations_from_the_read_clock(tmp_path: Path) -> None:
    path = _record(tmp_path, [_event()])
    recorded_at = path.read_bytes()
    harness = ReplayHarness(path)

    fresh = harness.derive(_at(BASE + 10))
    stale = harness.derive(_at(BASE + int(DEFAULT_STALENESS_SECONDS) + 1))

    assert fresh.pairs[0].age_seconds == 10.0
    assert fresh.pairs[0].stale is False
    assert stale.pairs[0].stale is True
    assert stale.pairs[0].age_seconds > DEFAULT_STALENESS_SECONDS
    assert path.read_bytes() == recorded_at


def test_replay_dedupes_the_same_source_observation(tmp_path: Path) -> None:
    duplicate = _event()
    path = _record(
        tmp_path,
        [duplicate, duplicate, _event(source_at=BASE + 1, observed_at=BASE + 2)],
    )
    state = ReplayHarness(path).derive(_at(BASE + 20))
    assert state.observed == 3
    assert state.duplicates == 1
    assert len(state.pairs) == 1


def test_replay_flags_a_late_event_without_reordering_it(tmp_path: Path) -> None:
    path = _record(
        tmp_path,
        [
            _event(source_at=BASE),
            _event(source_at=BASE + 100, observed_at=BASE + 101, price="3"),
            _event(source_at=BASE + 50, observed_at=BASE + 102, price="2.8"),
        ],
    )
    state = ReplayHarness(path).derive(_at(BASE + 120))
    assert state.late == 1
    assert len(state.pairs) == 1
    assert state.pairs[0].event.source.observed_at == _at(BASE + 100)
    assert state.pairs[0].event.price == Decimal("3")


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("not json", "line 1"),
        (
            json.dumps(
                {
                    "schema_version": MARKET_EVENT_SCHEMA_VERSION + 1,
                    "chain": "solana",
                    "venue": "pumpfun",
                    "pair": "mint",
                    "price": None,
                    "size": None,
                    "direction": None,
                    "units": "SOL",
                    "source": {
                        "name": "pumpfun-firehose",
                        "url": None,
                        "observed_at": _at(BASE).isoformat(),
                    },
                    "source_timestamp": _at(BASE).isoformat(),
                    "observed_at": _at(BASE).isoformat(),
                }
            ),
            "schema version",
        ),
        (
            json.dumps(
                {
                    "schema_version": MARKET_EVENT_SCHEMA_VERSION,
                    "chain": "solana",
                    "venue": "pumpfun",
                    "pair": "mint",
                    "price": "1",
                    "size": "1",
                    "direction": "buy",
                    "units": "",
                    "source": {
                        "name": "pumpfun-firehose",
                        "url": None,
                        "observed_at": _at(BASE).isoformat(),
                    },
                    "source_timestamp": _at(BASE).isoformat(),
                    "observed_at": _at(BASE).isoformat(),
                }
            ),
            "units",
        ),
    ],
)
def test_replay_refuses_a_corrupt_market_recording(tmp_path: Path, line: str, message: str) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(EventLogError, match=message):
        read_events(path)


def test_replay_refuses_a_missing_recording(tmp_path: Path) -> None:
    with pytest.raises(EventLogError, match="missing"):
        ReplayHarness(tmp_path / "missing.jsonl").derive(_at(BASE))


def test_empty_recording_derives_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    state = ReplayHarness(path).derive(_at(BASE))
    assert state.observed == 0
    assert state.pairs == ()
