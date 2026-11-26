"""Frozen entrypoint for the desktop API sidecar.

Electron starts this binary on a private loopback port with an embedded SQLite database.
It is the same FastAPI app as the server deployment; only the storage and bind address
differ.
"""

from __future__ import annotations

import os

import uvicorn

from ezchain_api.main import app


def smoke_native() -> None:
    """Post-freeze proof that the sidecar's own Python can load the bridge."""
    import importlib

    bridge = importlib.import_module("ezchain_bridge")
    assert hasattr(bridge, "quantize_money")
    print("ezchain_bridge: importable in the frozen sidecar")


def main() -> None:
    if os.environ.get("EZCHAIN_DESKTOP_SMOKE_NATIVE") == "1":
        smoke_native()
        return
    port = int(os.environ["EZCHAIN_DESKTOP_API_PORT"])
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("EZCHAIN_LOG_LEVEL", "INFO").lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
