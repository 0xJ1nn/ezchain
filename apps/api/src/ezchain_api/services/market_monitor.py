"""Observation-driven all-chain market monitoring ("market eyes").

ezCHAIN must know what it can see, not only when a user opens a token. One bounded scan
discoveries every registered chain with a single ``chain=all`` adapter call, then makes a
per-chain attempt only for the chains that pass did not cover. The rate-limit budget is
therefore proportional to observed gaps, not to the registry size.

Persisted observations are the source of truth for the overview: row counts and ages
come from ``market_snapshots``, so the endpoint still works after a restart. Attempt times
and errors are process-local because they describe this process's latest scan. A chain is
``ok`` only when it has a fresh persisted row; unknown is never reported as zero.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS
from ezchain_core import MarketRow, SourceRef, TokenSnapshot, utcnow

from ..config import Settings
from ..models import MarketSnapshotRow
from . import paper

logger = logging.getLogger(__name__)

CHAIN_STATUSES = ("ok", "stale", "no_data", "error", "scanning")
MIN_SCAN_INTERVAL_SECONDS = 30.0
MIN_STALE_AFTER_SECONDS = 60.0
MAX_ERROR_TEXT = 240


@runtime_checkable
class MarketSource(Protocol):
    """The discovery call the monitor needs; ``DexScreenerClient`` satisfies it."""

    async def list_markets(
        self, *, chain: str | None = None, query: str | None = None, limit: int = 50
    ) -> tuple[list[MarketRow], SourceRef]: ...


@dataclass
class ChainState:
    """Process-local view of one chain's latest scan attempt."""

    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    rows: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class ScanResult:
    """What one scan attempted and persisted."""

    observed_at: datetime
    attempted: tuple[str, ...]
    persisted: int
    rows_by_chain: dict[str, int]
    errors: dict[str, str]


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _aware(value).isoformat()


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:MAX_ERROR_TEXT]


def stale_after_seconds(settings: Settings) -> float:
    """A chain is stale after two scan intervals, never tighter than the quote mark age."""
    return float(max(settings.max_quote_age_seconds, settings.market_scan_interval_seconds * 2))


def _snapshot(row: MarketRow) -> TokenSnapshot:
    """Project a pair row onto the token-level snapshot the table stores.

    The API owns no pair/dex/url columns, so only the snapshot-facing fields are carried
    over. A row without a base token cannot be keyed and is skipped by the caller.
    """
    return TokenSnapshot(
        chain=row.chain,
        address=row.base_token,
        symbol=row.symbol,
        name=row.name or row.symbol,
        price_usd=row.price_usd,
        liquidity_usd=row.liquidity_usd,
        volume_24h_usd=row.volume_24h_usd,
        price_change_m5=row.price_change_m5,
        price_change_h1=row.price_change_h1,
        price_change_h6=row.price_change_h6,
        price_change_h24=row.price_change_h24,
        pairs=(),
        source=row.source,
        stale=False,
    )


def _best_rows(rows: list[MarketRow]) -> list[MarketRow]:
    """Keep the deepest pair per token so a shallow pool cannot overwrite the best mark."""
    best: dict[str, MarketRow] = {}
    for row in rows:
        if not row.base_token:
            logger.debug("skipping market row without base token: %s", row.pair_address)
            continue
        current = best.get(row.base_token)
        if current is None or (
            row.liquidity_usd,
            row.volume_24h_usd,
        ) > (
            current.liquidity_usd,
            current.volume_24h_usd,
        ):
            best[row.base_token] = row
    return list(best.values())


class MarketMonitor:
    """Bounded all-chain observation loop with a graceful stop.

    One scan is sequential on purpose: the adapter already paces requests, and a single
    failing chain or source must never abort the remaining work.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        source: MarketSource,
        settings: Settings,
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._interval_seconds = max(
            MIN_SCAN_INTERVAL_SECONDS, float(settings.market_scan_interval_seconds)
        )
        self._limit = settings.market_scan_limit
        self._now = now
        self._stop = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        self._states: dict[str, ChainState] = {chain.id: ChainState() for chain in CHAINS}
        self._scanning = False

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def scanning(self) -> bool:
        return self._scanning

    def state(self, chain_id: str) -> ChainState | None:
        return self._states.get(chain_id)

    async def run_once(self) -> ScanResult:
        """Run one scan. The lock keeps a manual scan from overlapping the loop."""
        async with self._scan_lock:
            self._scanning = True
            try:
                return await self._scan()
            finally:
                self._scanning = False

    async def _scan(self) -> ScanResult:
        started = self._now()
        discovered: dict[str, list[MarketRow]] = {}
        rows_by_chain: dict[str, int] = {}
        errors: dict[str, str] = {}
        attempted: list[str] = []
        persisted = 0

        # The all-chain discovery covers every registered chain by definition. The attempt
        # stamp is set once the call returns so a first scan reports `scanning`, not
        # `no_data`, while discovery is still in flight.
        try:
            rows, _source = await self._source.list_markets(chain="all", limit=self._limit)
        except Exception as exc:  # one provider failure must not stop the scan
            logger.warning("market scan all-chain discovery failed: %s", _error_text(exc))
        else:
            for row in rows:
                if row.chain in self._states:
                    discovered.setdefault(row.chain, []).append(row)

        for chain in CHAINS:
            self._states[chain.id].last_attempt_at = started

        for chain_id in sorted(discovered):
            attempted.append(chain_id)
            count = self._persist(chain_id, discovered[chain_id], started, errors)
            persisted += count
            rows_by_chain[chain_id] = count

        for chain in CHAINS:
            if chain.id in discovered:
                continue
            attempted.append(chain.id)
            self._states[chain.id].last_attempt_at = self._now()
            try:
                rows, _source = await self._source.list_markets(
                    chain=chain.id, limit=min(self._limit, 100)
                )
            except Exception as exc:
                message = _error_text(exc)
                state = self._states[chain.id]
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                logger.warning("market scan %s failed: %s", chain.id, message)
                continue
            chain_rows = [row for row in rows if row.chain == chain.id]
            if not chain_rows:
                state = self._states[chain.id]
                state.rows = 0
                state.last_success_at = self._now()
                state.last_error = None
                rows_by_chain[chain.id] = 0
                continue
            count = self._persist(chain.id, chain_rows, self._now(), errors)
            persisted += count
            rows_by_chain[chain.id] = count

        return ScanResult(
            observed_at=started,
            attempted=tuple(attempted),
            persisted=persisted,
            rows_by_chain=rows_by_chain,
            errors=dict(errors),
        )

    def _persist(
        self,
        chain_id: str,
        rows: list[MarketRow],
        observed_at: datetime,
        errors: dict[str, str],
    ) -> int:
        best = _best_rows(rows)
        state = self._states[chain_id]
        try:
            with self._session_factory() as session:
                for row in best:
                    paper.record_snapshot(session, _snapshot(row))
                session.commit()
        except Exception as exc:
            message = _error_text(exc)
            state.rows = 0
            state.last_error = message
            errors[chain_id] = message
            logger.warning("market scan %s persist failed: %s", chain_id, message)
            return 0
        state.rows = len(best)
        state.last_success_at = observed_at
        state.last_error = None
        return len(best)

    async def run_forever(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("market scan pass failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue

    def stop(self) -> None:
        """Idempotent stop request; the loop exits after the current scan."""
        self._stop.set()


def build_overview(
    session: Session,
    monitor: MarketMonitor | None,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per-chain observation status from persisted rows plus this process's scan state."""
    current = _aware(now or utcnow())
    counts: dict[str, tuple[int, datetime | None]] = {}
    observed = session.execute(
        select(
            MarketSnapshotRow.chain,
            func.count(MarketSnapshotRow.id),
            func.max(MarketSnapshotRow.observed_at),
        ).group_by(MarketSnapshotRow.chain)
    ).all()
    for chain_id, row_count, last_observed_at in observed:
        counts[str(chain_id)] = (
            int(row_count),
            None if last_observed_at is None else _aware(last_observed_at),
        )

    stale_after = stale_after_seconds(settings)
    chains: list[dict[str, Any]] = []
    totals: dict[str, int] = {
        "chains": len(CHAINS),
        "covered": 0,
        "stale": 0,
        "no_data": 0,
        "error": 0,
        "snapshots": 0,
    }

    for meta in CHAINS:
        rows, last_observed = counts.get(meta.id, (0, None))
        state = None if monitor is None else monitor.state(meta.id)
        last_attempt = None if state is None else state.last_attempt_at
        last_error = None if state is None else state.last_error
        age_seconds = (
            None
            if last_observed is None
            else int(max(0.0, (current - last_observed).total_seconds()))
        )

        if last_error is not None:
            status = "error"
        elif rows == 0:
            status = "scanning" if monitor is not None and last_attempt is None else "no_data"
        elif age_seconds is not None and age_seconds > stale_after:
            status = "stale"
        else:
            status = "ok"

        totals["snapshots"] += rows
        if rows > 0:
            totals["covered"] += 1
        if status in ("stale", "no_data", "error"):
            totals[status] += 1

        chains.append(
            {
                "id": meta.id,
                "name": meta.name,
                "family": meta.family,
                "quote_kind": meta.quote_kind,
                "status": status,
                "rows": rows,
                "last_observed_at": _iso(last_observed),
                "age_seconds": age_seconds,
                "last_attempt_at": _iso(last_attempt),
                "last_error": last_error,
            }
        )

    return {"as_of": current.isoformat(), "chains": chains, "totals": totals}


def stored_snapshots(
    session: Session, *, chain: str = "all", limit: int = 50
) -> list[MarketSnapshotRow]:
    """Latest persisted observation per token, deepest liquidity first, nulls last."""
    statement = select(MarketSnapshotRow)
    if chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == chain)
    statement = statement.order_by(
        MarketSnapshotRow.liquidity_usd.is_(None),
        MarketSnapshotRow.liquidity_usd.desc(),
        MarketSnapshotRow.observed_at.desc(),
    ).limit(limit)
    return list(session.scalars(statement))


__all__ = [
    "CHAIN_STATUSES",
    "ChainState",
    "MarketMonitor",
    "MarketSource",
    "ScanResult",
    "build_overview",
    "stale_after_seconds",
    "stored_snapshots",
]
