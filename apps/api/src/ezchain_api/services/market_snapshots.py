"""Market snapshot writes: one row per token and the newest observation wins.

An order prices against a live token snapshot and records it; the market monitor refreshes
marks on its own schedule. Both writers share the ``(chain, token_address)`` key, so the
write is one ``INSERT ... ON CONFLICT DO UPDATE``: a writer that loses the race converges on
the winner instead of raising an integrity error that would abort an order or a scan.

The update is conditional on the incoming observation not being older than the stored one,
so a delayed poll cannot move a mark backwards. An observation dated beyond the tolerated
clock skew is rejected with ``MARK_TIMESTAMP_FUTURE`` and writes nothing: it never supplies
a price, never replaces a stored row, and cannot suppress a later correct observation. A
row already stored beyond that skew is a clock-faulted legacy row: it never outranks an
accepted report, so a corrected observation replaces it instead of being rejected by a
bogus timestamp. The receiver's clock is stored separately as ``received_at`` so a skew is
visible without trusting the source's timestamp.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ezchain_core import MARK_FUTURE_SKEW_SECONDS, TokenSnapshot, utcnow

from ..models import MarketSnapshotRow, new_id

logger = logging.getLogger(__name__)

# Kept as a module name for callers that read the bound here; the value lives in core so
# the writer, the read model and the risk gate cannot drift apart.
FUTURE_SKEW_SECONDS = MARK_FUTURE_SKEW_SECONDS

# The columns a newer observation replaces. The row id and the key columns never change.
_REPLACED_COLUMNS = (
    "symbol",
    "name",
    "price_usd",
    "liquidity_usd",
    "volume_24h_usd",
    "price_change_m5",
    "price_change_h1",
    "price_change_h6",
    "price_change_h24",
    "source",
    "observed_at",
    "received_at",
    "raw",
)

# Identity fields are filled when missing but never overwritten by a later
# observation, so a newer row cannot silently change a stored pair address.
_IDENTITY_COLUMNS = (
    "pair_address",
    "dex",
    "base_token_address",
    "quote_token_address",
    "pair_created_at",
    "url",
)


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _values(
    snapshot: TokenSnapshot, observed_at: datetime, received_at: datetime
) -> dict[str, Any]:
    token = snapshot.address
    source = snapshot.source
    return {
        "id": new_id(),
        "chain": snapshot.chain,
        "token_address": token,
        "symbol": snapshot.symbol or token,
        "name": snapshot.name or snapshot.symbol or token,
        "price_usd": snapshot.price_usd,
        "liquidity_usd": snapshot.liquidity_usd,
        "volume_24h_usd": snapshot.volume_24h_usd,
        "price_change_m5": snapshot.price_change_m5,
        "price_change_h1": snapshot.price_change_h1,
        "price_change_h6": snapshot.price_change_h6,
        "price_change_h24": snapshot.price_change_h24,
        "source": source.name if source is not None else "unknown",
        "observed_at": observed_at,
        "received_at": received_at,
        "pair_address": snapshot.pair_address,
        "dex": snapshot.dex,
        "base_token_address": snapshot.base_token_address,
        "quote_token_address": snapshot.quote_token_address,
        "pair_created_at": snapshot.pair_created_at,
        "url": snapshot.url,
        "raw": {},
    }


def _insert_for(session: Session) -> Any:
    """The dialect's INSERT so the conflict target resolves on the existing unique index."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgres_insert(MarketSnapshotRow)
    if dialect == "sqlite":
        return sqlite_insert(MarketSnapshotRow)
    raise RuntimeError(f"market snapshot storage does not support dialect {dialect}")


def _stored(session: Session, chain: str, token: str) -> MarketSnapshotRow:
    return session.scalars(
        select(MarketSnapshotRow)
        .where(MarketSnapshotRow.chain == chain, MarketSnapshotRow.token_address == token)
        .execution_options(populate_existing=True)
    ).one()


def record_snapshot(session: Session, snapshot: TokenSnapshot) -> MarketSnapshotRow | None:
    """Upsert the latest observation for (chain, token); older events never overwrite.

    One statement claims the key, so concurrent writers cannot collide on the unique
    index. Returns the row that is stored afterwards, which is the existing mark when the
    incoming observation lost on ``observed_at``. A report dated beyond the tolerated skew
    is rejected (``MARK_TIMESTAMP_FUTURE``) and writes nothing, so it cannot take a mark
    and cannot suppress the next correct observation; ``None`` is returned. A stored row
    that is itself beyond the skew is a clock fault, so it never outranks an accepted
    report: the corrected observation replaces it.
    """
    now = utcnow()
    source = snapshot.source
    observed = _aware(source.observed_at) if source is not None else now

    skew_seconds = (observed - now).total_seconds()
    if skew_seconds > FUTURE_SKEW_SECONDS:
        logger.warning(
            "market snapshot %s:%s rejected with MARK_TIMESTAMP_FUTURE: dated %.1fs after "
            "the write clock (limit %.1fs); source time %s, received %s",
            snapshot.chain,
            snapshot.address,
            skew_seconds,
            FUTURE_SKEW_SECONDS,
            observed.isoformat(),
            now.isoformat(),
        )
        return None

    values = _values(snapshot, observed, now)
    statement = _insert_for(session)
    conflict_target = ["chain", "token_address"]
    excluded = statement.excluded
    identity_set = {
        column: func.coalesce(getattr(MarketSnapshotRow, column), getattr(excluded, column))
        for column in _IDENTITY_COLUMNS
    }
    # The stored timestamp orders observations, but a legacy row already dated beyond the
    # tolerated skew is not evidence of a newer observation: trusting it would reject
    # every later correct write and freeze the bogus mark forever. Such a row loses to
    # any accepted report; a trustworthy row keeps the not-older rule.
    stored_is_bogus_future = MarketSnapshotRow.observed_at > now + timedelta(
        seconds=FUTURE_SKEW_SECONDS
    )
    write = statement.values(**values).on_conflict_do_update(
        index_elements=conflict_target,
        set_={
            **{column: getattr(excluded, column) for column in _REPLACED_COLUMNS},
            **identity_set,
        },
        where=or_(
            MarketSnapshotRow.observed_at <= excluded.observed_at,
            stored_is_bogus_future,
        ),
    )
    session.execute(write)
    return _stored(session, snapshot.chain, snapshot.address)
