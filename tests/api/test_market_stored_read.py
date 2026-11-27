"""The stored markets list is a bounded read (GH-104).

``GET /api/v1/markets/stored`` used to fetch up to 100,000 rows, filter them in Python
and slice a page. These tests pin the three properties that replaced that: the database
answers the filters, the database cuts the page, and the work a page does not grow with the
table. The last one is measured, not asserted from the shape of the code: SQLite's progress
handler counts the steps a statement takes, and the count is identical for a table 50×
larger because the statement walks the index and stops at the page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import ezchain_api.models  # noqa: F401  (register tables on Base.metadata)
from ezchain_api.config import Settings
from ezchain_api.db import Base
from ezchain_api.deps import get_session
from ezchain_api.models import MarketSnapshotRow
from ezchain_api.routers import markets

NOW = datetime.now(UTC)
PAGE_FILTERS = {
    "min_liquidity_usd": "10000",
    "max_change_h24": "5",
    "min_pair_age_minutes": "30",
}


@pytest.fixture(autouse=True)
def _frozen_market_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every stored-markets read at the same instant the fixtures were built at.

    The page compares pair ages against the request time; rows seeded exactly on the
    30-minute boundary would flip as the suite advanced by milliseconds, changing the
    expected total by one on a loaded host. The router reads ``datetime.now(UTC)``, so the
    module's ``datetime`` is replaced with a subclass pinned to ``NOW``.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW

    monkeypatch.setattr(markets, "datetime", _FrozenDatetime)



def _settings() -> Settings:
    return Settings(
        environment="test",
        data_mode="fixture",
        database_url="sqlite+pysqlite://",
        auto_create_schema=False,
    )


def _client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(markets.router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[markets.get_settings] = _settings
    return TestClient(app)


def _rows(count: int, *, chain: str = "ethereum") -> list[dict[str, Any]]:
    """A seed whose order is the same in SQL and in Python: distinct liquidity and time.

    Pair ages sit half a minute off every whole minute, so the endpoint's read clock — a
    moment after ``NOW`` — cannot move a row across a pair-age filter's boundary while its
    page is compared with the Python pass measured at ``NOW``.
    """
    rows: list[dict[str, Any]] = []
    for index in range(count):
        rows.append(
            {
                "id": f"{chain}-{index:06d}",
                "chain": chain,
                "token_address": f"0x{index:040x}",
                "symbol": f"T{index}",
                "name": f"Token {index}",
                "source": "dexscreener",
                "price_usd": Decimal("1.5"),
                "liquidity_usd": Decimal(index * 1000) if index % 3 else None,
                "volume_24h_usd": Decimal(index * 10),
                # A third of the rows have no change, a third are outside a max of 5.
                "price_change_h24": None if index % 4 == 0 else Decimal(index % 11 - 5),
                "observed_at": NOW - timedelta(seconds=index),
                "dex": "uniswap" if index % 2 else "orca",
                "pair_created_at": (
                    None if index % 7 == 0 else NOW - timedelta(minutes=index, seconds=-30)
                ),
                "raw": {},
            }
        )
    return rows


def _seed(engine: Engine, rows: list[dict[str, Any]]) -> None:
    with Session(engine) as session:
        session.execute(insert(MarketSnapshotRow), rows)
        session.commit()


def _memory_engine() -> Engine:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _legacy_page(
    session: Session,
    *,
    chain: str,
    limit: int,
    offset: int,
    min_liquidity_usd: Decimal,
    max_change_h24: Decimal,
    min_pair_age_minutes: int,
    now: datetime,
) -> tuple[list[str], int, dict[str, int]]:
    """What the endpoint returned before GH-104: fetch the table, filter and slice here."""
    statement = select(MarketSnapshotRow).order_by(
        MarketSnapshotRow.liquidity_usd.is_(None),
        MarketSnapshotRow.liquidity_usd.desc(),
        MarketSnapshotRow.observed_at.desc(),
    )
    if chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == chain)
    unknown: dict[str, int] = {}
    kept: list[MarketSnapshotRow] = []
    for row in session.scalars(statement):
        missing = set()
        liquidity = row.liquidity_usd
        change = row.price_change_h24
        created = row.pair_created_at
        if liquidity is None:
            missing.add("liquidity_usd")
        if change is None:
            missing.add("price_change_h24")
        if created is None:
            missing.add("pair_created_at")
        if missing:
            for field in missing:
                unknown[field] = unknown.get(field, 0) + 1
            continue
        assert liquidity is not None and change is not None and created is not None
        if liquidity < min_liquidity_usd or change > max_change_h24:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if (now - created).total_seconds() / 60.0 < min_pair_age_minutes:
            continue
        kept.append(row)
    return [row.id for row in kept[offset : offset + limit]], len(kept), unknown


def test_a_page_reads_the_same_number_of_rows_whatever_the_table_holds() -> None:
    """The page is cut in SQL, so the page statement's step count ignores the table size.

    A progress handler runs every instruction SQLite executes. It is armed only while the
    page statement runs — the one that reads ``market_snapshots`` as rows — because the
    totals are one aggregate over the filtered set by contract: the response reports
    ``total`` and a count per unknown filter, and those describe the population. If the
    page itself went back to materialising the table, the 10,000-row count would be tens of
    times the 200-row one; they are equal here.
    """

    def page_steps(engine: Engine) -> int:
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        counted = {"steps": 0, "page": -1}

        def tick() -> int:
            counted["steps"] += 1
            return 0

        def raw(connection: Any) -> Any:
            return connection.connection.dbapi_connection

        def armed(statement: str) -> bool:
            return statement.lstrip().startswith("SELECT market_snapshots")

        @event.listens_for(engine, "before_cursor_execute")
        def before(connection: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            if armed(statement):
                counted["steps"] = 0
                raw(connection).set_progress_handler(tick, 1)

        @event.listens_for(engine, "after_cursor_execute")
        def after(connection: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
            if armed(statement):
                raw(connection).set_progress_handler(None, 0)
                counted["page"] = counted["steps"]

        try:
            body = _client(session).get(
                "/api/v1/markets/stored", params={"chain": "ethereum", "limit": 50}
            )
            assert body.status_code == 200
            assert len(body.json()["rows"]) == 50
        finally:
            for name, listener in (
                ("before_cursor_execute", before),
                ("after_cursor_execute", after),
            ):
                event.remove(engine, name, listener)
            session.close()
        assert counted["page"] > 0
        return counted["page"]

    small = _memory_engine()
    _seed(small, _rows(200))
    large = _memory_engine()
    _seed(large, _rows(10_000))

    assert page_steps(small) == page_steps(large)


def test_the_page_statement_carries_a_limit(caplog: pytest.LogCaptureFixture) -> None:
    """The page is a LIMIT/OFFSET statement, not a filter over the whole table."""
    engine = _memory_engine()
    _seed(engine, _rows(120))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if "FROM market_snapshots" in statement:
            statements.append(statement)

    try:
        body = _client(session).get("/api/v1/markets/stored", params={"limit": 25}).json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        session.close()

    assert len(body["rows"]) == 25
    page = [
        statement for statement in statements if statement.startswith("SELECT market_snapshots")
    ]
    assert page, statements
    assert "LIMIT" in page[0]


def test_a_ten_thousand_row_table_returns_the_page_the_old_code_would() -> None:
    """The SQL filters and the SQL slice agree with the Python pass they replaced."""
    engine = _memory_engine()
    _seed(engine, _rows(10_000))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    client = _client(session)
    expected_ids, expected_total, expected_unknown = _legacy_page(
        session,
        chain="all",
        limit=20,
        offset=60,
        min_liquidity_usd=Decimal(PAGE_FILTERS["min_liquidity_usd"]),
        max_change_h24=Decimal(PAGE_FILTERS["max_change_h24"]),
        min_pair_age_minutes=int(PAGE_FILTERS["min_pair_age_minutes"]),
        now=NOW,
    )

    body = client.get(
        "/api/v1/markets/stored", params={**PAGE_FILTERS, "chain": "all", "limit": 20, "offset": 60}
    ).json()

    assert [row["token_address"] for row in body["rows"]] == [
        f"0x{int(row_id.rsplit('-', 1)[1]):040x}" for row_id in expected_ids
    ]
    assert body["total"] == expected_total
    assert body["offset"] == 60
    assert body["applied_filters"]["unknown_excluded"] == expected_unknown


def test_the_cursor_pages_on_the_same_anchor_and_still_validates_its_filters() -> None:
    """The cursor format is the API contract: same anchor, same filter match, same refusals."""
    engine = _memory_engine()
    _seed(engine, _rows(300))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    client = _client(session)

    first = client.get("/api/v1/markets/stored", params={"chain": "all", "limit": 10}).json()
    cursor = first["next_cursor"]
    assert cursor is not None
    second = client.get(
        "/api/v1/markets/stored", params={"chain": "all", "limit": 10, "cursor": cursor}
    ).json()

    assert second["offset"] == 10
    assert {row["token_address"] for row in second["rows"]}.isdisjoint(
        row["token_address"] for row in first["rows"]
    )

    mismatch = client.get(
        "/api/v1/markets/stored",
        params={"chain": "all", "limit": 10, "cursor": cursor, "min_liquidity_usd": "1"},
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"]["code"] == "INVALID_CURSOR"


def test_a_cursor_whose_anchor_left_the_result_is_refused() -> None:
    """The anchor is looked up in the filtered set, so a row that left it names no position.

    The rows behind a cursor can change between pages: a deleted row, or one a filter now
    excludes because its liquidity moved. Resuming from a position the row no longer has
    would skip or repeat rows, so the cursor is refused instead.
    """
    engine = _memory_engine()
    _seed(engine, _rows(60))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    client = _client(session)

    first = client.get("/api/v1/markets/stored", params={"chain": "all", "limit": 5}).json()
    cursor = first["next_cursor"]
    anchor_token = first["rows"][-1]["token_address"]

    session.execute(
        delete(MarketSnapshotRow).where(MarketSnapshotRow.token_address == anchor_token)
    )
    session.commit()
    remaining = session.scalar(
        select(MarketSnapshotRow.id).where(MarketSnapshotRow.token_address == anchor_token)
    )
    assert remaining is None
    gone = client.get(
        "/api/v1/markets/stored", params={"chain": "all", "limit": 5, "cursor": cursor}
    )
    assert gone.status_code == 400
    assert gone.json()["detail"]["code"] == "INVALID_CURSOR"
    assert "no longer exists" in gone.json()["detail"]["message"]

    second = client.get("/api/v1/markets/stored", params={"chain": "all", "limit": 5}).json()
    cursor = second["next_cursor"]
    changed = second["rows"][-1]["token_address"]
    session.execute(
        update(MarketSnapshotRow)
        .where(MarketSnapshotRow.token_address == changed)
        .values(liquidity_usd=None)
    )
    session.commit()
    excluded = client.get(
        "/api/v1/markets/stored",
        params={"chain": "all", "limit": 5, "cursor": cursor, "min_liquidity_usd": "0"},
    )
    # The filter set differs from the cursor's, so the filter check refuses this one first.
    # The deletion above is what proves the anchor lookup itself refuses an unknown row.
    assert excluded.status_code == 400
    assert excluded.json()["detail"]["code"] == "INVALID_CURSOR"


def test_unknown_liquidity_is_never_compared_as_zero() -> None:
    """A NULL is excluded from a floor and counted, not sorted below the floor and dropped."""
    engine = _memory_engine()
    _seed(engine, _rows(30))
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    client = _client(session)

    unfiltered = client.get("/api/v1/markets/stored", params={"chain": "all", "limit": 200}).json()
    unknown_rows = [row for row in unfiltered["rows"] if row["liquidity_usd"] is None]
    assert unknown_rows, "the seed has rows with unknown liquidity"

    filtered = client.get(
        "/api/v1/markets/stored",
        params={"chain": "all", "limit": 200, "min_liquidity_usd": "0"},
    ).json()

    assert all(row["liquidity_usd"] is not None for row in filtered["rows"])
    assert filtered["applied_filters"]["unknown_excluded"]["liquidity_usd"] == len(unknown_rows)
    assert filtered["total"] == unfiltered["total"] - len(unknown_rows)


def test_the_indexed_order_is_the_order_the_list_publishes() -> None:
    """Rows come back in the published order, with unknown liquidity as the tail.

    The comparison is against the statement the endpoint used to run, because that order is
    what clients already page through: this refactor changes where the rows are cut, not how
    they sort. On SQLite the money columns hold the exact decimal *text*, so the column's own
    order is a character order there; that predates this change and is left alone, since
    fixing it would renumber every stored page.
    """
    engine = _memory_engine()
    _seed(engine, _rows(120))
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    expected = list(
        session.scalars(
            select(MarketSnapshotRow.id).order_by(
                MarketSnapshotRow.liquidity_usd.is_(None),
                MarketSnapshotRow.liquidity_usd.desc(),
                MarketSnapshotRow.observed_at.desc(),
            )
        )
    )
    rows = (
        _client(session)
        .get("/api/v1/markets/stored", params={"chain": "all", "limit": 200})
        .json()["rows"]
    )

    assert [row["token_address"] for row in rows] == [
        f"0x{int(row_id.rsplit('-', 1)[1]):040x}" for row_id in expected
    ]
    first_unknown = next(index for index, row in enumerate(rows) if row["liquidity_usd"] is None)
    assert all(row["liquidity_usd"] is None for row in rows[first_unknown:])
    assert all(row["liquidity_usd"] is not None for row in rows[:first_unknown])
