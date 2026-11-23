"""The gateway's one configuration source and its one data directory.

The unit reads a single JSON document. The location of that document is the only thing an
operator has to supply; every path the gateway writes — configuration snapshot, component
state and the merged log — lives under the same data directory. Explicit command-line
overrides are refinements of that one source, never a second source of truth.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BIND = "127.0.0.1:8080"
DEFAULT_MODE = "paper"
CONFIG_ENV = "EZCHAIN_GATEWAY_CONFIG"
DATA_DIR_ENV = "EZCHAIN_GATEWAY_DATA_DIR"


@dataclass(frozen=True)
class GatewayConfig:
    """Everything the supervisor needs, resolved before any component is started."""

    data_dir: Path
    bind: str = DEFAULT_BIND
    tls: bool = False
    mode: str = DEFAULT_MODE
    log_level: str = "INFO"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "gateway.json"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "gateway.state.json"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "gateway.log"

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "bind": self.bind,
            "tls": self.tls,
            "mode": self.mode,
            "log_level": self.log_level,
        }

    def save(self) -> None:
        """Snapshot the resolved settings beside the state and log files."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        self.config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _default_config_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "ezchain" / "gateway.json"


def _read_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def load_config(
    config_path: str | Path | None = None,
    *,
    data_dir: str | Path | None = None,
    bind: str | None = None,
    tls: bool | None = None,
    mode: str | None = None,
) -> GatewayConfig:
    """Resolve the single configuration document plus explicit overrides.

    A missing or unreadable document is not an error: the defaults describe a loopback
    unit in paper mode, which is the only safe starting point. Values are never echoed
    by the status or readiness commands.
    """
    if config_path:
        path = Path(config_path)
    elif data_dir is not None:
        # The data directory holds the one document, so a later command pointed at it
        # sees exactly the settings the unit was started with.
        path = Path(data_dir).expanduser() / "gateway.json"
    else:
        path = Path(os.environ.get(CONFIG_ENV, "") or _default_config_path())
    document = _read_document(path)

    raw_dir = (
        data_dir
        if data_dir is not None
        else (document.get("data_dir") or os.environ.get(DATA_DIR_ENV) or str(path.parent))
    )
    resolved_dir = Path(str(raw_dir)).expanduser()
    raw_tls = tls if tls is not None else document.get("tls", False)
    return GatewayConfig(
        data_dir=resolved_dir,
        bind=str(bind if bind is not None else document.get("bind", DEFAULT_BIND)),
        tls=bool(raw_tls),
        mode=str(mode if mode is not None else document.get("mode", DEFAULT_MODE)),
        log_level=str(document.get("log_level", "INFO")),
    )
