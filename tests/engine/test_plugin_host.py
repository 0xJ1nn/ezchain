"""The supervised worker is the only place plugin code runs.

These tests build throwaway plugin packages in ``tmp_path``. No strategy code lives in the
repository; the fixture exists only for this test.
"""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from ezchain_engine import PluginHost, PluginRegistry
from ezchain_sdk import Candle, PluginContext

MANIFEST = {
    "id": "com.example.test",
    "name": "Test plugin",
    "version": "1.0.0",
    "sdk": ">=1,<2",
    "entrypoint": "demo_plugin:Demo",
    "permissions": ["read_market", "read_account", "submit_intent"],
    "params_schema": {
        "type": "object",
        "properties": {"entry_pct": {"type": "number", "default": 1}},
        "additionalProperties": False,
    },
    "timeframes": ["1h"],
    "primary_timeframe": "1h",
}


def _plugin(
    tmp_path: Path, source: str, manifest: dict[str, object] | None = None
) -> PluginRegistry:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "ezchain.plugin.json").write_text(json.dumps(manifest or MANIFEST))
    (root / "demo_plugin.py").write_text(textwrap.dedent(source))
    return PluginRegistry(paths=[tmp_path], include_entry_points=False)


def _context(state: dict[str, object] | None = None) -> PluginContext:
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    bar = Candle(
        open_time=now,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
    )
    return PluginContext(
        now=now,
        mode="paper",
        params={"entry_pct": 1},
        state=dict(state or {}),
        candles={"1h": (bar,)},
    )


@pytest.mark.asyncio
async def test_worker_returns_intents_and_replayable_state(tmp_path: Path) -> None:
    registry = _plugin(
        tmp_path,
        """
        from decimal import Decimal
        from ezchain_sdk import Intent, Strategy

        class Demo(Strategy):
            def on_start(self, ctx):
                ctx.state["ticks"] = 0

            def on_candle(self, ctx, candle):
                ctx.state["ticks"] += 1
                if ctx.state["ticks"] == 1:
                    return [Intent(side="buy", notional_usd=Decimal("10"), reason="ENTRY")]
                return []
        """,
    )
    record = registry.get("com.example.test")
    host = PluginHost(timeout_seconds=3)
    try:
        first = await host.tick(
            "strategy-1",
            record,
            _context(),
            event={"kind": "candle", "candle": _context().candles["1h"][0].to_dict()},
        )
        assert first.ok
        assert first.intents == (
            {
                "side": "buy",
                "notional_usd": "10",
                "reason": "ENTRY",
                "dedupe_key": None,
                "limit_price": None,
                "ttl_s": None,
                "size_pct": None,
            },
        )
        assert first.state == {"ticks": 1}

        second = await host.tick(
            "strategy-1",
            record,
            _context(first.state),
            event={"kind": "candle", "candle": _context().candles["1h"][0].to_dict()},
        )
        assert second.ok
        assert second.intents == ()
        assert second.state == {"ticks": 2}
    finally:
        await host.close_all()


@pytest.mark.asyncio
async def test_plugin_output_cannot_corrupt_the_protocol_and_env_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZCHAIN_API_TOKEN", "must-not-leak")
    registry = _plugin(
        tmp_path,
        """
        import os
        from ezchain_sdk import Strategy

        class Demo(Strategy):
            def on_candle(self, ctx, candle):
                print("this is not JSON")
                ctx.state["token"] = os.environ.get("EZCHAIN_API_TOKEN")
                return []
        """,
    )
    host = PluginHost(timeout_seconds=3)
    try:
        context = _context()
        tick = await host.tick(
            "strategy-2",
            registry.get("com.example.test"),
            context,
            event={"kind": "candle", "candle": context.candles["1h"][0].to_dict()},
        )
        assert tick.ok
        assert tick.state["token"] is None
    finally:
        await host.close_all()


@pytest.mark.asyncio
async def test_timeout_kills_the_worker_and_quarantines_after_three_failures(
    tmp_path: Path,
) -> None:
    registry = _plugin(
        tmp_path,
        """
        from ezchain_sdk import Strategy

        class Demo(Strategy):
            def on_candle(self, ctx, candle):
                while True:
                    pass
        """,
    )
    host = PluginHost(timeout_seconds=0.3)
    record = registry.get("com.example.test")
    try:
        for _ in range(3):
            tick = await host.tick("strategy-3", record, _context())
            assert not tick.ok
            assert tick.error_code == "PLUGIN_TIMEOUT"
        blocked = await host.tick("strategy-3", record, _context())
        assert blocked.error_code == "PLUGIN_QUARANTINED"
        host.release("strategy-3")
        assert "strategy-3" not in host.quarantined()
    finally:
        await host.close_all()
        assert host.quarantined() == {}


@pytest.mark.asyncio
async def test_plugin_exception_is_reported_without_a_traceback(tmp_path: Path) -> None:
    registry = _plugin(
        tmp_path,
        """
        from ezchain_sdk import Strategy

        class Demo(Strategy):
            def on_candle(self, ctx, candle):
                raise RuntimeError("boom")
        """,
    )
    host = PluginHost(timeout_seconds=3)
    try:
        context = _context()
        tick = await host.tick(
            "strategy-4",
            registry.get("com.example.test"),
            context,
            event={"kind": "candle", "candle": context.candles["1h"][0].to_dict()},
        )
        assert not tick.ok
        assert tick.error_code == "PLUGIN_ERROR"
        assert tick.error_message == "boom"
    finally:
        await host.close_all()


def test_registry_reports_bad_manifests_and_duplicate_ids(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "ezchain.plugin.json").write_text(json.dumps({**MANIFEST, "sdk": ">=2"}))
    (bad / "demo_plugin.py").write_text("")
    registry = PluginRegistry(paths=[tmp_path], include_entry_points=False)
    assert registry.list() == []
    assert [issue.code for issue in registry.issues] == ["PLUGIN_SDK_INCOMPATIBLE"]

    for name in ("other", "third"):
        duplicate = tmp_path / name
        duplicate.mkdir()
        (duplicate / "ezchain.plugin.json").write_text(json.dumps(MANIFEST))
        (duplicate / "demo_plugin.py").write_text("")
    registry = PluginRegistry(paths=[tmp_path], include_entry_points=False)
    assert len(registry.list()) == 1
    assert any(issue.code == "PLUGIN_DUPLICATE_ID" for issue in registry.issues)
