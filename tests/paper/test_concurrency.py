"""Concurrency regression: two sells cannot spend the same paper position."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ezchain_api.models import Base, PaperFillRow
from ezchain_api.services import paper

from .conftest import (
    CHAIN,
    TOKEN,
    FakeDexScreener,
    FakeJupiter,
    make_request,
    make_settings,
    make_snapshot,
    place,
)


def test_concurrent_full_sells_never_oversell(tmp_path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'paper-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter(price=Decimal("2000"))

    with maker() as session:
        opened = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(side="buy", amount_usd="100", key="race-open"),
        )
        assert opened.status == "filled"
    with maker() as session:
        held = paper.positions(session)[0]["quantity"]
        assert Decimal(str(held)) > 0

    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def sell(index: int) -> str:
        try:
            with maker() as session:
                barrier.wait(timeout=10)
                order = place(
                    session,
                    settings,
                    dex,
                    jupiter,
                    make_request(
                        side="sell",
                        amount_usd="1000",
                        key=f"race-sell-{index}",
                    ),
                )
                return order.status
        except BaseException as exc:  # noqa: BLE001 - a thread failure is the test failing
            errors.append(exc)
            return "error"

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(sell, range(4)))

    assert not errors, errors
    assert statuses.count("filled") == 1, statuses
    with maker() as session:
        fills = session.query(PaperFillRow).filter(PaperFillRow.side == "sell").all()
        sold = sum((Decimal(str(fill.quantity)) for fill in fills), Decimal(0))
        assert sold == Decimal(str(held))
        assert paper.positions(session) == []
    engine.dispose()


def test_concurrent_buys_on_different_tokens_cannot_overdraw_cash(tmp_path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'paper-cash-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    # Keep the sizing policy from becoming the gate under test: on a $100 account the
    # default 2% mid cap is $2, below the $10 minimum, so both buys would refuse for
    # size before ever reaching the cash check.
    settings = make_settings(
        paper_starting_cash_usd=Decimal("100"),
        trade_min_amount_usd=Decimal("1"),
        trade_min_pct_equity=Decimal("0"),
        trade_mid_min_amount_usd=Decimal("1"),
        trade_mid_min_pct_equity=Decimal("0"),
        trade_max_pct_equity=Decimal("100"),
        trade_mid_max_pct_equity=Decimal("100"),
    )
    dex = FakeDexScreener(make_snapshot(price=Decimal("1"), liquidity=Decimal("1000000")))
    jupiter = FakeJupiter(price=Decimal("1"))
    other = "0x0000000000000000000000000000000000000def"

    barrier = threading.Barrier(2)

    def buy(token: str, key: str) -> str:
        with maker() as session:
            barrier.wait(timeout=10)
            order = place(
                session,
                settings,
                dex,
                jupiter,
                make_request(side="buy", amount_usd="80", key=key, token=token),
            )
            return order.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda pair: buy(*pair), [(TOKEN, "cash-a"), (other, "cash-b")]))

    assert sorted(statuses) == ["filled", "rejected"], statuses
    with maker() as session:
        account = paper.account_snapshot(session)
        assert Decimal(account["cash_usd"]) >= 0
    engine.dispose()


def test_stale_oversized_sell_is_position_changed(tmp_path, monkeypatch) -> None:
    from dataclasses import replace

    from ezchain_core.ledger import position_account

    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'paper-stale-quote.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    settings = make_settings()
    dex = FakeDexScreener(make_snapshot(price=Decimal("2000"), liquidity=Decimal("500000")))
    jupiter = FakeJupiter(price=Decimal("2000"))

    with maker() as session:
        assert (
            place(
                session,
                settings,
                dex,
                jupiter,
                make_request(side="buy", amount_usd="100", key="stale-open"),
            ).status
            == "filled"
        )

    real_replay = paper._replay
    calls = 0
    account = position_account(CHAIN, TOKEN)

    def replay_two_views(rows, **kwargs):
        nonlocal calls
        calls += 1
        result = real_replay(rows, **kwargs)
        if calls != 2:
            return result
        quantity, cost = result.positions[account]
        return replace(
            result,
            positions={**result.positions, account: (quantity / 2, cost / 2)},
        )

    monkeypatch.setattr(paper, "_replay", replay_two_views)
    with maker() as session:
        order = place(
            session,
            settings,
            dex,
            jupiter,
            make_request(side="sell", amount_usd="1000", key="stale-sell"),
        )
        assert order.status == "rejected"
        assert order.reason == "POSITION_CHANGED"
    engine.dispose()
