"""Server-derived execution mode and the signed live gate record.

The mode is never a request field. It is computed from configuration and, for ``live``,
from a signed gate record bound to this exact build. A live start without a verified
record refuses by naming the record, never with a traceback (ADR-0035).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ezchain_api import __version__

if TYPE_CHECKING:
    from .config import Settings

# A live process is still refused after the gate passes until the key-holding executor
# ships. Flipping this is the only change that enables live execution; the gate stays.
LIVE_EXECUTOR_SHIPPED = False

GATE_SCHEMA_VERSION = 1
GATE_FIELDS = (
    "schema_version",
    "build_hash",
    "checklist_version",
    "approver",
    "approved_at",
    "signature",
)
PLACEHOLDER_BUILD_HASHES = frozenset({"", "dev", "local", "none", "unknown"})
MIN_GATE_KEY_BYTES = 32
_CHECKLIST_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class LiveGateRefused(ValueError):
    """A live start was refused. The message names the record or the field at fault."""


@dataclass(frozen=True)
class LiveGate:
    """A verified gate record. The signature has already been checked."""

    build_hash: str
    checklist_version: str
    approver: str
    approved_at: datetime
    fingerprint: str


@dataclass(frozen=True)
class ExecutionModeState:
    mode: str
    live_available: bool
    live_blocked_reason: str | None
    gate: LiveGate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution": self.mode,
            "live_available": self.live_available,
            "live_blocked_reason": self.live_blocked_reason,
        }


def build_fingerprint(build_hash: str) -> str:
    """A digest of the API version and the build hash the operator deployed."""
    canonical = f"ezchain-api/{__version__}:{build_hash.strip()}".encode()
    return hashlib.sha256(canonical).hexdigest()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _refuse(message: str) -> LiveGateRefused:
    return LiveGateRefused(f"EZCHAIN_EXECUTION_MODE=live refused: {message}")


def _current_build_hash(settings: Settings) -> str:
    current = settings.build_hash.strip()
    if current.lower() in PLACEHOLDER_BUILD_HASHES:
        raise _refuse(
            "EZCHAIN_BUILD_HASH must name the exact build; the live gate record is bound to it"
        )
    return current


def _record_path(settings: Settings) -> Path:
    configured = (settings.live_gate_record or "").strip()
    if not configured:
        raise _refuse(
            "no live gate record is configured; set EZCHAIN_LIVE_GATE_RECORD to the "
            "signed record for this build"
        )
    return Path(configured)


def _gate_key(settings: Settings) -> bytes:
    secret = settings.live_gate_key
    if secret is None:
        raise _refuse("EZCHAIN_LIVE_GATE_KEY is not set; the gate record cannot be verified")
    key = secret.get_secret_value().encode()
    if len(key) < MIN_GATE_KEY_BYTES:
        raise _refuse(f"EZCHAIN_LIVE_GATE_KEY must be at least {MIN_GATE_KEY_BYTES} bytes")
    return key


def _read_record(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _refuse(f"no live gate record at {path}") from None
    except OSError:
        raise _refuse(f"the live gate record at {path} cannot be read") from None
    except UnicodeDecodeError:
        raise _refuse(f"the live gate record at {path} is not UTF-8 text") from None
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        raise _refuse(f"the live gate record at {path} is not valid JSON") from None
    if not isinstance(record, dict):
        raise _refuse(f"the live gate record at {path} must be a JSON object")
    return record


def _require_text(record: dict[str, Any], field: str, path: Path) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _refuse(f"the live gate record at {path} is missing {field}")
    return value.strip()


def verify_live_gate(settings: Settings) -> LiveGate:
    """Verify the signed record for this build, or refuse naming what is missing."""
    path = _record_path(settings)
    build_hash = _current_build_hash(settings)
    key = _gate_key(settings)
    record = _read_record(path)

    unknown = sorted(set(record) - set(GATE_FIELDS))
    if unknown:
        raise _refuse(f"the live gate record at {path} has unknown fields: {', '.join(unknown)}")
    missing = sorted(set(GATE_FIELDS) - set(record))
    if missing:
        raise _refuse(f"the live gate record at {path} is missing {', '.join(missing)}")

    schema_version = record["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != GATE_SCHEMA_VERSION
    ):
        raise _refuse(
            f"the live gate record at {path} has schema_version "
            f"{schema_version!r}, expected {GATE_SCHEMA_VERSION}"
        )
    recorded_build = _require_text(record, "build_hash", path)
    if recorded_build != build_hash:
        raise _refuse(
            f"the live gate record at {path} is for build {recorded_build!r}, "
            f"this build is {build_hash!r}"
        )

    checklist_version = _require_text(record, "checklist_version", path)
    if not _CHECKLIST_VERSION.match(checklist_version):
        raise _refuse(
            f"the live gate record at {path} names an invalid checklist_version "
            f"{checklist_version!r}"
        )
    approver = _require_text(record, "approver", path)
    if len(approver) > 120:
        raise _refuse(f"the live gate record at {path} names an approver over 120 characters")
    approved_at_text = _require_text(record, "approved_at", path)
    try:
        approved_at = datetime.fromisoformat(approved_at_text.replace("Z", "+00:00"))
    except ValueError:
        raise _refuse(
            f"the live gate record at {path} has an invalid approved_at {approved_at_text!r}"
        ) from None

    supplied = _require_text(record, "signature", path)
    payload = {field: record[field] for field in GATE_FIELDS if field != "signature"}
    expected = hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise _refuse(f"the live gate record at {path} is not signed by EZCHAIN_LIVE_GATE_KEY")

    return LiveGate(
        build_hash=recorded_build,
        checklist_version=checklist_version,
        approver=approver,
        approved_at=approved_at,
        fingerprint=build_fingerprint(build_hash),
    )


def derive_execution_mode(settings: Settings) -> ExecutionModeState:
    """The one place the mode is decided. No request argument exists by design."""
    if settings.execution_mode != "live":
        return ExecutionModeState(
            mode="paper",
            live_available=False,
            live_blocked_reason="EZCHAIN_EXECUTION_MODE is paper",
        )
    gate = verify_live_gate(settings)
    if not LIVE_EXECUTOR_SHIPPED:
        raise _refuse("the live executor is not shipped in this build")
    return ExecutionModeState(mode="live", live_available=True, live_blocked_reason=None, gate=gate)


def mode_status(settings: Settings) -> dict[str, Any]:
    """The derived mode for read surfaces. A refused live start reports paper."""
    try:
        state = derive_execution_mode(settings)
    except LiveGateRefused as exc:
        return {
            "execution": "paper",
            "live_available": False,
            "live_blocked_reason": str(exc),
        }
    return state.to_dict()


__all__ = [
    "ExecutionModeState",
    "GATE_FIELDS",
    "GATE_SCHEMA_VERSION",
    "LIVE_EXECUTOR_SHIPPED",
    "LiveGate",
    "LiveGateRefused",
    "build_fingerprint",
    "derive_execution_mode",
    "mode_status",
    "verify_live_gate",
]
