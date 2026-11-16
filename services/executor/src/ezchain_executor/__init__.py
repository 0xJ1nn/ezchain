"""The execution service: one pipeline from intent to journal.

Paper, testnet and live share :mod:`ezchain_executor.pipeline`; only the venue adapter
and the custodian differ. Key material never leaves this package, and the ledger is the
only accountant.
"""

from .journal import JournalSink, MemoryJournal
from .kill_switch import KillSwitch, KillSwitchActive, KillSwitchUnavailable
from .outbox import (
    AttemptLog,
    BroadcastAttempt,
    FileAttemptLog,
    MemoryAttemptLog,
    Reconciler,
    Reconciliation,
    TransactionStatusVenue,
    VenueTxStatus,
)
from .pipeline import DedupeStore, ExecutionPipeline, IntentRejected
from .signing import KeyRing, LocalTransactionSigner, SigningBoundary, SigningRefused
from .vault import (
    HardwareSignerVault,
    SignerApprovalDenied,
    SignerHandleUnknown,
    SignerLocked,
    SignerSignature,
    SignerTransport,
    SignerTransportError,
    SignerUnreachable,
    VaultTransactionSigner,
)
from .venue import FakeVenue, VenueError

__all__ = [
    "AttemptLog",
    "BroadcastAttempt",
    "DedupeStore",
    "ExecutionPipeline",
    "FakeVenue",
    "FileAttemptLog",
    "HardwareSignerVault",
    "IntentRejected",
    "JournalSink",
    "KeyRing",
    "KillSwitch",
    "KillSwitchActive",
    "KillSwitchUnavailable",
    "LocalTransactionSigner",
    "MemoryAttemptLog",
    "MemoryJournal",
    "Reconciler",
    "Reconciliation",
    "SignerApprovalDenied",
    "SignerHandleUnknown",
    "SignerLocked",
    "SignerSignature",
    "SignerTransport",
    "SignerTransportError",
    "SignerUnreachable",
    "SigningBoundary",
    "SigningRefused",
    "TransactionStatusVenue",
    "VaultTransactionSigner",
    "VenueError",
    "VenueTxStatus",
]
