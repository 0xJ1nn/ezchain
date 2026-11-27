"""Allow the supervised unit to be started with ``python -m ezchain_api.gateway``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
