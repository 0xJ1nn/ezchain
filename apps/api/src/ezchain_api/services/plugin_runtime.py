"""Assemble the manifest-declared context for one supervised plugin tick.

The plugin never sees the database, the network or a provider client. It sees a frozen
snapshot, closed bars for the timeframes its manifest declared, its own allocation and,
only with ``read_account``, the paper account projection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ezchain_core.strategy import PluginParams, validate_params
from ezchain_core.types import TokenSnapshot
from ezchain_engine import PluginRegistry, latest_plugin_state, position_state
from ezchain_sdk import (
    Account,
    Allocation,
    PluginContext,
)
from ezchain_sdk import (
    Candle as PluginCandle,
)
from ezchain_sdk import (
    Position as PluginPosition,
)
from ezchain_sdk import (
    Snapshot as PluginSnapshot,
)
from ezchain_sdk import validate_params as validate_plugin_params

from ..config import Settings
from ..models import StrategyRow
from . import paper
from .candles import CandleSource, TokenSource, get_candles


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _snapshot(snapshot: TokenSnapshot) -> PluginSnapshot:
    source = snapshot.source
    return PluginSnapshot(
        chain=snapshot.chain,
        address=snapshot.address,
        symbol=snapshot.symbol,
        name=snapshot.name,
        price_usd=snapshot.price_usd,
        liquidity_usd=snapshot.liquidity_usd,
        volume_24h_usd=snapshot.volume_24h_usd,
        price_change_m5=snapshot.price_change_m5,
        price_change_h1=snapshot.price_change_h1,
        price_change_h6=snapshot.price_change_h6,
        price_change_h24=snapshot.price_change_h24,
        stale=snapshot.stale,
        observed_at=None if source is None else source.observed_at,
    )


class PaperPluginRuntime:
    """The engine's plugin context port, backed by candles and the paper ledger."""

    def __init__(
        self,
        candle_source: CandleSource,
        snapshot_source: TokenSource,
        settings: Settings,
        registry: PluginRegistry,
    ) -> None:
        self._candle_source = candle_source
        self._snapshot_source = snapshot_source
        self._settings = settings
        self._registry = registry

    async def build_context(
        self,
        session: Session,
        strategy: StrategyRow,
        *,
        snapshot: TokenSnapshot,
        now: datetime,
    ) -> PluginContext:
        if not strategy.plugin_id:
            raise ValueError("strategy has no plugin_id")
        record = self._registry.require(strategy.plugin_id)
        manifest = record.manifest
        params = validate_params("plugin", dict(strategy.params or {}))
        if not isinstance(params, PluginParams):  # pragma: no cover - kind is checked upstream
            raise ValueError("strategy is not a plugin")
        # Revalidate on every tick: an upgraded manifest must not silently reinterpret a
        # persisted configuration. A mismatch fails closed before the worker starts.
        plugin_params = validate_plugin_params(manifest.params_schema, params.config)

        timeframes = tuple(manifest.timeframes)
        if manifest.primary_timeframe and manifest.primary_timeframe not in timeframes:
            timeframes = (*timeframes, manifest.primary_timeframe)
        candles: dict[str, tuple[PluginCandle, ...]] = {}
        if "read_market" in manifest.permissions:
            for timeframe in timeframes:
                series = await get_candles(
                    session,
                    self._candle_source,
                    self._snapshot_source,
                    strategy.chain,
                    strategy.token_address,
                    timeframe,
                    manifest.context_bars,
                    now,
                    data_mode=self._settings.data_mode,
                )
                candles[timeframe] = tuple(
                    PluginCandle(
                        open_time=_aware(candle.open_time),
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                    )
                    for candle in series.candles
                )

        position = None
        account = None
        positions: tuple[PluginPosition, ...] = ()
        if "read_account" in manifest.permissions:
            state = position_state(
                session, strategy.chain, strategy.token_address, strategy.token_symbol
            )
            account_body = paper.account_snapshot(session)
            price = None if snapshot.stale else snapshot.price_usd
            value = None if state.quantity <= 0 or price is None else state.quantity * price
            position = PluginPosition(
                chain=strategy.chain,
                token_address=strategy.token_address,
                symbol=strategy.token_symbol,
                quantity=state.quantity,
                avg_cost_usd=state.avg_cost_usd,
                mark_usd=price,
                market_value_usd=value,
                unrealized_usd=(
                    None if value is None or state.cost_usd <= 0 else value - state.cost_usd
                ),
            )
            account = Account(
                cash_usd=Decimal(str(account_body.get("cash_usd", "0"))),
                equity_usd=Decimal(str(account_body.get("equity_usd", "0"))),
                realized_usd=Decimal(str(account_body.get("realized_usd", "0"))),
                unrealized_usd=Decimal(str(account_body.get("unrealized_usd", "0"))),
                open_positions=len(account_body.get("positions", [])),
            )
            positions = tuple(
                PluginPosition(
                    chain=str(item.get("chain", strategy.chain)),
                    token_address=str(item.get("token_address", "")),
                    symbol=str(item.get("symbol", "")),
                    quantity=Decimal(str(item.get("quantity", "0"))),
                    avg_cost_usd=_decimal_or_none(item.get("avg_cost_usd")),
                    mark_usd=_decimal_or_none(item.get("mark_usd")),
                    market_value_usd=_decimal_or_none(item.get("market_value_usd")),
                    unrealized_usd=_decimal_or_none(item.get("unrealized_usd")),
                )
                for item in account_body.get("positions", [])
            )

        return PluginContext(
            now=now,
            mode="paper",
            params=plugin_params,
            state=latest_plugin_state(session, strategy.id),
            snapshot=_snapshot(snapshot) if "read_market" in manifest.permissions else None,
            candles=candles,
            position=position,
            positions=positions,
            account=account,
            allocation=Allocation(
                max_notional_usd=params.max_position_usd,
                max_positions=self._settings.max_open_positions,
                max_daily_loss_usd=self._settings.max_daily_loss_usd,
            ),
        )


__all__ = ["PaperPluginRuntime"]
