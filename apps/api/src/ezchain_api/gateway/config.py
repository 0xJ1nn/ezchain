"""The gateway's one configuration source and its one data directory.

The unit reads a single JSON document. The location of that document is the only thing an
operator has to supply; every path the gateway writes — configuration snapshot, component
state and the merged log — lives under the same data directory. Explicit command-line
overrides are refinements of that one source, never a second source of truth.

A listener outside loopback is refused unless it can be served over TLS and is backed by
the deployment token. ``tls`` is never a reporting label: it is built from an explicit
certificate and key that are passed to the listener. The token is read from the same
environment the API uses and is never written to the configuration snapshot or printed.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BIND = "127.0.0.1:8080"
DEFAULT_MODE = "paper"
CONFIG_ENV = "EZCHAIN_GATEWAY_CONFIG"
DATA_DIR_ENV = "EZCHAIN_GATEWAY_DATA_DIR"
TOKEN_ENV = "EZCHAIN_API_TOKEN"
TLS_CERT_ENV = "EZCHAIN_GATEWAY_TLS_CERT"
TLS_KEY_ENV = "EZCHAIN_GATEWAY_TLS_KEY"


class GatewayConfigError(ValueError):
    """The requested listener cannot be started safely."""


def split_bind(bind: str) -> tuple[str, str]:
    """Split a ``host:port`` bind into its host and port, defaulting a bare host."""
    host, separator, port = bind.rpartition(":")
    if not separator or not host or not port:
        return bind, "8000"
    return host, port


def is_loopback_bind(bind: str) -> bool:
    """True only for a host the operating system treats as the local machine."""
    host, _ = split_bind(bind)
    host = host.strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bind_security_problems(config: GatewayConfig) -> tuple[str, ...]:
    """The reasons this configuration cannot be started, empty when it is safe."""
    problems: list[str] = []
    if config.tls and not (config.tls_cert and config.tls_key):
        problems.append("TLS needs both a certificate and a key")
    if not is_loopback_bind(config.bind):
        if not config.tls:
            problems.append("a non-loopback bind requires TLS")
        if not config.token:
            problems.append(f"a non-loopback bind requires {TOKEN_ENV}")
    return tuple(problems)


def validate_bind_security(config: GatewayConfig) -> None:
    """Refuse a listener that would be plaintext or unauthenticated."""
    problems = bind_security_problems(config)
    if problems:
        raise GatewayConfigError("; ".join(problems))


@dataclass(frozen=True)
class GatewayConfig:
    """Everything the supervisor needs, resolved before any component is started."""

    data_dir: Path
    bind: str = DEFAULT_BIND
    tls: bool = False
    mode: str = DEFAULT_MODE
    log_level: str = "INFO"
    tls_cert: Path | None = None
    tls_key: Path | None = None
    # Present only to decide whether a non-loopback bind may start and to pass to the
    # child. Excluded from repr and from the snapshot so it is never printed or persisted.
    token: str | None = field(default=None, repr=False, compare=False)

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
    tls_cert: str | Path | None = None,
    tls_key: str | Path | None = None,
    validate_security: bool = True,
) -> GatewayConfig:
    """Resolve the single configuration document plus explicit overrides.

    A missing or unreadable document is not an error: the defaults describe a loopback
    unit in paper mode, which is the only safe starting point. Values are never echoed
    by the status or readiness commands. Read-only commands pass ``validate_security``
    false so an operator can inspect a unit whose token is not in this process's env.
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
    raw_cert = (
        tls_cert
        if tls_cert is not None
        else (document.get("tls_cert") or os.environ.get(TLS_CERT_ENV))
    )
    raw_key = (
        tls_key if tls_key is not None else (document.get("tls_key") or os.environ.get(TLS_KEY_ENV))
    )
    raw_token = os.environ.get(TOKEN_ENV) or document.get("api_token")
    resolved = GatewayConfig(
        data_dir=resolved_dir,
        bind=str(bind if bind is not None else document.get("bind", DEFAULT_BIND)),
        tls=bool(raw_tls),
        mode=str(mode if mode is not None else document.get("mode", DEFAULT_MODE)),
        log_level=str(document.get("log_level", "INFO")),
        tls_cert=Path(str(raw_cert)).expanduser() if raw_cert else None,
        tls_key=Path(str(raw_key)).expanduser() if raw_key else None,
        token=str(raw_token) if raw_token else None,
    )
    if validate_security:
        validate_bind_security(resolved)
    return resolved
