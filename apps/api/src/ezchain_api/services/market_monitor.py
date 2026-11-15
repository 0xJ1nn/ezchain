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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Numeric, and_, case, cast, func, not_, or_, select
from sqlalchemy.orm import Session

from ezchain_adapters import CHAINS, ChainCoverage, CircuitBreaker, SortMode, UpstreamError
from ezchain_core import MarketRow, SourceRef, TokenSnapshot, utcnow

from ..config import Settings
from ..models import MarketSnapshotRow
from . import market_snapshots, paper

logger = logging.getLogger(__name__)

CHAIN_STATUSES = ("ok", "stale", "no_data", "error", "scanning")
MIN_SCAN_INTERVAL_SECONDS = 30.0
STALE_MARGIN_SECONDS = 15.0
MAX_ERROR_TEXT = 240

# The stored list's ordering, and the one migration 0026 indexes. ``id`` is the tie-break:
# keyset paging needs a total order, and two rows of one scan share a timestamp.
STORED_ORDER = (
    MarketSnapshotRow.liquidity_usd.is_(None),
    MarketSnapshotRow.liquidity_usd.desc(),
    MarketSnapshotRow.observed_at.desc(),
    MarketSnapshotRow.id.desc(),
)


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


def _snapshot(row: MarketRow, max_age_seconds: float | None = None) -> TokenSnapshot:
    """Project a pair row onto the token-level snapshot the table stores.

    Pair identity is carried over at the snapshot boundary, with empty or ``unknown``
    sentinels normalised to ``None`` so a missing value stays unknown. A row without
    a base token cannot be keyed and is skipped by the caller. ``max_age_seconds`` makes
    the ``stale`` flag reflect the row's own observation age instead of a constant.
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
        stale=(
            False
            if max_age_seconds is None or row.source is None
            else row.source.is_stale(max_age_seconds)
        ),
        pair_created_at=row.pair_created_at,
        pair_address=row.pair_address or None,
        dex=None if row.dex in ("", "unknown") else row.dex,
        base_token_address=row.base_token or None,
        quote_token_address=row.quote_token or None,
        url=row.url or None,
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
        self._max_mark_age_seconds = float(settings.max_mark_age_seconds)
        self._now = now
        # Guard against monotonic clocks that are finite iterables in tests: once exhausted,
        # reuse the last observed value instead of raising StopIteration mid-scan.
        _last: dict[str, float] = {"value": 0.0}
        _seen = {"flag": False}

        def _safe_monotonic() -> float:
            try:
                value = monotonic()
            except StopIteration:
                return _last["value"] if _seen["flag"] else 0.0
            _last["value"] = value
            _seen["flag"] = True
            return value

        self._monotonic = _safe_monotonic
        self._stop = asyncio.Event()
        self._scan_lock = asyncio.Lock()
        self._states: dict[str, ChainState] = {chain.id: ChainState() for chain in CHAINS}
        self._scanning = False
        self._last_scan_duration_seconds = 0.0

        def _setting(*names: str, default: float | int) -> float | int:
            for name in names:
                value = getattr(settings, name, None)
                if value is not None:
                    return value  # type: ignore[no-any-return]
            return default

        self._discovery_deadline_seconds = float(
            _setting(
                "market_discovery_deadline_seconds",
                "market_discovery_timeout_seconds",
                default=10.0,
            )
        )
        self._failure_threshold = int(
            _setting(
                "market_provider_failure_threshold",
                "market_provider_max_failures",
                default=3,
            )
        )
        self._cooldown_seconds = float(
            _setting(
                "market_provider_cooldown_seconds",
                "market_circuit_breaker_cooldown_seconds",
                default=60.0,
            )
        )
        self._breaker = CircuitBreaker(
            failure_threshold=self._failure_threshold,
            cooldown_seconds=self._cooldown_seconds,
            clock=monotonic,
        )
        self._provider_name = "dexscreener"

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def last_scan_duration_seconds(self) -> float:
        return self._last_scan_duration_seconds

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def provider_name(self) -> str:
        """The discovery provider this monitor's breaker protects."""
        return self._provider_name

    def state(self, chain_id: str) -> ChainState | None:
        return self._states.get(chain_id)

    async def run_once(self) -> ScanResult:
        """Run one scan. The lock keeps a manual scan from overlapping the loop."""
        async with self._scan_lock:
            self._scanning = True
            started = self._monotonic()
            try:
                return await self._scan()
            except asyncio.CancelledError:
                # ``_scan`` takes the one half-open permit before probing and records an
                # outcome only when the probe completes. A cancelled scan (shutdown, a
                # superseded manual scan) is not a verdict on the provider, so release
                # the permit; otherwise it is held for ever and every later scan is
                # rejected as "circuit open" until the process restarts.
                self._breaker.reset_probe()
                raise
            finally:
                self._last_scan_duration_seconds = max(0.0, self._monotonic() - started)
                self._scanning = False

    async def _scan(self) -> ScanResult:
        started = self._now()
        self._current_deadline_end = self._monotonic() + self._discovery_deadline_seconds
        discovered: dict[str, list[MarketRow]] = {}
        rows_by_chain: dict[str, int] = {}
        errors: dict[str, str] = {}
        attempted: list[str] = []
        persisted = 0
        successful_chains: set[str] = set()

        coverage: list[ChainCoverage] | None = None
        all_chain_failed = False
        if self._breaker.is_open():
            all_chain_failed = True
            message = f"{self._provider_name}: circuit open"
            errors[self._provider_name] = message
            logger.warning("market scan skipped: %s", message)
        else:
            try:
                rows, _source, coverage = await asyncio.wait_for(
                    self._source.discover_markets(chain="all", sort="trending", limit=self._limit),
                    timeout=self._discovery_deadline_seconds,
                )
            except TimeoutError:
                all_chain_failed = True
                self._breaker.record_failure()
                message = f"{self._provider_name}: discovery deadline exceeded"
                errors[self._provider_name] = message
                logger.warning(
                    "market scan all-chain discovery timed out after %.3fs",
                    self._discovery_deadline_seconds,
                )
            except Exception as exc:  # one provider failure must not stop the scan
                all_chain_failed = True
                self._breaker.record_failure()
                message = f"{self._provider_name}: {_error_text(exc)}"
                errors[self._provider_name] = message
                logger.warning("market scan all-chain discovery failed: %s", _error_text(exc))
            else:
                self._breaker.record_success()
                for row in rows:
                    if row.chain in self._states:
                        discovered.setdefault(row.chain, []).append(row)

        if coverage is not None and not all_chain_failed:
            persisted = self._use_all_chain_coverage(
                coverage, discovered, started, attempted, rows_by_chain, errors
            )
            successful_chains.update(
                item.chain
                for item in coverage
                if item.reason not in ("error", "no_seeds", "timeout")
            )
        else:
            # The all-chain pass raised or returned no coverage: this process cannot know
            # which chains were attempted, so probe each one once, never more.
            persisted = await self._probe_chains(
                attempted, rows_by_chain, errors, self._current_deadline_end, successful_chains
            )

        remaining = max(0.0, self._current_deadline_end - self._monotonic())
        if remaining > 0:
            refreshed = await self._refresh_positions(remaining)
        else:
            refreshed = 0

        # A total failure must not masquerade as an empty successful scan.
        total_failure = persisted == 0 and bool(errors) and not successful_chains
        if total_failure:
            raise UpstreamError(
                "market discovery failed for all providers",
                detail="; ".join(f"{key}: {value}" for key, value in errors.items()),
            )

        return ScanResult(
            observed_at=started,
            attempted=tuple(attempted),
            persisted=persisted,
            rows_by_chain=rows_by_chain,
            errors=dict(errors),
            positions_refreshed=refreshed,
        )

    async def _refresh_positions(self, budget: float | None = None) -> int:
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
                if budget is not None:
                    remaining = max(0.0, self._current_deadline_end - self._monotonic())
                    if remaining <= 0:
                        break
                    snapshot = await asyncio.wait_for(
                        getter(
                            chain,
                            token,
                            max_age_seconds=self._max_mark_age_seconds,
                        ),
                        timeout=remaining,
                    )
                else:
                    snapshot = await getter(
                        chain, token, max_age_seconds=self._max_mark_age_seconds
                    )
            except Exception as exc:
                logger.warning("mark refresh %s:%s failed: %s", chain, token, _error_text(exc))
                continue
            with self._session_factory() as session:
                try:
                    stored = market_snapshots.record_snapshot(session, snapshot)
                    session.commit()
                except Exception as exc:
                    logger.warning("mark refresh %s:%s failed: %s", chain, token, _error_text(exc))
                    continue
            if stored is None:
                logger.warning(
                    "mark refresh %s:%s rejected with MARK_TIMESTAMP_FUTURE", chain, token
                )
                continue
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
            if item.reason == "no_seeds":
                # Nothing was asked of the provider: this chain has no observation, which
                # is not the same as an empty one. Do not record an attempt or a success.
                continue
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
            elif item.reason == "timeout":
                # A timeout is a failed observation, not an empty one. Keep its own reason
                # so the overview can tell a timed-out chain from a genuinely empty feed.
                message = "all-chain probe timed out"
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
            if item.reason == "no_seeds":
                continue
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
        deadline_end: float,
        successful_chains: set[str],
    ) -> int:
        """Bounded fallback: at most one probe per registered chain."""
        persisted = 0
        if self._breaker.is_open():
            message = f"{self._provider_name}: circuit open"
            for chain in CHAINS:
                attempted.append(chain.id)
                state = self._states[chain.id]
                state.last_attempt_at = self._now()
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
            return 0
        for chain in CHAINS:
            attempted.append(chain.id)
            state = self._states[chain.id]
            state.last_attempt_at = self._now()
            remaining = max(0.0, deadline_end - self._monotonic())
            if remaining <= 0:
                message = f"{self._provider_name}: discovery deadline exceeded"
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                continue
            try:
                rows, _source, coverage = await asyncio.wait_for(
                    self._source.discover_markets(
                        chain=chain.id, sort="trending", limit=min(self._limit, 100)
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                self._breaker.record_failure()
                message = f"{self._provider_name}: discovery deadline exceeded"
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                logger.warning("market scan %s failed: %s", chain.id, message)
                continue
            except Exception as exc:
                self._breaker.record_failure()
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
                self._breaker.record_failure()
                message = "chain probe failed"
                state.rows = 0
                state.last_error = message
                errors[chain.id] = message
                rows_by_chain[chain.id] = 0
                continue
            self._breaker.record_success()
            successful_chains.add(chain.id)
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
        persisted = 0
        failures: list[str] = []
        try:
            with self._session_factory() as session:
                for row in best:
                    snapshot = _snapshot(row, self._max_mark_age_seconds)
                    try:
                        # One savepoint per row: a row the database refuses costs only
                        # that row, and the rest of the batch still commits.
                        with session.begin_nested():
                            stored = market_snapshots.record_snapshot(session, snapshot)
                    except Exception as exc:
                        message = _error_text(exc)
                        failures.append(f"{snapshot.address}: {message}")
                        logger.warning(
                            "market snapshot %s:%s failed: %s",
                            chain_id,
                            snapshot.address,
                            message,
                        )
                        continue
                    if stored is None:
                        # A far-future report is rejected, not a persisted row; it must
                        # not make the chain look fresher than its evidence.
                        failures.append(f"{snapshot.address}: MARK_TIMESTAMP_FUTURE")
                        continue
                    persisted += 1
                session.commit()
        except Exception as exc:
            message = _error_text(exc)
            state.rows = 0
            state.last_error = message
            errors[chain_id] = message
            logger.warning("market scan %s persist failed: %s", chain_id, message)
            return 0
        state.rows = persisted
        state.last_success_at = observed_at
        if failures:
            message = f"{len(failures)} snapshot row(s) failed: {failures[0]}"
            state.last_error = message
            errors[chain_id] = message
        else:
            state.last_error = None
        return persisted

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


def count_stored_snapshots(session: Session, *, chain: str = "all") -> int:
    """Total persisted observations for the chain filter (one per token)."""
    statement = select(func.count(MarketSnapshotRow.id))
    if chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == chain)
    return int(session.scalar(statement) or 0)


@dataclass(frozen=True)
class StoredFilters:
    """Which rows the stored list shows, as values SQL can answer.

    Every field here is also carried by the response's ``applied_filters`` and by the
    cursor, so this is the request's filter set and nothing derived from the rows.
    """

    chain: str = "all"
    dex: str | None = None
    min_liquidity_usd: Decimal | None = None
    min_volume_24h_usd: Decimal | None = None
    min_change_h24: Decimal | None = None
    max_change_h24: Decimal | None = None
    min_pair_age_minutes: int | None = None
    max_pair_age_minutes: int | None = None


def _money(column: Any, dialect: str) -> Any:
    """The money column as a number, so a filter compares values and not digit strings.

    Postgres stores these columns as ``NUMERIC`` and compares them exactly as written.
    SQLite stores the same exact value as its decimal string (``_ExactDecimal``), where
    ``'500' >= '10000'`` is true because text compares character by character, so the
    comparison casts first. Only the filter casts: ordering keeps the column's own type,
    because that is what the published order means on each dialect.

    The cast is the one place this read is not exact. SQLite runs a cast through a double,
    so above about fifteen significant digits a floor can decide a row by a rounding step;
    that needs an amount past 10^15 with a fractional part at once, which a stored USD
    liquidity or volume is not. Postgres, the deployed database, compares the exact numbers.
    """
    return cast(column, Numeric(38, 8)) if dialect == "sqlite" else column


def _stored_predicates(
    filters: StoredFilters, now: datetime, dialect: str
) -> tuple[list[Any], dict[str, Any]]:
    """The SQL conditions these filters mean, and the row set each unknown field counts.

    A filter that names a value a row does not carry drops the row, and the row is counted
    under the field it was missing: unknown is never read as zero, and a row missing two
    fields is counted under both. Unknown is decided before any comparison, as the Python
    pass this replaces decided it, so a row missing a value is counted as unknown even when
    another filter would also have dropped it.
    """
    unknown: dict[str, Any] = {}
    kept: list[Any] = []
    if filters.min_liquidity_usd is not None:
        unknown["liquidity_usd"] = MarketSnapshotRow.liquidity_usd.is_(None)
        kept.append(_money(MarketSnapshotRow.liquidity_usd, dialect) >= filters.min_liquidity_usd)
    if filters.min_volume_24h_usd is not None:
        unknown["volume_24h_usd"] = MarketSnapshotRow.volume_24h_usd.is_(None)
        kept.append(_money(MarketSnapshotRow.volume_24h_usd, dialect) >= filters.min_volume_24h_usd)
    if filters.min_change_h24 is not None or filters.max_change_h24 is not None:
        unknown["price_change_h24"] = MarketSnapshotRow.price_change_h24.is_(None)
        if filters.min_change_h24 is not None:
            kept.append(
                _money(MarketSnapshotRow.price_change_h24, dialect) >= filters.min_change_h24
            )
        if filters.max_change_h24 is not None:
            kept.append(
                _money(MarketSnapshotRow.price_change_h24, dialect) <= filters.max_change_h24
            )
    if filters.min_pair_age_minutes is not None or filters.max_pair_age_minutes is not None:
        unknown["pair_created_at"] = MarketSnapshotRow.pair_created_at.is_(None)
        # Age is floored at zero at read time, so a pair dated in the future is zero minutes
        # old: a floor of zero constrains nothing, and a ceiling of zero keeps it.
        if filters.min_pair_age_minutes:
            kept.append(
                MarketSnapshotRow.pair_created_at
                <= now - timedelta(minutes=filters.min_pair_age_minutes)
            )
        if filters.max_pair_age_minutes is not None:
            kept.append(
                MarketSnapshotRow.pair_created_at
                >= now - timedelta(minutes=filters.max_pair_age_minutes)
            )
    if filters.dex is not None:
        # A row with no venue has no matching venue. Folding is ASCII-only in SQL, and every
        # venue identifier the adapters report is ASCII.
        kept.append(func.lower(MarketSnapshotRow.dex) == filters.dex.casefold())
    if unknown:
        kept.append(not_(or_(*unknown.values())))
    return kept, unknown


def _restrict(statement: Any, filters: StoredFilters, now: datetime, dialect: str) -> Any:
    if filters.chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == filters.chain)
    kept, _ = _stored_predicates(filters, now, dialect)
    for clause in kept:
        statement = statement.where(clause)
    return statement


def stored_snapshot_totals(
    session: Session, filters: StoredFilters, now: datetime
) -> tuple[int, dict[str, int]]:
    """The filtered row count and the per-field unknown counts, in one aggregate read.

    Both numbers describe the whole filtered population, so the database has to visit it;
    it returns one row of counters instead of one row per token, which is the difference
    between a count and the list this endpoint used to materialise.
    """
    dialect = session.get_bind().dialect.name
    kept, unknown = _stored_predicates(filters, now, dialect)
    kept_mask = and_(*kept) if kept else None
    columns: list[Any] = [
        func.sum(case((kept_mask, 1), else_=0))
        if kept_mask is not None
        else func.count(MarketSnapshotRow.id),
        *(func.sum(case((condition, 1), else_=0)) for condition in unknown.values()),
    ]
    statement = select(*columns)
    if filters.chain != "all":
        statement = statement.where(MarketSnapshotRow.chain == filters.chain)
    row = session.execute(statement).one()
    return int(row[0] or 0), {
        field: int(value or 0) for field, value in zip(unknown, row[1:], strict=True)
    }


def stored_snapshot_page(
    session: Session,
    filters: StoredFilters,
    now: datetime,
    *,
    limit: int,
    offset: int = 0,
    after: MarketSnapshotRow | None = None,
) -> list[MarketSnapshotRow]:
    """One page of the filtered list, ordered and cut by the database.

    ``after`` is the keyset anchor: the rows that sort after it in :data:`STORED_ORDER`.
    It and ``offset`` are alternatives, one per request. Either way the statement returns
    at most ``limit`` rows, so a page's cost does not grow with the table.
    """
    statement = _restrict(select(MarketSnapshotRow), filters, now, session.get_bind().dialect.name)
    if after is not None:
        statement = statement.where(_after_anchor(after))
    statement = statement.order_by(*STORED_ORDER).limit(limit).offset(max(0, offset))
    return list(session.scalars(statement))


def stored_snapshot_with_id(
    session: Session, filters: StoredFilters, now: datetime, row_id: str
) -> MarketSnapshotRow | None:
    """The cursor's anchor row, but only while it still matches this request's filters."""
    statement = _restrict(
        select(MarketSnapshotRow).where(MarketSnapshotRow.id == row_id),
        filters,
        now,
        session.get_bind().dialect.name,
    )
    return session.scalars(statement.limit(1)).first()


def stored_snapshot_position(
    session: Session, filters: StoredFilters, now: datetime, anchor: MarketSnapshotRow
) -> int:
    """How many filtered rows sort at or before ``anchor``: the offset of the row after it.

    The response reports ``offset`` and ``next_offset``, so a keyset page still has to know
    where it sits. This counts the page's prefix, the same rows an ``OFFSET`` page would
    have walked, and materialises none of them.
    """
    statement = _restrict(
        select(func.count()).where(not_(_after_anchor(anchor))),
        filters,
        now,
        session.get_bind().dialect.name,
    )
    return int(session.scalar(statement) or 0)


def _after_anchor(anchor: MarketSnapshotRow) -> Any:
    """Rows that sort strictly after ``anchor`` in :data:`STORED_ORDER`.

    Spelled out rather than written as a row-value comparison because a row value holding
    a NULL compares to unknown. Every row with unknown liquidity sorts after every row with
    a known one, so that whole group is one branch when the anchor's liquidity is known.
    """
    liquidity = MarketSnapshotRow.liquidity_usd
    tie = or_(
        MarketSnapshotRow.observed_at < anchor.observed_at,
        and_(
            MarketSnapshotRow.observed_at == anchor.observed_at,
            MarketSnapshotRow.id < anchor.id,
        ),
    )
    if anchor.liquidity_usd is None:
        return and_(liquidity.is_(None), tie)
    return or_(
        liquidity.is_(None),
        liquidity < anchor.liquidity_usd,
        and_(liquidity == anchor.liquidity_usd, tie),
    )


def stored_snapshots(
    session: Session, *, chain: str = "all", limit: int = 50, offset: int = 0
) -> list[MarketSnapshotRow]:
    """Latest persisted observation per token, deepest liquidity first, nulls last."""
    return stored_snapshot_page(
        session, StoredFilters(chain=chain), datetime.now(UTC), limit=limit, offset=offset
    )


__all__ = [
    "CHAIN_STATUSES",
    "ChainState",
    "MarketMonitor",
    "MarketSource",
    "ScanResult",
    "STORED_ORDER",
    "StoredFilters",
    "build_overview",
    "stale_after_seconds",
    "count_stored_snapshots",
    "stored_snapshot_page",
    "stored_snapshot_position",
    "stored_snapshot_totals",
    "stored_snapshot_with_id",
    "stored_snapshots",
]
