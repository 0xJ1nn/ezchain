"""Persist normalised ``MarketEvent``s, and read them back as the same contract type.

The store is the query path for market observations; the JSONL event log stays the replay
input. Both are written from one normalised event, so there is no second parse of a
provider payload and no second definition of a market number.

What the store guarantees:

* **Idempotency is the schema's, not the caller's.** The primary key is the contract's own
  identity ``(source name, pair, source timestamp)``, the same triple replay dedupes on. A
  repeated observation is one row and no second outbox message; the returned count tells the
  caller what it added.
* **A failed write says why.** Nothing is swallowed and nothing is invented: a database
  error, an unreachable server or a schema mismatch surfaces as :class:`MarketStoreError`
  carrying the driver's message. An insert that ran inside the caller's transaction leaves
  that transaction failed, so the caller must roll back rather than continue.
* **Unknown stays unknown.** A null price, size, direction or confidence is written as null
  and read back as null. The store never substitutes zero.
* **Freshness is not stored.** ``age_seconds``/``is_stale`` are the reader's, computed from
  the row's source timestamp exactly as ``MarketEvent`` derives them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ezchain_api.models import MarketEventRow, MarketOutboxRow
from ezchain_core import MarketEvent, MarketEventError, SourceRef

#: One recorded observation is one message on this topic. A consumer subscribes here.
MARKET_EVENT_TOPIC = "market.event"

#: The contract's idempotency identity, as the table's primary key spells it.
IDENTITY_COLUMNS = ("source_name", "pair", "source_observed_at")

#: Selected as Core columns so a read behaves the same on a ``Connection`` and a ``Session``:
#: an ORM entity select would return objects through one and mappings through the other.
EVENT_COLUMNS = MarketEventRow.__table__
OUTBOX_COLUMNS = MarketOutboxRow.__table__


class MarketStoreError(RuntimeError):
    """A market observation could not be persisted or read back."""


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dialect(connection: Connection | Session) -> str:
    """The SQL dialect behind a raw connection or an ORM session."""
    if isinstance(connection, Session):
        return connection.get_bind().dialect.name
    return connection.dialect.name


def _values(event: MarketEvent) -> dict[str, Any]:
    return {
        "source_name": event.source.name,
        "pair": event.pair,
        "source_observed_at": _aware(event.source.observed_at),
        "chain": event.chain,
        "venue": event.venue,
        "price": event.price,
        "size": event.size,
        "direction": event.direction,
        "units": event.units,
        "confidence": event.confidence,
        "source_url": event.source.url,
        "observed_at": _aware(event.observed_at),
        "schema_version": event.schema_version,
    }


def _decimal(value: Any) -> Decimal | None:
    """A null is null; anything else is read back equal to what was written.

    A Postgres ``NUMERIC(38,18)`` returns the column's scale, so ``2.5`` comes back as
    ``2.500000000000000000``. The value is the same, but the string a read model prints
    would not match the recording. Insignificant trailing zeros are dropped (and only
    those — never a digit), so a round trip through the store reproduces the recorded form.
    """
    if value is None:
        return None
    return Decimal(format(Decimal(str(value)).normalize(), "f"))


def _row_to_event(row: Any) -> MarketEvent:
    mapping = row._mapping
    return MarketEvent(
        chain=mapping["chain"],
        venue=mapping["venue"],
        pair=mapping["pair"],
        price=_decimal(mapping["price"]),
        size=_decimal(mapping["size"]),
        direction=mapping["direction"],
        units=mapping["units"],
        source=SourceRef(
            mapping["source_name"],
            mapping["source_url"],
            _aware(mapping["source_observed_at"]),
        ),
        observed_at=_aware(mapping["observed_at"]),
        confidence=_decimal(mapping["confidence"]),
    )


def _identity(event: MarketEvent) -> tuple[str, str, datetime]:
    return (event.source.name, event.pair, _aware(event.source.observed_at))


def _distinct(events: Sequence[MarketEvent]) -> list[MarketEvent]:
    """One event per identity, first occurrence wins.

    A batch may name the same observation twice — a retry appended to a live batch, or two
    feeds that saw one source timestamp. Inserting both would make the statement's RETURNING
    answer ambiguous, so the batch is deduped the way replay dedupes a recording.
    """
    seen: dict[tuple[str, str, datetime], MarketEvent] = {}
    for event in events:
        seen.setdefault(_identity(event), event)
    return list(seen.values())


def _postgres_insert_written(
    connection: Connection | Session, payload: list[dict[str, Any]]
) -> list[Any]:
    from sqlalchemy.dialects.postgresql import insert as postgres_insert

    statement = postgres_insert(MarketEventRow).values(payload)
    statement = statement.on_conflict_do_nothing(index_elements=list(IDENTITY_COLUMNS))
    return list(
        connection.execute(
            statement.returning(
                MarketEventRow.source_name,
                MarketEventRow.pair,
                MarketEventRow.source_observed_at,
            )
        ).all()
    )


def _sqlite_insert_written(
    connection: Connection | Session, payload: list[dict[str, Any]]
) -> list[Any]:
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    statement = sqlite_insert(MarketEventRow).values(payload)
    statement = statement.on_conflict_do_nothing(index_elements=list(IDENTITY_COLUMNS))
    return list(
        connection.execute(
            statement.returning(
                MarketEventRow.source_name,
                MarketEventRow.pair,
                MarketEventRow.source_observed_at,
            )
        ).all()
    )


def _insert_events(
    connection: Connection | Session, events: Sequence[MarketEvent]
) -> list[MarketEvent]:
    """Insert observations, skipping the ones already stored.

    Returns the events this call actually wrote. ``RETURNING`` is what makes that exact: a
    row the conflict clause skipped is not returned, so a repeat produces no outbox message
    and no second write. A row count alone could not distinguish the two.
    """
    distinct = _distinct(events)
    if not distinct:
        return []
    dialect = _dialect(connection)
    payload = [_values(event) for event in distinct]
    if dialect == "postgresql":
        written = _postgres_insert_written(connection, payload)
    elif dialect == "sqlite":
        written = _sqlite_insert_written(connection, payload)
    else:  # pragma: no cover - no other dialect is deployed
        raise MarketStoreError(f"unsupported dialect for market events: {dialect}")
    keys = {(row[0], row[1], _aware(row[2])) for row in written}
    return [event for event in distinct if _identity(event) in keys]


def _outbox_rows(events: Sequence[MarketEvent], clock: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": str(uuid4()),
            "topic": MARKET_EVENT_TOPIC,
            "payload": event.to_dict(),
            "created_at": clock,
            "published_at": None,
            "attempts": 0,
        }
        for event in events
    ]


def record_events(
    connection: Connection,
    events: Iterable[MarketEvent],
    *,
    outbox: bool = True,
    now: datetime | None = None,
) -> int:
    """Append observations inside the caller's transaction; return rows newly written.

    A duplicate observation is not an error: the contract already decided the triple is one
    observation, so a repeat is a no-op. With ``outbox`` (the default) one outbox row per
    newly written event is appended in the same transaction, so a relay sees every
    observation exactly once and only after it commits.
    """
    batch = tuple(events)
    if not batch:
        return 0
    try:
        clock = now or datetime.now(UTC)
        added = _insert_events(connection, batch)
        if outbox and added:
            connection.execute(sa.insert(MarketOutboxRow), _outbox_rows(added, clock))
        return len(added)
    except MarketStoreError:
        raise
    except (SQLAlchemyError, MarketEventError) as exc:
        raise MarketStoreError(f"could not persist market events: {exc}") from exc


def record_events_transactionally(
    engine: Engine,
    events: Iterable[MarketEvent],
    *,
    outbox: bool = True,
    now: datetime | None = None,
) -> int:
    """Record one batch in its own transaction, committing the events and their outbox.

    A consumer never sees a message for a row that was rolled back, and a committed row
    always has its message. A failure raises :class:`MarketStoreError` and commits nothing.
    """
    try:
        with engine.begin() as connection:
            return record_events(connection, events, outbox=outbox, now=now)
    except MarketStoreError:
        raise
    except SQLAlchemyError as exc:
        raise MarketStoreError(f"could not persist market events: {exc}") from exc


def load_events(
    connection: Connection | Session,
    *,
    pair: str | None = None,
    chain: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> tuple[MarketEvent, ...]:
    """Read stored observations oldest first, ordered by ``(source timestamp, identity)``.

    Ordering is deterministic so a replay over the store produces the same sequence on every
    run: the source timestamp first, then the identity columns that make a row unique.
    A ``None`` filter means "no filter", never "zero".
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    statement = sa.select(EVENT_COLUMNS)
    if pair is not None:
        statement = statement.where(MarketEventRow.pair == pair)
    if chain is not None:
        statement = statement.where(MarketEventRow.chain == chain)
    if since is not None:
        statement = statement.where(MarketEventRow.source_observed_at >= _aware(since))
    if until is not None:
        statement = statement.where(MarketEventRow.source_observed_at < _aware(until))
    statement = statement.order_by(
        MarketEventRow.source_observed_at,
        MarketEventRow.source_name,
        MarketEventRow.pair,
    )
    if limit is not None:
        statement = statement.limit(limit)
    try:
        rows = connection.execute(statement).all()
    except SQLAlchemyError as exc:
        raise MarketStoreError(f"could not read market events: {exc}") from exc
    return tuple(_row_to_event(row) for row in rows)


def latest_events(
    connection: Connection | Session, *, chain: str | None = None
) -> tuple[MarketEvent, ...]:
    """The newest stored observation per ``(chain, venue, pair)``, ordered by that key.

    This is the read a stored-market surface or a token header needs: one row per market
    with its source and timestamp intact, so the caller ages it itself. A tie on the source
    timestamp breaks on the identity, so the choice is stable across queries.
    """
    ranked = sa.select(
        EVENT_COLUMNS,
        sa.func.row_number()
        .over(
            partition_by=(
                MarketEventRow.chain,
                MarketEventRow.venue,
                MarketEventRow.pair,
            ),
            order_by=(
                MarketEventRow.source_observed_at.desc(),
                MarketEventRow.source_name,
            ),
        )
        .label("rank"),
    ).subquery()
    statement = sa.select(ranked).where(ranked.c.rank == 1)
    if chain is not None:
        statement = statement.where(ranked.c.chain == chain)
    statement = statement.order_by(ranked.c.chain, ranked.c.venue, ranked.c.pair)
    try:
        rows = connection.execute(statement).all()
    except SQLAlchemyError as exc:
        raise MarketStoreError(f"could not read market events: {exc}") from exc
    return tuple(_row_to_event(row) for row in rows)


def pending_outbox(
    connection: Connection | Session, *, limit: int = 100
) -> tuple[dict[str, Any], ...]:
    """Unpublished outbox rows, oldest first. A relay drains these; the store never does."""
    statement = (
        sa.select(OUTBOX_COLUMNS)
        .where(MarketOutboxRow.published_at.is_(None))
        .order_by(MarketOutboxRow.created_at, MarketOutboxRow.id)
        .limit(limit)
    )
    try:
        rows = connection.execute(statement).all()
    except SQLAlchemyError as exc:
        raise MarketStoreError(f"could not read the market outbox: {exc}") from exc
    return tuple(dict(row._mapping) for row in rows)


def mark_published(
    connection: Connection, ids: Sequence[str], *, now: datetime | None = None
) -> int:
    """Mark outbox rows delivered; return how many changed.

    Delivery is at-least-once: a relay marks a row only after its publish returns, so a
    relay that dies in between re-delivers. The payload is the recorded event, and the
    consumer dedupes on the same identity the event table enforces.
    """
    if not ids:
        return 0
    clock = now or datetime.now(UTC)
    statement = (
        sa.update(MarketOutboxRow)
        .where(
            MarketOutboxRow.id.in_(list(ids)),
            MarketOutboxRow.published_at.is_(None),
        )
        .values(published_at=clock)
    )
    try:
        result = connection.execute(statement)
    except SQLAlchemyError as exc:
        raise MarketStoreError(f"could not mark the market outbox delivered: {exc}") from exc
    return int(result.rowcount or 0)


__all__ = [
    "IDENTITY_COLUMNS",
    "MARKET_EVENT_TOPIC",
    "MarketStoreError",
    "latest_events",
    "load_events",
    "mark_published",
    "pending_outbox",
    "record_events",
    "record_events_transactionally",
]
