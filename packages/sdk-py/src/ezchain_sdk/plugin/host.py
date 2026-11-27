"""One-shot restricted plugin invocation host.

``run_plugin`` starts a fresh ``sys.executable -I -S`` child for exactly one JSON
request and returns exactly one JSON response. It is a **restricted worker**,
not a security sandbox: see ``docs/STRATEGY-SDK.md`` §2.2 for the exact reach
boundary. The protocol is deliberately tiny — a request names an entrypoint
(``module:callable``) and carries JSON args/kwargs — and no broker, key, signer
or database handle is passed to the child.

The loader validates the manifest before a child is started. A child gets a
scrubbed environment, a private temporary cwd, only the plugin directory on
``sys.path``, best-effort POSIX rlimits, a wall-clock timeout and a per-stream
output cap. On timeout or overflow its whole process group is killed.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loader import discover_plugins

REASON_MANIFEST_INVALID = "PLUGIN_HOST_MANIFEST_INVALID"
REASON_TIMEOUT = "PLUGIN_HOST_TIMEOUT"
REASON_CRASH = "PLUGIN_HOST_CRASH"
REASON_OUTPUT_OVERSIZE = "PLUGIN_HOST_OUTPUT_OVERSIZE"
REASON_RESPONSE_INVALID = "PLUGIN_HOST_RESPONSE_INVALID"

REASONS: tuple[str, ...] = (
    REASON_MANIFEST_INVALID,
    REASON_TIMEOUT,
    REASON_CRASH,
    REASON_OUTPUT_OVERSIZE,
    REASON_RESPONSE_INVALID,
)

# Exactly these four variables reach the child. ``os.defpath`` is a stdlib
# constant, not the parent's PATH, so no parent directory leaks either.
_CHILD_ENV: dict[str, str] = {
    "PATH": os.defpath,
    "PYTHONHASHSEED": "0",
    "LC_ALL": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
}

# -I already ignores PYTHON* variables; the four above are still set explicitly
# so the allowlist is visible to the child and testable from the parent.
_BOOTSTRAP = r"""
import importlib
import json
import sys

plugin_dir = sys.argv[1]
raw_limits = json.loads(sys.argv[2])
# -I ignores PYTHONPATH, but not site-packages or .pth files; -S in the caller
# removes those, leaving stdlib paths plus this one plugin directory.
sys.path.insert(0, plugin_dir)

try:
    import resource
except ImportError:  # non-POSIX: resource limits are best effort
    resource = None

if resource is not None:
    for name, key in (
        ("RLIMIT_CPU", "cpu_s"),
        ("RLIMIT_AS", "address_space_bytes"),
        ("RLIMIT_NOFILE", "open_files"),
        ("RLIMIT_FSIZE", "file_bytes"),
    ):
        value = raw_limits.get(key)
        try:
            constant = getattr(resource, name)  # AttributeError on some macOS
        except AttributeError:
            continue
        if value is None:
            continue
        try:
            resource.setrlimit(constant, (int(value), int(value)))
        except (ValueError, OSError):
            pass

# Plugin writes are diagnostics; the protocol response uses the saved stream.
protocol_out = sys.stdout
sys.stdout = sys.stderr

try:
    request = json.loads(sys.stdin.read())
    if not isinstance(request, dict):
        raise TypeError("request must be a JSON object")
    entrypoint = request["entrypoint"]
    if not isinstance(entrypoint, str) or ":" not in entrypoint:
        raise ValueError("entrypoint must look like 'module:callable'")
    module_name, _, attr = entrypoint.partition(":")
    target = getattr(importlib.import_module(module_name), attr)
    args = request.get("args", [])
    kwargs = request.get("kwargs", {})
    payload = json.dumps(
        {"ok": True, "result": target(*args, **kwargs)}, ensure_ascii=False
    )
except BaseException as exc:
    payload = json.dumps(
        {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
        },
        ensure_ascii=False,
    )

protocol_out.write(payload + "\n")
protocol_out.flush()
"""


class PluginHostError(Exception):
    """A typed refusal from the host. ``reason`` is stable and safe to expose."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class HostLimits:
    """Best-effort bounds applied to one child invocation."""

    timeout_s: float = 5.0
    max_output_bytes: int = 1024 * 1024
    cpu_s: int = 10
    address_space_bytes: int = 512 * 1024 * 1024
    open_files: int = 64
    file_bytes: int = 1024 * 1024


DEFAULT_LIMITS = HostLimits()


@dataclass(frozen=True)
class PluginHostResult:
    """The decoded JSON response plus diagnostics captured from stderr."""

    result: Any
    stderr: str


class _BoundedReader(threading.Thread):
    """Read a pipe into a bounded buffer, flagging the first byte over the cap."""

    def __init__(self, stream: Any, cap: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._cap = cap
        self.data = bytearray()
        self.overflow = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(4096)
                if not chunk:
                    break
                room = self._cap - len(self.data)
                if len(chunk) > room:
                    self.data.extend(chunk[: max(room, 0)])
                    self.overflow = True
                    break
                self.data.extend(chunk)
        except (OSError, ValueError):
            pass


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except OSError:
            pass


def _tail(data: bytes, size: int = 2048) -> str:
    return data[-size:].decode("utf-8", errors="replace")


def _decode_payload(stdout: bytes) -> Any:
    """Validate the one-JSON-object protocol; raise a typed reason on failure."""
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginHostError(
            REASON_RESPONSE_INVALID, f"stdout is not one JSON response: {_tail(stdout)}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        raise PluginHostError(
            REASON_RESPONSE_INVALID,
            f"response must be an object with a boolean ok: {_tail(stdout)}",
        )
    if payload["ok"]:
        if set(payload) != {"ok", "result"}:
            raise PluginHostError(
                REASON_RESPONSE_INVALID, "success response must contain only ok and result"
            )
        return payload["result"]
    if set(payload) not in ({"ok", "error"}, {"ok", "error", "error_type"}):
        raise PluginHostError(
            REASON_RESPONSE_INVALID,
            "failure response must contain ok, error and optional error_type",
        )
    detail = str(payload.get("error", ""))
    raise PluginHostError(REASON_CRASH, detail)


def run_plugin(
    plugin_dir: str | Path,
    request: Mapping[str, Any],
    *,
    limits: HostLimits = DEFAULT_LIMITS,
    host_version: str | None = None,
) -> PluginHostResult:
    """Validate ``plugin_dir``, then invoke one entrypoint in a restricted child.

    ``request`` is ``{"entrypoint": "module:callable", "args": [...],
    "kwargs": {...}}``; the plugin's JSON ``result`` is returned with its
    separately captured stderr. Any refusal is a :class:`PluginHostError` with
    one of the five ``REASONS``.
    """
    root = Path(plugin_dir).expanduser().resolve()
    if not root.is_dir():
        raise PluginHostError(REASON_MANIFEST_INVALID, f"plugin directory does not exist: {root}")
    discovery = discover_plugins(root, host_version=host_version)
    if discovery.issues:
        issue = discovery.issues[0]
        raise PluginHostError(REASON_MANIFEST_INVALID, f"{issue.code}: {issue.message}")
    if not discovery.records:
        raise PluginHostError(REASON_MANIFEST_INVALID, f"no plugin manifest in {root}")

    limits_json = json.dumps(
        {
            "cpu_s": limits.cpu_s,
            "address_space_bytes": limits.address_space_bytes,
            "open_files": limits.open_files,
            "file_bytes": limits.file_bytes,
        }
    )
    # -I ignores PYTHON* variables; -S stops site/.pth files from putting
    # host packages (including this SDK) on the child's sys.path.
    argv = [sys.executable, "-I", "-S", "-c", _BOOTSTRAP, str(root), limits_json]
    request_bytes = json.dumps(dict(request), ensure_ascii=False).encode("utf-8") + b"\n"

    with tempfile.TemporaryDirectory(prefix="ezchain-plugin-") as cwd:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=_CHILD_ENV,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        out = _BoundedReader(proc.stdout, limits.max_output_bytes)
        err = _BoundedReader(proc.stderr, limits.max_output_bytes)
        out.start()
        err.start()
        timed_out = False
        try:
            try:
                proc.stdin.write(request_bytes)
            except BrokenPipeError:
                pass
            finally:
                try:
                    proc.stdin.close()
                except BrokenPipeError:
                    pass

            deadline = time.monotonic() + limits.timeout_s
            while proc.poll() is None:
                if out.overflow or err.overflow:
                    _kill_group(proc)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _kill_group(proc)
                    break
                time.sleep(0.005)
            proc.wait()
        finally:
            out.join(timeout=1.0)
            err.join(timeout=1.0)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError:
                    pass

    if out.overflow or err.overflow:
        raise PluginHostError(
            REASON_OUTPUT_OVERSIZE,
            f"output exceeded {limits.max_output_bytes} bytes on one stream",
        )
    if timed_out:
        raise PluginHostError(REASON_TIMEOUT, f"plugin exceeded {limits.timeout_s}s wall clock")
    if proc.returncode != 0:
        raise PluginHostError(
            REASON_CRASH,
            f"child exited with {proc.returncode}: {_tail(bytes(err.data))}",
        )
    result = _decode_payload(bytes(out.data))
    return PluginHostResult(result=result, stderr=_tail(bytes(err.data)))


__all__ = [
    "DEFAULT_LIMITS",
    "REASONS",
    "REASON_CRASH",
    "REASON_MANIFEST_INVALID",
    "REASON_OUTPUT_OVERSIZE",
    "REASON_RESPONSE_INVALID",
    "REASON_TIMEOUT",
    "HostLimits",
    "PluginHostError",
    "PluginHostResult",
    "run_plugin",
]
