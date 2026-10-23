"""Map supervised plugin intents onto the engine's single decision contract."""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import TOKEN, make_snapshot

from ezchain_api.models import StrategyRow
from ezchain_engine import PluginHost, PluginRegistry, evaluate_plugin_strategy
from ezchain_sdk import Candle, PluginContext

MANIFEST = {
    "id": "com.example.runtime",
    "name": "Runtime plugin",
    "version": "1.0.0",
    "sdk": ">=1,<2",
    "entrypoint": "runtime_plugin:Plugin",
    "permissions": ["read_market", "submit_intent"],
    "params_schema": {
        "type": "object",
        "properties": {"entry_pct": {"type": "number", "default": 1}},
        "additionalProperties": False,
    },
    "timeframes": ["1h"],
    "primary_timeframe": "1h",
}


def _registry(tmp_path: Path, body: str) -> PluginRegistry:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "ezchain.plugin.json").write_text(json.dumps(MANIFEST))
    (root / "runtime_plugin.py").write_text(textwrap.dedent(body))
    return PluginRegistry(paths=[tmp_path], include_entry_points=False)


class Runtime:
    def __init__(self, now: datetime) -> None:
        self.now = now

    async def build_context(self, session, strategy, *, snapshot, now):  # type: ignore[no-untyped-def]
        bar = Candle(
            open_time=now,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
        )
        return PluginContext(
            now=now,
            mode="paper",
            params={"entry_pct": 1},
            state={},
            candles={"1h": (bar,)},
        )


def _strategy(session) -> StrategyRow:  # type: ignore[no-untyped-def]
    row = StrategyRow(
        name="plugin",
        chain="solana",
        token_address=TOKEN,
        token_symbol="TEST",
        kind="plugin",
        plugin_id="com.example.runtime",
        plugin_version="1.0.0",
        params={
            "interval_minutes": 60,
            "max_position_usd": 500,
            "config": {"entry_pct": 1},
        },
        status="active",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.mark.asyncio
async def test_a_plugin_buy_intent_becomes_a_buy_decision(
    session_factory, tmp_path: Path
) -> None:
    registry = _registry(
        tmp_path,
        """
        from decimal import Decimal
        from ezchain_sdk import Intent, Strategy

        class Plugin(Strategy):
            def on_candle(self, ctx, candle):
                ctx.state["seen"] = ctx.state.get("seen", 0) + 1
                return [Intent(side="buy", notional_usd=Decimal("25"), reason="PLUGIN_ENTRY")]
        """,
    )
    host = PluginHost(timeout_seconds=3)
    try:
        with session_factory() as session:
            strategy = _strategy(session)
            decision = await evaluate_plugin_strategy(
                session,
                strategy,
                make_snapshot(),
                Runtime(datetime(2026, 10, 23, tzinfo=UTC)),
                host,
                registry,
                now=datetime(2026, 10, 23, tzinfo=UTC),
            )
        assert decision.action == "buy"
        assert decision.amount_usd == Decimal("25")
        assert decision.reason == "PLUGIN_ENTRY"
        assert decision.inputs["plugin_id"] == "com.example.runtime"
        assert decision.inputs["plugin_state"] == {"seen": 1}
        assert decision.inputs["intent"]["side"] == "buy"
    finally:
        await host.close_all()


@pytest.mark.asyncio
async def test_reserved_intent_fields_fail_closed(session_factory, tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        """
        from decimal import Decimal
        from ezchain_sdk import Intent, Strategy

        class Plugin(Strategy):
            def on_candle(self, ctx, candle):
                return [
                    Intent(side="buy", notional_usd=Decimal("25"), reason="LIMIT", limit_price=Decimal("1"))
                ]
        """,
    )
    host = PluginHost(timeout_seconds=3)
    try:
        with session_factory() as session:
            strategy = _strategy(session)
            decision = await evaluate_plugin_strategy(
                session,
                strategy,
                make_snapshot(),
                Runtime(datetime(2026, 10, 23, tzinfo=UTC)),
                host,
                registry,
                now=datetime(2026, 10, 23, tzinfo=UTC),
            )
        assert decision.action == "hold"
        assert decision.reason == "PLUGIN_UNSUPPORTED_INTENT"
    finally:
        await host.close_all()


@pytest.mark.asyncio
async def test_multiple_intents_are_refused(session_factory, tmp_path: Path) -> None:
    registry = _registry(
        tmp_path,
        """
        from decimal import Decimal
        from ezchain_sdk import Intent, Strategy

        class Plugin(Strategy):
            def on_candle(self, ctx, candle):
                return [
                    Intent(side="buy", notional_usd=Decimal("10"), reason="A"),
                    Intent(side="buy", notional_usd=Decimal("10"), reason="B"),
                ]
        """,
    )
    host = PluginHost(timeout_seconds=3)
    try:
        with session_factory() as session:
            strategy = _strategy(session)
            decision = await evaluate_plugin_strategy(
                session,
                strategy,
                make_snapshot(),
                Runtime(datetime(2026, 10, 23, tzinfo=UTC)),
                host,
                registry,
                now=datetime(2026, 10, 23, tzinfo=UTC),
            )
        assert decision.action == "hold"
        assert decision.reason == "PLUGIN_MULTIPLE_INTENTS"
    finally:
        await host.close_all()
