"""Every strategy buy is sized by the persisted policy in the one order path.

The regime here is the same SQLite paper account the production executor uses: a real
``PaperOrderExecutor``, the real engine decision loop and a mark-only venue. A strategy
amount above the effective profile maximum is a persisted ``SIZE_ABOVE_MAX`` rejection,
not a plugin clamp or a silent resize; an amount inside the cap fills and the run log
carries the same replayable sizing snapshot as the order.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from helpers import make_snapshot
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ezchain_adapters import QuoteRouter
from ezchain_api.config import Settings
from ezchain_api.models import PaperOrderRow, StrategyRow, StrategyRunRow
from ezchain_api.services import trade_policy
from ezchain_api.services.executor import PaperOrderExecutor
from ezchain_core import TradeSizeLimits
from ezchain_core.types import PairInfo, TokenSnapshot
from ezchain_engine import evaluate_strategy

# A mark-only chain: the paper router turns this snapshot into a labelled paper_mark,
# so the test proves policy sizing rather than a venue integration.
CHAIN = "blast"
TOKEN = "0x0000000000000000000000000000000000000abc"


class StaticDex:
    def __init__(self) -> None:
        self.calls = 0
        self.snapshot = replace(
            make_snapshot(),
            chain=CHAIN,
            address=TOKEN,
            pairs=(
                PairInfo(
                    "0xpair",
                    "uniswap",
                    "USDC",
                    Decimal("1"),
                    Decimal("100000"),
                    Decimal("1000000"),
                    None,
                ),
            ),
        )

    async def get_token(self, chain: str, address: str) -> TokenSnapshot:
        self.calls += 1
        return self.snapshot


def _executor(
    session_factory: sessionmaker[Session], settings: Settings, source: StaticDex
) -> PaperOrderExecutor:
    return PaperOrderExecutor(
        session_factory,
        settings,
        source,  # type: ignore[arg-type]
        QuoteRouter(providers=[], dexscreener=source),  # type: ignore[arg-type]
    )


async def _run(
    session_factory: sessionmaker[Session],
    strategy_id: str,
    source: StaticDex,
    executor: PaperOrderExecutor,
    now: datetime,
):
    with session_factory() as session:
        strategy = session.get(StrategyRow, strategy_id)
        assert strategy is not None
        return await evaluate_strategy(session, strategy, source, executor, now=now)


def _order(session_factory: sessionmaker[Session]) -> PaperOrderRow:
    with session_factory() as session:
        row = session.scalar(select(PaperOrderRow).order_by(PaperOrderRow.created_at.desc()))
        assert row is not None
        return row


def _runs(session_factory: sessionmaker[Session], strategy_id: str) -> list[StrategyRunRow]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(StrategyRunRow)
                .where(StrategyRunRow.strategy_id == strategy_id)
                .order_by(StrategyRunRow.created_at)
            )
        )


async def test_a_strategy_buy_above_the_profile_cap_is_rejected_not_clamped(
    session_factory: sessionmaker[Session], add_strategy
) -> None:
    settings = Settings(environment="test", database_url="sqlite+pysqlite://", data_mode="fixture")
    strategy_id = add_strategy(
        params={"order_usd": 500, "max_position_usd": 1000, "interval_minutes": 60},
        chain=CHAIN,
        token_address=TOKEN,
    )
    source = StaticDex()
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    result = await _run(
        session_factory, strategy_id, source, _executor(session_factory, settings, source), now
    )

    assert result.action == "hold"
    assert result.reason == "ORDER_REJECTED"
    assert result.order_id is not None
    assert result.inputs["attempted_amount_usd"] == "500"
    assert result.inputs["order_reason"] == "SIZE_ABOVE_MAX"
    snapshot = result.inputs["size_resolution"]
    assert snapshot["policy_applies"] is True
    assert snapshot["source"] == "strategy"
    assert snapshot["policy_source"] == "default"
    assert snapshot["profile"] == "mid"
    assert snapshot["requested_amount_usd"] == "500"
    assert snapshot["resolved_amount_usd"] is None
    assert snapshot["reason"] == "SIZE_ABOVE_MAX"
    assert snapshot["limits"]["max_amount_usd"] == "150"

    order = _order(session_factory)
    assert order.status == "rejected"
    assert order.reason == "SIZE_ABOVE_MAX"
    assert order.amount_usd == Decimal("500")
    assert order.strategy_id == strategy_id
    assert order.risk_profile == "mid"
    assert order.size_resolution == snapshot
    # One snapshot for the decision itself; the refused size never reaches the quote.
    assert source.calls == 1

    runs = _runs(session_factory, strategy_id)
    assert len(runs) == 1
    assert runs[0].reason == "ORDER_REJECTED"
    assert runs[0].size_resolution == snapshot
    assert runs[0].inputs["size_resolution"] == snapshot


async def test_a_strategy_buy_inside_the_cap_fills_and_persists_profile_and_source(
    session_factory: sessionmaker[Session], add_strategy
) -> None:
    settings = Settings(environment="test", database_url="sqlite+pysqlite://", data_mode="fixture")
    record = None
    with session_factory() as session:
        record = trade_policy.load_trade_policy(session, settings)
        policy = replace(
            record.policy,
            profiles={
                **record.policy.profiles,
                "mid": TradeSizeLimits(
                    min_amount_usd=Decimal("10"),
                    min_pct_equity=Decimal("0.10"),
                    max_amount_usd=Decimal("200"),
                    max_pct_equity=Decimal("2"),
                ),
            },
        )
        trade_policy.save_trade_policy(session, policy, updated_by="test")
    strategy_id = add_strategy(
        params={"order_usd": 200, "max_position_usd": 1000, "interval_minutes": 60},
        chain=CHAIN,
        token_address=TOKEN,
    )
    source = StaticDex()

    result = await _run(
        session_factory,
        strategy_id,
        source,
        _executor(session_factory, settings, source),
        datetime(2026, 10, 22, 12, 0, tzinfo=UTC),
    )

    assert result.action == "buy"
    assert result.reason == "MOMENTUM_ENTRY"
    assert result.order_id is not None
    snapshot = result.inputs["size_resolution"]
    assert snapshot["source"] == "strategy"
    assert snapshot["policy_source"] == "persisted"
    assert snapshot["profile"] == "mid"
    assert snapshot["requested_amount_usd"] == "200"
    assert snapshot["resolved_amount_usd"] == "200"
    assert snapshot["reason"] is None
    assert snapshot["limits"]["max_amount_usd"] == "200"

    order = _order(session_factory)
    assert order.status == "filled"
    assert order.amount_usd == Decimal("200")
    assert order.risk_profile == "mid"
    assert order.strategy_id == strategy_id
    assert order.size_resolution == snapshot

    runs = _runs(session_factory, strategy_id)
    assert len(runs) == 1
    assert runs[0].action == "buy"
    assert runs[0].order_id == order.id
    assert runs[0].size_resolution == snapshot
    assert runs[0].inputs["size_resolution"] == snapshot
