"""Observation-driven all-chain market monitoring ("market eyes").

ezCHAIN must know what it can see, not only when a user opens a token. One bounded scan
discoveries every registered chain with a single ``chain=all`` adapter call. The adapter's
coverage tells this process which chains were attempted, so a second per-chain pass runs
only when that coverage is unavailable; one provider failure never aborts the scan.

Persisted observations are the source of truth for the overview: row counts and ages
come from ``market_snapshots``, so the endpoint still works after a restart. Attempt times
and errors are process-local because they describe this process's latest scan. A chain is
``ok`` only when it has a fresh persisted row; unknown is never reported as zero.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS, ChainCoverage, SortMode
from ezchain_core import MarketRow, SourceRef, TokenSnapshot, utcnow

from ..config import Settings
from ..models import MarketSnapshotRow
from . import paper

logger = logging.getLogger(__name__)

CHAIN_STATUSES = ("ok", "stale", "no_data", "error", "scanning")
MIN_SCAN_INTERVAL_SECONDS = 30.0
STALE_MARGIN_SECONDS = 15.0
MAX_ERROR_TEXT = 240


@runtime_checkable
class MarketSource(Protocol):
    """Discovery the monitor needs; ``DexScreenerClient`` satisfies it."""

    async def discover_markets(
        self,
        *,
        chain: str | None = None,
        query: str | None = None,
        sort: SortMode = "trending",
        limit: int = 50,
    ) -> tuple[list[MarketRow], SourceRef, list[ChainCoverage] | None]: ...


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
    positions_refreshed: int = 0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _aware(value).isoformat()


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return text[:MAX_ERROR_TEXT]


def stale_after_seconds(settings: Settings, last_scan_duration_seconds: float = 0.0) -> float:
    """Staleness covers one interval plus the last scan's duration and a margin.

    ``run_forever`` sleeps the interval *after* a scan, so a row can legitimately be
    ``interval + scan_duration + margin`` old before the next pass observes it.
    """
    interval = float(settings.market_scan_interval_seconds)
    return float(
        max(
            60.0,
            interval + max(0.0, last_scan_duration_seconds) + STALE_MARGIN_SECONDS,
        )
    )


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
        # ``market_snapshots`` does not persist pair age. The order path re-fetches a live
        # token snapshot and applies the gate there; do not imply this value is stored.
        pair_created_at=None,
    )


def _depth(row: MarketRow) -> tuple[bool, Decimal, bool, Decimal]:
    """Deepest first; unknown liquidity/volume sorts last without changing the row."""
    return (
        row.liquidity_usd is not None,
        row.liquidity_usd or Decimal(0),
        row.volume_24h_usd is not None,
        row.volume_24h_usd or Decimal(0),
    )


def _best_rows(rows: list[MarketRow]) -> list[MarketRow]:
    """Keep the deepest pair per token so a shallow pool cannot overwrite the best mark."""
    best: dict[str, MarketRow] = {}
    for row in rows:
        if not row.base_token:
            logger.debug("skipping market row without base token: %s", row.pair_address)
            continue
        current = best.get(row.base_token)
        if current is None or _depth(row) > _depth(current):
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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._interval_seconds = max(
            MIN_SCAN_INTERVAL_SECONDS, float(settings.market_scan_interval_seconds)
        )
        self._limit = settings.market_scan_limit
        self._max_positions = settings.max_open_positions
        self._now = now
        self._monotonic = monotonic
        self._stop = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        self._states: dict[str, ChainState] = {chain.id: ChainState() for chain in CHAINS}
        self._scanning = False
        self._last_scan_duration_seconds = 0.0

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def last_scan_duration_seconds(self) -> float:
        return self._last_scan_duration_seconds

    def state(self, chain_id: str) -> ChainState | None:
        return self._states.get(chain_id)

    async def run_once(self) -> ScanResult:
        """Run one scan. The lock keeps a manual scan from overlapping the loop."""
        async with self._scan_lock:
            self._scanning = True
            started = self._monotonic()
            try:
                return await self._scan()
            finally:
                self._last_scan_duration_seconds = max(0.0, self._monotonic() - started)
                self._scanning = False

    async def _scan(self) -> ScanResult:
        started = self._now()
        discovered: dict[str, list[MarketRow]] = {}
        rows_by_chain: dict[str, int] = {}
        errors: dict[str, str] = {}
        attempted: list[str] = []
        persisted = 0

        coverage: list[ChainCoverage] | None = None
        all_chain_failed = False
        try:
            rows, _source, coverage = await self._source.discover_markets(
                chain="all", sort="trending", limit=self._limit
            )
        except Exception as exc:  # one provider failure must not stop the scan
            all_chain_failed = True
            logger.warning("market scan all-chain discovery failed: %s", _error_text(exc))
        else:
            for row in rows:
                if row.chain in self._states:
                    discovered.setdefault(row.chain, []).append(row)

        if coverage is not None and not all_chain_failed:
            persisted = self._use_all_chain_coverage(
                coverage, discovered, started, attempted, rows_by_chain, errors
            )
        else:
            # The all-chain pass raised or returned no coverage: this process cannot know
            # which chains were attempted, so probe each one once, never more.
            persisted = await self._probe_chains(attempted, rows_by_chain, errors)

        refreshed = await self._refresh_positions()
        return ScanResult(
            observed_at=started,
            attempted=tuple(attempted),
            persisted=persisted,
            rows_by_chain=rows_by_chain,
            errors=dict(errors),
            positions_refreshed=refreshed,
        )

    async def _refresh_positions(self) -> int:
        """Refresh marks for open paper positions, including quote-side tokens.

        The market fan-out stores base tokens. A position can be the quote side of its
        deepest pair, so it needs its own token lookup. Bounded by the open-position
        limit; one failing token never stops the scan.
        """
        getter = getattr(self._source, "get_token", None)
        if getter is None:
            return 0
        with self._session_factory() as session:
            views = paper.positions(session)
        refreshed = 0
        for view in views[: self._max_positions]:
            chain = str(view["chain"])
            token = str(view["token_address"])
            try:
                snapshot = await getter(chain, token)
            except Exception as exc:
                logger.warning("mark refresh %s:%s failed: %s", chain, token, _error_text(exc))
                continue
            with self._session_factory() as session:
                paper.record_snapshot(session, snapshot)
                session.commit()
            refreshed += 1
        return refreshed

    def _use_all_chain_coverage(
        self,
        coverage: list[ChainCoverage],
        discovered: dict[str, list[MarketRow]],
        started: datetime,
        attempted: list[str],
        rows_by_chain: dict[str, int],
        errors: dict[str, str],
    ) -> int:
        """Apply one fan-out result without any per-chain re-probe."""
        persisted = 0
        failed_chains: set[str] = set()
        for item in coverage:
            state = self._states.get(item.chain)
            if state is None:
                continue
            attempted.append(item.chain)
            state.last_attempt_at = started
            if item.reason == "error":
                message = "all-chain probe failed"
                failed_chains.add(item.chain)
                state.rows = 0
                state.last_error = message
                errors[item.chain] = message
            elif item.reason == "no_pairs":
                state.rows = 0
                state.last_success_at = started
                state.last_error = None
            else:
                state.last_error = None

        for chain_id in sorted(discovered):
            if chain_id in failed_chains:
                rows_by_chain[chain_id] = 0
                continue
            count = self._persist(chain_id, discovered[chain_id], started, errors)
            persisted += count
            rows_by_chain[chain_id] = count

        for item in coverage:
            if item.chain in rows_by_chain:
                continue
            rows_by_chain[item.chain] = 0
            if item.reason == "pairs":
                state = self._states.get(item.chain)
                if state is not None:
                    state.last_success_at = started
                    state.last_error = None
        return persisted

    async def _probe_chains(
        self,
        attempted: list[str],
        rows_by_chain: dict[str, int],
        errors: dict[str, str],
    ) -> int:
        """Bounded fallback: at most one probe per registered chain."""
        persisted = 0
        for chain in CHAINS:
            attempted.append(chain.id)
            state = self._states[chain.id]
            state.last_attempt_at = self._now()
            try:
                rows, _source, coverage = await self._source.discover_markets(
                    chain=chain.id, sort="trending", limit=min(self._limit, 100)
                )
            except Exception as exc:
                message = _error_text(exc)
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                logger.warning("market scan %s failed: %s", chain.id, message)
                continue
            failed = any(
                item.chain == chain.id and item.reason == "error" for item in coverage or []
            )
            chain_rows = [row for row in rows if row.chain == chain.id]
            if failed:
                message = "chain probe failed"
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                continue
            if not chain_rows:
                state.rows = 0
                state.last_success_at = self._now()
                state.last_error = None
                rows_by_chain[chain.id] = 0
                continue
            count = self._persist(chain.id, chain_rows, self._now(), errors)
            persisted += count
            rows_by_chain[chain.id] = count
        return persisted

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

    stale_after = stale_after_seconds(
        settings, 0.0 if monitor is None else monitor.last_scan_duration_seconds
    )
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
        # covered and error are disjoint: a chain with persisted rows is covered even if
        # its latest attempt failed; error counts only failed attempts with no rows.
        if status == "stale":
            totals["stale"] += 1
        elif status == "no_data":
            totals["no_data"] += 1
        elif status == "error" and rows == 0:
            totals["error"] += 1

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
    session: Session, *, chain: str = "all", limit: int = 50, offset: int = 0
) -> list[MarketSnapshotRow]:
    """Latest persisted observation per token, deepest liquidity first, nulls last."""
    statement = select(MarketSnapshotRow)
    if chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == chain)
    statement = (
        statement.order_by(
            MarketSnapshotRow.liquidity_usd.desc().nulls_last(),
            MarketSnapshotRow.observed_at.desc(),
            MarketSnapshotRow.token_address.asc(),
        )
        .offset(max(0, offset))
        .limit(limit)
    )
    return list(session.scalars(statement))


def count_stored_snapshots(session: Session, *, chain: str = "all") -> int:
    """Total persisted observations for one chain (or every chain)."""
    statement = select(func.count(MarketSnapshotRow.id))
    if chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == chain)
    return int(session.scalar(statement) or 0)


__all__ = [
    "CHAIN_STATUSES",
    "ChainState",
    "MarketMonitor",
    "MarketSource",
    "ScanResult",
    "build_overview",
    "count_stored_snapshots",
    "stale_after_seconds",
    "stored_snapshots",
]
