"""Frozen entrypoint for the desktop API sidecar.

Electron starts this binary on a private loopback port with an embedded SQLite database.
It is the same FastAPI app as the server deployment; only the storage and bind address
differ.
"""

from __future__ import annotations

import os

import uvicorn

from ezchain_api.main import app


def main() -> None:
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
