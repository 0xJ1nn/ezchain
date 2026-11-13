"""The execution service: one pipeline from intent to journal.

Paper, testnet and live share :mod:`ezchain_executor.pipeline`; only the venue adapter
and the custodian differ. Key material never leaves this package, and the ledger is the
only accountant.
"""

from .journal import JournalSink, MemoryJournal
from .kill_switch import KillSwitch, KillSwitchActive, KillSwitchUnavailable
from .pipeline import DedupeStore, ExecutionPipeline, IntentRejected
from .signing import KeyRing, LocalTransactionSigner, SigningBoundary, SigningRefused
from .venue import FakeVenue, VenueError

__all__ = [
    "DedupeStore",
    "ExecutionPipeline",
    "FakeVenue",
    "IntentRejected",
    "JournalSink",
    "KeyRing",
    "KillSwitch",
    "KillSwitchActive",
    "KillSwitchUnavailable",
    "LocalTransactionSigner",
    "MemoryJournal",
    "SigningBoundary",
    "SigningRefused",
    "VenueError",
]
