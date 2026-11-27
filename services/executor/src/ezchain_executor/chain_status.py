"""Read-only bridge from a chain status reader to the broadcast outbox.

A chain adapter can say whether a transaction is confirmed, failed, pending or unknown,
but that answer carries no executed quantity, price or fee. The outbox's venue protocol
therefore receives only what is provable:

* ``failed`` on chain is evidence the transaction did not execute — the claim resolves as
  never filled (``not_found``).
* ``confirmed`` is *not* a fill. No receipt amounts are invented, so the status is
  ``unknown`` and the claim stays pending for later reconciliation from a real receipt.
* ``pending`` and ``unknown`` stay pending as well; a transport failure never becomes a
  negative answer.

This module holds no chain code, no RPC client and no key material; the reader is injected
structurally, so the adapter layer never imports the executor and vice versa.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .outbox import VenueTxStatus


@runtime_checkable
class ChainStatusReader(Protocol):
    """A read-only status source, structurally matched — no adapter import here."""

    async def status(self, tx_hash: str) -> object: ...


class ChainConfirmationVenue:
    """The outbox-facing seam; never constructs a ``Fill``."""

    def __init__(self, reader: ChainStatusReader) -> None:
        self.reader = reader

    async def transaction_status(
        self, *, order_id: str, transaction_digest: str | None
    ) -> VenueTxStatus:
        if not transaction_digest:
            return VenueTxStatus("unknown")
        try:
            confirmed = await self.reader.status(transaction_digest)
        except Exception:  # noqa: BLE001 - an unreadable status is not a negative answer
            return VenueTxStatus("unknown")
        outcome = getattr(confirmed, "outcome", None)
        if outcome == "failed":
            return VenueTxStatus("not_found")
        # A confirmed chain transaction does not prove an executed amount. Until a
        # receipt's transfers can be read and bound to the order, there is no Fill.
        return VenueTxStatus("unknown")


__all__ = ["ChainConfirmationVenue", "ChainStatusReader"]
