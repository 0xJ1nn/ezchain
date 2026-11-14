"""Operator tooling: a scripted backup and a rehearsed restore.

``backup`` dumps a Postgres database and records a fingerprint of the ledger it
contained. ``restore`` rebuilds a fresh database from a dump and proves the result
against that fingerprint. ``rehearse`` runs both and records the elapsed time, which is
the only number that makes a backup real.

Nothing here writes to a source database, and every restore target must have a name that
marks it as disposable.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
