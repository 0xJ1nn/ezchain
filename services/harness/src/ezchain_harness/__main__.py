"""``python -m ezchain_harness`` runs the headless command."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
