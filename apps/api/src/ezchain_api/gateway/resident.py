"""Host a library component in its own address space for the unit's lifetime.

Some components are libraries with no daemon loop and no port of their own. Keeping them
loaded in a supervised process preserves the isolation boundary — in particular the
boundary around money authority — without giving the gateway a second transport or a reason
to import those libraries itself.
"""

from __future__ import annotations

import importlib
import signal
import sys
import threading
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("resident: expected one component module", file=sys.stderr)
        return 2
    importlib.import_module(args[0])

    stopped = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopped.wait(1.0):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
