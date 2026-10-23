"""Supervised plugin worker.

The host starts this module in a separate interpreter. It speaks one JSON object per line
on stdin/stdout; plugin ``print`` output is redirected to stderr so it can never corrupt
the protocol. The worker has no database, HTTP client, token or key.

This is containment, not a security sandbox. It bounds time, memory and crashes, and it
keeps secrets out of the process. A plugin can still read the filesystem and open sockets;
install only code you trust.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Mapping
from typing import Any

MAX_STATE_BYTES = 64 * 1024
MAX_INTENTS = 20
MAX_LOGS = 100
MAX_LOG_BYTES = 128 * 1024


def _apply_limits() -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non-Unix
        return
    try:
        memory_mb = max(64, int(os.environ.get("EZCHAIN_PLUGIN_MEMORY_MB", "512")))
        memory = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    except (ValueError, OSError):
        pass
    try:
        cpu = max(1, int(os.environ.get("EZCHAIN_PLUGIN_CPU_SECONDS", "10")))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    except (ValueError, OSError):
        pass


def _load_class(entrypoint: str) -> type[Any]:
    module_name, _, class_name = entrypoint.partition(":")
    module = __import__(module_name, fromlist=[class_name])
    target = getattr(module, class_name)
    if not isinstance(target, type):
        raise TypeError(f"{entrypoint} is not a class")
    return target


def _state(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("state must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError(f"state exceeds {MAX_STATE_BYTES} bytes")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("state must be an object")
    return decoded


def _logs(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logs = raw[:MAX_LOGS]
    try:
        encoded = json.dumps(logs, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return []
    if len(encoded.encode("utf-8")) > MAX_LOG_BYTES:
        trimmed = logs[:10]
        try:
            if len(json.dumps(trimmed, separators=(",", ":")).encode("utf-8")) <= MAX_LOG_BYTES:
                return trimmed
        except (TypeError, ValueError):
            pass
        return []
    return logs


def _intents(raw: object) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("a callback must return a list of Intent")
    if len(raw) > MAX_INTENTS:
        raise ValueError(f"at most {MAX_INTENTS} intents are allowed per tick")
    from ezchain_sdk import Intent

    result: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Intent):
            intent = item
        elif isinstance(item, Mapping):
            intent = Intent.from_dict(item)
        else:
            raise ValueError("a callback returned something other than Intent")
        result.append(intent.to_dict())
    return result


def main() -> int:
    _apply_limits()
    protocol = sys.stdout
    # Plugin output must not share the protocol stream.
    sys.stdout = sys.stderr

    def send(payload: Mapping[str, Any]) -> None:
        protocol.write(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
        protocol.flush()

    line = sys.stdin.readline()
    if not line:
        return 2
    try:
        init = json.loads(line)
        if not isinstance(init, Mapping):
            raise ValueError("init must be an object")
        root = init.get("plugin_root")
        if isinstance(root, str) and root:
            sys.path.insert(0, root)
        entrypoint = str(init["entrypoint"])
        strategy_class = _load_class(entrypoint)
        strategy = strategy_class()
        from ezchain_sdk import PluginContext

        context = PluginContext.from_dict(init["context"])
        started = False
        start_state: dict[str, Any] = {}
        try:
            strategy.on_start(context)
            start_state = dict(context.state)
            started = True
        except Exception:
            traceback.print_exc(file=sys.stderr)
            send(
                {
                    "ok": False,
                    "error": {
                        "code": "PLUGIN_START_FAILED",
                        "type": "on_start",
                        "message": "on_start raised; see host diagnostics",
                    },
                }
            )
        send({"ok": True, "ready": True})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        send(
            {
                "ok": False,
                "error": {
                    "code": "PLUGIN_LOAD_FAILED",
                    "type": type(exc).__name__,
                    "message": str(exc)[:500],
                },
            }
        )
        return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise ValueError("message must be an object")
            op = message.get("op")
            if op == "close":
                try:
                    strategy.on_stop(context, str(message.get("reason", "host")))
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                send({"ok": True, "closed": True})
                return 0
            if op != "tick":
                raise ValueError(f"unknown op: {op!r}")
            context = PluginContext.from_dict(message["context"])
            for key, value in start_state.items():
                context.state.setdefault(key, value)
            event = message.get("event", {"kind": "timer"})
            if not isinstance(event, Mapping):
                raise ValueError("event must be an object")
            kind = event.get("kind")
            if kind == "candle":
                from ezchain_sdk import Candle

                result = strategy.on_candle(context, Candle.from_dict(event["candle"]))
            elif kind == "signal":
                from ezchain_sdk import Signal

                result = strategy.on_signal(context, Signal.from_dict(event["signal"]))
            elif kind == "fill":
                from ezchain_sdk import Fill

                strategy.on_fill(context, Fill.from_dict(event["fill"]))
                result = []
            else:
                result = strategy.on_timer(context)
            if not started:
                raise RuntimeError("on_start has not completed")
            extra = strategy.state()
            if extra:
                if not isinstance(extra, Mapping):
                    raise ValueError("strategy.state() must return an object")
                context.state.update(extra)
            send(
                {
                    "ok": True,
                    "intents": _intents(result),
                    "state": _state(context.state),
                    "logs": _logs(context.logs),
                }
            )
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send(
                {
                    "ok": False,
                    "error": {
                        "code": "PLUGIN_ERROR",
                        "type": type(exc).__name__,
                        "message": str(exc)[:500],
                    },
                }
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the host
    raise SystemExit(main())
