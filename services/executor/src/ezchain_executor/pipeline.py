"""The reference execution pipeline: Intent -> RiskDecision -> Quote -> Order -> Fill -> Journal.

This is the reference path for paper, testnet and live. The venue, the kill switch and
the journal sink are collaborators, so the only difference between modes is which venue
and which custodian are injected. (The API paper order service is still the caller of
record for paper orders; delegating it to this pipeline is tracked separately.) Rules
this file keeps:

* Fail closed on money. A missing snapshot or stale quote refuses both sides at the
  re-risk stage; a missing or malformed quote, or an unknown risk verdict, produces no
  order. Entry-only gates never block a reduce-only exit.
* The kill switch is read before any venue contact, again after the quote, and once more
  between the pre-submit position re-bound and the signature, so an arm lands within the
  same intent rather than on the next one. An unreadable switch refuses.
* The order is priced by the quote only when the quote's size is the risk-approved size;
  the journal is built from the venue's receipt, never from either.
* Idempotency is bound to the payload: the same key with a different order is a typed
  conflict, not a replay. Only order-producing results are cached, in a bounded map, so
  a retry after a pre-submit denial runs again.
* A reduce-only exit is sized to the held position, re-bounded against it before submit,
  and cannot be capped below it.
* With a signing boundary configured, the venue builds from an immutable approved intent
  and the executor decodes the bytes and checks them against it before the key is touched;
  a changed field refuses with a typed code. That check runs in the pipeline, not in the
  signer, so a signer that verifies nothing cannot disarm it; the boundary re-checks the
  signature it got back against the approved payload and approval it decoded. The signed
  payload's digest then travels through submit and a receipt that does not echo it is never
  journaled. Live refuses an unsigned submit before the venue; paper may run unsigned.
* Nothing here writes the ledger; the sink does, and only after a confirmed fill.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from ezchain_core import (
    SIGNING_BOUNDARY_REQUIRED,
    TRANSACTION_BUILD_FAILED,
    TRANSACTION_DIGEST_MISMATCH,
    TRANSACTION_NOT_APPROVABLE,
    TRANSACTION_SIGN_FAILED,
    ApprovedIntent,
    ExecutionResult,
    ExecutionStatus,
    Fill,
    Intent,
    Journal,
    Mode,
    Order,
    PositionState,
    Quote,
    RiskDecision,
    RiskLimits,
    RiskReason,
    SignedTransaction,
    TokenSnapshot,
    TransactionDecodeError,
    Venue,
    decode_transaction,
    journal_from_fill,
    risk_decision_from_intent,
    verify_prepared_transaction,
    verify_signed_transaction,
)

from .journal import JournalSink
from .kill_switch import KillSwitch, KillSwitchActive, KillSwitchUnavailable
from .signing import SigningBoundary, SigningRefused
from .venue import VenueError

# A quote may differ from the approved size only by rounding. Anything larger than this
# is a different order, not the one risk approved.
_QUOTE_SIZE_TOLERANCE = Decimal("0.01")


class IntentRejected(Exception):
    """Raised by :meth:`ExecutionPipeline.place` for a non-filled result."""

    def __init__(self, result: ExecutionResult) -> None:
        super().__init__(result.reason or result.status)
        self.result = result


class DedupeStore:
    """Bounded idempotency map: key -> (payload fingerprint, cached result).

    Only order-producing results are stored (see ``_should_cache``), and the oldest
    entry is evicted when the bound is reached, so process memory cannot grow without
    limit. ``max_entries`` must be positive.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        if max_entries < 1:
            raise ValueError("the idempotency store needs at least one entry")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[str, ExecutionResult]] = OrderedDict()

    def get(self, key: str) -> tuple[str, ExecutionResult] | None:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def __setitem__(self, key: str, value: tuple[str, ExecutionResult]) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class ExecutionPipeline:
    """Run one intent through every stage against one venue.

    ``snapshot_source`` and ``position_source`` are per-intent readers (market data and
    the ledger-derived position), not caches: a reduce-only exit is bounded to what the
    ledger says is held at the moment the intent runs.
    """

    venue: Venue
    sink: JournalSink
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    limits: RiskLimits | None = None
    snapshot_source: Any = None
    position_source: Any = None
    timeout_seconds: float = 10.0
    idempotency_store: DedupeStore = field(default_factory=DedupeStore)
    # When set, every order is built from an immutable approved intent, decoded and
    # checked against it, then signed before the venue sees it. Paper may leave it unset;
    # a live pipeline without one refuses every submit instead of broadcasting unsigned.
    signing: SigningBoundary | None = None
    mode: Mode = "paper"
    _in_flight: dict[str, tuple[str, asyncio.Future[ExecutionResult]]] = field(default_factory=dict)

    async def run(
        self,
        intent: Intent,
        *,
        snapshot: TokenSnapshot | None = None,
        position: PositionState | None = None,
        equity_usd: Decimal | None = None,
        open_positions: int = 0,
        daily_realized_pnl_usd: Decimal = Decimal(0),
        timeout_seconds: float | None = None,
    ) -> ExecutionResult:
        """Drive one intent through every stage. Never raises for a refused order."""
        fingerprint = _intent_fingerprint(intent)
        existing = self.idempotency_store.get(intent.key)
        if existing is not None:
            stored_fingerprint, cached = existing
            if stored_fingerprint != fingerprint:
                return _conflict(intent)
            return replace(cached, duplicate=True)
        inflight = self._in_flight.get(intent.key)
        if inflight is not None:
            inflight_fingerprint, inflight_claim = inflight
            if inflight_fingerprint != fingerprint:
                return _conflict(intent)
            return replace(await asyncio.shield(inflight_claim), duplicate=True)

        loop = asyncio.get_running_loop()
        claim: asyncio.Future[ExecutionResult] = loop.create_future()
        # A waiter may never arrive; consuming the exception prevents asyncio from logging
        # an unretrieved exception when this run itself raises.
        claim.add_done_callback(_consume_exception)
        self._in_flight[intent.key] = (fingerprint, claim)
        try:
            result = await self._run(
                intent,
                snapshot=snapshot,
                position=position,
                equity_usd=equity_usd,
                open_positions=open_positions,
                daily_realized_pnl_usd=daily_realized_pnl_usd,
                timeout_seconds=timeout_seconds,
            )
        except BaseException as exc:
            if not claim.done():
                claim.set_exception(exc)
            raise
        else:
            # A refusal that never reached the venue is retryable; caching it would make
            # a fixed snapshot or a fresh quote return the stale denial forever.
            if _should_cache(result):
                self.idempotency_store[intent.key] = (fingerprint, result)
            if not claim.done():
                claim.set_result(result)
            return result
        finally:
            self._in_flight.pop(intent.key, None)

    async def place(self, intent: Intent, **kwargs: Any) -> ExecutionResult:
        """Run an intent and raise :class:`IntentRejected` unless it filled."""
        result = await self.run(intent, **kwargs)
        if result.status not in ("filled", "partially_filled"):
            raise IntentRejected(result)
        return result

    async def _run(
        self,
        intent: Intent,
        *,
        snapshot: TokenSnapshot | None,
        position: PositionState | None,
        equity_usd: Decimal | None,
        open_positions: int,
        daily_realized_pnl_usd: Decimal,
        timeout_seconds: float | None,
    ) -> ExecutionResult:
        if snapshot is None and self.snapshot_source is not None:
            snapshot = await self.snapshot_source(intent)
        if position is None and self.position_source is not None:
            position = await self.position_source(intent)
        if position is None:
            position = _empty_position(intent)

        # 1. Size the request. A sell is bounded to the held position; a reduce-only exit
        # is never capped below it, so an entry limit cannot leave the intended exit
        # unsubmitted. Sizing first means risk sees the quantity the order will trade.
        sized = _size_request(intent, position, snapshot)
        if isinstance(sized, ExecutionResult):
            return sized
        intent = sized

        # 2. RiskDecision, before any venue call. Entry gates read the market snapshot; a
        # buy with no snapshot is denied here, never optimistically allowed.
        decision = risk_decision_from_intent(
            intent=intent,
            snapshot=snapshot,
            position=position,
            limits=self.limits,
            equity_usd=equity_usd,
            open_positions=open_positions,
            daily_realized_pnl_usd=daily_realized_pnl_usd,
        )
        if not decision.allowed:
            return _denied(intent, decision)

        # 3. The kill switch is read after risk and before the venue, so an armed switch
        # cannot look like a venue rejection and the venue is never contacted.
        blocked = self._kill_switch_denial(intent, decision)
        if blocked is not None:
            return blocked

        # 4. Quote. A sell is quoted for the exact quantity it closes.
        quoted = await self._quote(intent, decision, timeout_seconds)
        if isinstance(quoted, ExecutionResult):
            return quoted
        quote = quoted

        # 4.5 Re-read the switch after the venue round-trip: arming while the quote was in
        # flight must refuse the submit, not only the next intent.
        blocked = self._kill_switch_denial(intent, decision, quote=quote)
        if blocked is not None:
            return blocked

        # 5. Risk is re-evaluated against the quote it will trade: freshness and impact
        # are only knowable now. A denial leaves no order.
        quote_decision = risk_decision_from_intent(
            intent=intent,
            snapshot=snapshot,
            quote=quote,
            position=position,
            limits=self.limits,
            equity_usd=equity_usd,
            open_positions=open_positions,
            daily_realized_pnl_usd=daily_realized_pnl_usd,
        )
        if not quote_decision.allowed:
            return _denied(intent, quote_decision, quote=quote)

        # 6. Order, priced by the quote only when the quote is the approved size.
        order = _order_from_quote(intent, quote, quote_decision)
        if isinstance(order, ExecutionResult):
            return order

        # 6.5 A sell is re-bounded against the held position right before submit: the
        # ledger may have changed during the quote round-trip.
        if order.side == "sell":
            held = await self._held_position(intent, position)
            if held is None or order.quantity > held.quantity:
                return _refused_before_submit(intent, order, reason="POSITION_CHANGED")

        # 6.7 Sign. The adapter sees only the approved intent, the decode is checked
        # against it here in the pipeline, and a signer that verifies nothing cannot
        # disarm that. Live has no unsigned path: without a boundary the submit refuses.
        signed: SignedTransaction | None = None
        if self.signing is None:
            if self.mode != "paper":
                return _refused_before_submit(intent, order, reason=SIGNING_BOUNDARY_REQUIRED)
        else:
            signed_result = await self._sign(order)
            if isinstance(signed_result, ExecutionResult):
                return signed_result
            signed = signed_result

        # 6.8 The last gate for a buy is the submit boundary: an arm that landed during
        # the signing await above must refuse without the signed transaction reaching the
        # venue. Nothing awaits between this read and the submit call, so no stage can
        # outlive it. An exit is not recalled here: it only reduces the position and must
        # still settle.
        blocked = self._kill_switch_denial(intent, quote_decision, quote=quote)
        if blocked is not None:
            return blocked

        # 7. Fill from the venue receipt. A signature this process produced must come
        # back with the same payload digest, or the receipt is not for our transaction.
        submitted = await self._submit(order, signed, timeout_seconds)
        if isinstance(submitted, ExecutionResult):
            return submitted
        fill = submitted
        if signed is not None and fill.transaction_digest != signed.payload_digest:
            return _order_failed(order, "rejected", TRANSACTION_DIGEST_MISMATCH, fill=fill)

        # 8. Journal from the receipt, then append. The ledger is the only accountant.
        journal = _journal_from(intent, fill, position)
        try:
            self.sink.append(journal)
        except Exception as exc:  # noqa: BLE001 - the fill happened; the journal did not
            return ExecutionResult(
                intent=intent,
                status="journal_error",
                risk_decision=quote_decision,
                quote=quote,
                order=order,
                fill=fill,
                reason=f"JOURNAL_FAILED: {exc}",
            )

        return ExecutionResult(
            intent=intent,
            status="partially_filled" if fill.is_partial else "filled",
            risk_decision=quote_decision,
            quote=quote,
            order=order,
            fill=fill,
            journal=journal,
        )

    async def _quote(
        self, intent: Intent, decision: RiskDecision, timeout_seconds: float | None
    ) -> Quote | ExecutionResult:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            quote = await asyncio.wait_for(self.venue.quote(intent), timeout=timeout)
        except TimeoutError:
            return _denied(intent, decision, status="timeout", reason="QUOTE_TIMEOUT")
        except VenueError as exc:
            return _denied(intent, decision, status="rejected", reason=exc.code)
        except Exception:  # noqa: BLE001 - an adapter failure is a refusal, not a crash
            return _denied(intent, decision, status="rejected", reason="NO_QUOTE")
        if not _quote_is_executable(quote):
            return _denied(intent, decision, status="rejected", reason="NO_QUOTE")
        return quote

    async def _sign(self, order: Order) -> SignedTransaction | ExecutionResult:
        """Build, decode, bind and sign one order; refuse before the key on any doubt.

        The binding is checked here, not delegated to the signer: whichever
        :class:`~ezchain_core.signing.TransactionSigner` the boundary carries, the decoded
        payload must reproduce the approved intent before it is handed over, and the
        signature that comes back must cover those exact bytes and that same approval.
        """
        boundary = self.signing
        assert boundary is not None  # narrowed by the caller
        try:
            approved = ApprovedIntent.from_order(
                order,
                cluster=boundary.cluster,
                recipient=boundary.recipient,
                program_id=boundary.program_id,
            )
        except ValueError:
            return _refused_before_submit(
                _intent_from_order(order), order, reason=TRANSACTION_NOT_APPROVABLE
            )
        try:
            raw = await boundary.adapter.prepare(approved)
        except Exception:  # noqa: BLE001 - a builder failure is a refusal, not a crash
            return _refused_before_submit(
                _intent_from_order(order), order, reason=TRANSACTION_BUILD_FAILED
            )
        try:
            prepared = decode_transaction(raw)
        except TransactionDecodeError as exc:
            return _refused_before_submit(_intent_from_order(order), order, reason=exc.code)
        code = verify_prepared_transaction(prepared, approved)
        if code is not None:
            return _refused_before_submit(_intent_from_order(order), order, reason=code)
        try:
            signed = boundary.signer.sign(prepared, approved)
        except SigningRefused as exc:
            return _refused_before_submit(_intent_from_order(order), order, reason=exc.code)
        except Exception:  # noqa: BLE001 - an unverified signature is no signature
            return _refused_before_submit(
                _intent_from_order(order), order, reason=TRANSACTION_SIGN_FAILED
            )
        code = verify_signed_transaction(signed, prepared, approved)
        if code is not None:
            return _refused_before_submit(_intent_from_order(order), order, reason=code)
        return signed

    async def _submit(
        self,
        order: Order,
        signed: SignedTransaction | None,
        timeout_seconds: float | None,
    ) -> Fill | ExecutionResult:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            fill = await asyncio.wait_for(self.venue.submit(order, signed), timeout=timeout)
        except TimeoutError:
            return _order_failed(order, "timeout", "TIMEOUT")
        except VenueError as exc:
            return _order_failed(order, "rejected", exc.code)
        except Exception:  # noqa: BLE001 - an adapter failure is a refusal, not a crash
            return _order_failed(order, "rejected", "VENUE_ERROR")
        if not fill.bookable:
            return _order_failed(order, "rejected", "UNBOOKABLE_RECEIPT", fill=fill)
        if fill.quantity > order.quantity:
            return _order_failed(order, "rejected", "RECEIPT_EXCEEDS_ORDER", fill=fill)
        return fill

    def _kill_switch_denial(
        self,
        intent: Intent,
        decision: RiskDecision,
        *,
        quote: Quote | None = None,
    ) -> ExecutionResult | None:
        """A fresh switch read: ``None`` when execution may proceed."""
        try:
            self.kill_switch.check()
        except KillSwitchUnavailable:
            return _denied(intent, decision, reason="KILL_SWITCH_UNAVAILABLE", quote=quote)
        except KillSwitchActive:
            return _denied(intent, decision, reason="KILL_SWITCH_ACTIVE", quote=quote)
        return None

    async def _held_position(self, intent: Intent, fallback: PositionState) -> PositionState | None:
        """The position right before submit; ``None`` means it could not be read."""
        if self.position_source is None:
            return fallback
        try:
            fresh: PositionState | None = await self.position_source(intent)
        except Exception:  # noqa: BLE001 - an unreadable position must fail closed
            return None
        return fresh


def _consume_exception(future: asyncio.Future[ExecutionResult]) -> None:
    if not future.cancelled():
        future.exception()


def _size_request(
    intent: Intent, position: PositionState, snapshot: TokenSnapshot | None
) -> Intent | ExecutionResult:
    """Resolve a sell's token quantity from the position before it is quoted.

    A caller that knows the position sends the exact quantity. Without one, a normal sell
    converts its USD notional at the market mark and fails closed when the mark is
    unknown. A reduce-only exit with neither asks for the whole position: its executable
    sell quote is the exit's price, and a mark the platform cannot verify must not decide
    how much of the position to leave behind.
    """
    if intent.side == "buy":
        return intent
    held = position.quantity
    if held <= 0:
        return ExecutionResult(
            intent=intent,
            status="rejected",
            risk_decision=RiskDecision(True, ()),
            reason="INSUFFICIENT_POSITION",
        )
    if intent.quantity is not None:
        requested = intent.quantity
    elif intent.reduce_only:
        requested = held
    elif snapshot is not None and snapshot.price_usd is not None and snapshot.price_usd > 0:
        requested = intent.amount_usd / snapshot.price_usd
    else:
        return ExecutionResult(
            intent=intent,
            status="rejected",
            risk_decision=RiskDecision(True, ()),
            reason="PRICE_UNKNOWN",
        )
    quantity = min(requested, held)
    if quantity <= 0:
        return ExecutionResult(
            intent=intent,
            status="rejected",
            risk_decision=RiskDecision(True, ()),
            reason="INVALID_AMOUNT",
        )
    return intent.sized(amount_usd=intent.amount_usd, quantity=quantity)


def _order_from_quote(
    intent: Intent, quote: Quote, decision: RiskDecision
) -> Order | ExecutionResult:
    """Price the order from the quote, only when the quote is the approved size.

    The quote's input amount is the side risk approved: USD for a buy, token quantity
    for a sell. A venue that returns a different size cannot silently replace the
    approved order with a larger one.
    """
    if intent.side == "buy":
        quantity, amount_usd = quote.amount_out, quote.amount_in
        approved: Decimal | None = intent.amount_usd
    else:
        quantity, amount_usd = quote.amount_in, quote.amount_out
        approved = intent.quantity
    if quantity <= 0 or amount_usd <= 0:
        return ExecutionResult(
            intent=intent,
            status="rejected",
            risk_decision=decision,
            quote=quote,
            reason="NO_ROUTE",
        )
    if approved is None or not _consistent_size(quote.amount_in, approved):
        return ExecutionResult(
            intent=intent,
            status="rejected",
            risk_decision=decision,
            quote=quote,
            reason="QUOTE_SIZE_MISMATCH",
        )
    return Order(
        id=f"order:{intent.key}",
        idempotency_key=intent.key,
        chain=intent.chain,
        token_address=intent.token_address,
        side=intent.side,
        amount_usd=amount_usd,
        quantity=quantity,
        quote=quote,
        risk_decision=decision,
        reduce_only=intent.reduce_only,
    )


def _consistent_size(quoted: Decimal, approved: Decimal) -> bool:
    """True when ``quoted`` is ``approved`` up to rounding."""
    if approved <= 0:
        return False
    return abs(quoted - approved) <= approved * _QUOTE_SIZE_TOLERANCE


def _journal_from(intent: Intent, fill: Fill, position: PositionState) -> Journal:
    avg_cost = position.avg_cost_usd
    cost_basis = None
    if fill.side == "sell":
        cost_basis = min(position.cost_usd, fill.quantity * avg_cost)
    return journal_from_fill(
        fill,
        chain=intent.chain,
        token_address=intent.token_address,
        avg_cost_usd=avg_cost,
        cost_basis_usd=cost_basis,
    )


def _quote_is_executable(quote: Quote) -> bool:
    return (
        quote.price > 0
        and quote.amount_in > 0
        and quote.amount_out > 0
        and quote.source is not None
    )


def _empty_position(intent: Intent) -> PositionState:
    return PositionState(
        chain=intent.chain,
        token_address=intent.token_address,
        symbol="",
        quantity=Decimal(0),
        cost_usd=Decimal(0),
    )


def _denied(
    intent: Intent,
    decision: RiskDecision,
    *,
    status: ExecutionStatus = "denied",
    reason: str | None = None,
    quote: Quote | None = None,
) -> ExecutionResult:
    if reason is None:
        reason = ",".join(str(code) for code in decision.reasons) or "RISK_DENIED"
    return ExecutionResult(
        intent=intent,
        status=status,
        risk_decision=decision,
        quote=quote,
        reason=reason,
    )


def _order_failed(
    order: Order,
    status: ExecutionStatus,
    reason: str,
    *,
    fill: Fill | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        intent=_intent_from_order(order),
        status=status,
        risk_decision=order.risk_decision,
        quote=order.quote,
        order=order,
        fill=fill,
        reason=reason,
    )


def _intent_from_order(order: Order) -> Intent:
    return Intent(
        side=order.side,
        amount_usd=order.amount_usd,
        chain=order.chain,
        token_address=order.token_address,
        idempotency_key=order.idempotency_key,
        quantity=order.quantity,
        reduce_only=order.reduce_only,
    )


def _refused_before_submit(intent: Intent, order: Order, *, reason: str) -> ExecutionResult:
    """A refusal after pricing but before the venue saw the order (never cached)."""
    return ExecutionResult(
        intent=intent,
        status="rejected",
        risk_decision=order.risk_decision,
        quote=order.quote,
        reason=reason,
    )


def _conflict(intent: Intent) -> ExecutionResult:
    """A reused idempotency key that names a different order payload."""
    reason = RiskReason("IDEMPOTENCY_CONFLICT")
    return ExecutionResult(
        intent=intent,
        status="rejected",
        risk_decision=RiskDecision(False, (reason,)),
        reason=reason.code,
    )


def _should_cache(result: ExecutionResult) -> bool:
    """Cache only results that may have reached the venue.

    A pre-submit refusal carries no order, so it is retryable: a missing snapshot can be
    fixed and the same intent re-run. An order means the venue may have executed it, so
    the result is replayed rather than risked twice.
    """
    return result.order is not None


_FINGERPRINT_FIELDS = (
    "chain",
    "token_address",
    "side",
    "amount_usd",
    "quantity",
    "reduce_only",
    "strategy_id",
    "risk_profile",
)


def _canonical_decimal(value: Decimal) -> str:
    """One spelling per value, so ``50``, ``50.0`` and ``50.00`` hash alike."""
    if not value.is_finite():
        raise ValueError("a fingerprint cannot be built from a non-finite decimal")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _intent_fingerprint(intent: Intent) -> str:
    """SHA-256 of the intent payload the idempotency key is bound to."""
    values = {name: getattr(intent, name) for name in _FINGERPRINT_FIELDS}
    canonical = {
        name: _canonical_decimal(value) if isinstance(value, Decimal) else value
        for name, value in values.items()
        if value is not None
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["DedupeStore", "ExecutionPipeline", "IntentRejected"]
