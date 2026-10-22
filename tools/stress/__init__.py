"""PAPER-only stress harness for ezCHAIN.

The package exercises the public API, verifies accounting invariants, writes evidence and
can file one deduplicated GitHub issue per failure fingerprint. It never signs, never
handles keys and refuses to run unless the API reports paper mode.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
