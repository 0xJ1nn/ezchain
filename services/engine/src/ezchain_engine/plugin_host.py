"""Supervised plugin processes with timeouts, crash quarantine and a scrubbed env."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ezchain_sdk import PluginContext

from .plugins import PluginRecord

logger = logging.getLogger(__name__)

WORKER_MODULE = "ezchain_engine.plugin_worker"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_FAILURES = 3
FAILURE_WINDOW_SECONDS = 600.0
STDERR_TAIL = 20


@dataclass(frozen=True)
class PluginTick:
    """One supervised callback result. ``ok`` is false for any host-side refusal."""

    ok: bool
    intents: tuple[dict[str, Any], ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    logs: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "intents": list(self.intents),
            "state": self.state,
            "logs": list(self.logs),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class PluginWorkerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Worker:
    process: asyncio.subprocess.Process
    lock: asyncio.Lock
    stderr_task: asyncio.Task[None] | None = None
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=STDERR_TAIL))
    config_digest: str = ""


class PluginHost:
    """One worker per strategy, restarted on config change and quarantined on crashes.

    The host is intentionally process-local. A restart loses in-memory plugin state; the
    replayable state is the ``ctx.state`` returned with each tick and persisted in the run
    log, which the context builder feeds back on the next tick.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        memory_mb: int = 512,
        cpu_seconds: int = 10,
    ) -> None:
        self._timeout = max(0.1, timeout_seconds)
        self._memory_mb = max(64, memory_mb)
        self._cpu_seconds = max(1, cpu_seconds)
        self._workers: dict[str, _Worker] = {}
        self._quarantined: dict[str, str] = {}
        self._failures: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout

    def quarantined(self) -> dict[str, str]:
        return dict(self._quarantined)

    def release(self, strategy_id: str) -> None:
        """Clear a quarantine after an operator has inspected the plugin."""
        self._quarantined.pop(strategy_id, None)
        self._failures.pop(strategy_id, None)

    async def tick(
        self,
        strategy_id: str,
        record: PluginRecord,
        context: PluginContext,
        *,
        event: Mapping[str, Any] | None = None,
    ) -> PluginTick:
        if strategy_id in self._quarantined:
            return PluginTick(
                ok=False,
                error_code="PLUGIN_QUARANTINED",
                error_message=self._quarantined[strategy_id],
            )
        digest = self._config_digest(record, context.params)
        try:
            worker = await self._ensure_worker(strategy_id, record, context, digest)
        except PluginWorkerError as exc:
            self._record_failure(strategy_id, exc.code, exc.message)
            return PluginTick(ok=False, error_code=exc.code, error_message=exc.message)

        async with worker.lock:
            try:
                response = await self._exchange(
                    worker,
                    {"op": "tick", "context": context.to_dict(), "event": dict(event or {})},
                )
            except PluginWorkerError as exc:
                await self._kill(strategy_id, worker)
                self._record_failure(strategy_id, exc.code, exc.message)
                return PluginTick(ok=False, error_code=exc.code, error_message=exc.message)

        if not response.get("ok"):
            error = response.get("error", {})
            code = str(error.get("code", "PLUGIN_ERROR"))
            message = str(error.get("message", "plugin callback failed"))
            self._record_failure(strategy_id, code, message)
            return PluginTick(ok=False, error_code=code, error_message=message)

        self._failures.pop(strategy_id, None)
        intents = response.get("intents", [])
        state = response.get("state", {})
        logs = response.get("logs", [])
        if (
            not isinstance(intents, list)
            or not isinstance(state, dict)
            or not isinstance(logs, list)
        ):
            return PluginTick(
                ok=False,
                error_code="PLUGIN_CONTRACT_VIOLATION",
                error_message="worker returned a malformed result",
            )
        return PluginTick(
            ok=True,
            intents=tuple(item for item in intents if isinstance(item, dict)),
            state=state,
            logs=tuple(item for item in logs if isinstance(item, dict)),
        )

    async def close(self, strategy_id: str) -> None:
        worker = self._workers.pop(strategy_id, None)
        if worker is not None:
            await self._shutdown(worker, reason="strategy_closed")
        self._quarantined.pop(strategy_id, None)
        self._failures.pop(strategy_id, None)

    async def close_all(self) -> None:
        for strategy_id in list(self._workers):
            await self.close(strategy_id)
        # Quarantine can outlive its worker: a timeout kills the process before the
        # failure is recorded. Reload is the documented recovery action, so it must
        # clear both maps.
        self._quarantined.clear()
        self._failures.clear()

    async def _ensure_worker(
        self,
        strategy_id: str,
        record: PluginRecord,
        context: PluginContext,
        digest: str,
    ) -> _Worker:
        async with self._lock:
            worker = self._workers.get(strategy_id)
            if worker is not None and worker.config_digest == digest:
                if worker.process.returncode is None:
                    return worker
                self._workers.pop(strategy_id, None)
            if worker is not None:
                await self._shutdown(worker, reason="config_changed")
            worker = await self._spawn(record, context, digest)
            self._workers[strategy_id] = worker
            return worker

    async def _spawn(self, record: PluginRecord, context: PluginContext, digest: str) -> _Worker:
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "EZCHAIN_PLUGIN_MEMORY_MB": str(self._memory_mb),
            "EZCHAIN_PLUGIN_CPU_SECONDS": str(self._cpu_seconds),
        }
        cwd = str(record.root) if isinstance(record.root, Path) else None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-B",
                "-m",
                WORKER_MODULE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                limit=MAX_RESPONSE_BYTES,
                start_new_session=True,
            )
        except OSError as exc:
            raise PluginWorkerError("PLUGIN_SPAWN_FAILED", str(exc)) from exc
        worker = _Worker(process=process, lock=asyncio.Lock(), config_digest=digest)
        worker.stderr_task = asyncio.create_task(self._drain_stderr(process, worker.stderr_tail))
        init = {
            "plugin_root": None if record.root is None else str(record.root),
            "entrypoint": record.manifest.entrypoint,
            "context": context.to_dict(),
        }
        try:
            response = await self._exchange(worker, init, timeout=self._timeout)
        except PluginWorkerError:
            await self._shutdown(worker, reason="init_failed")
            raise
        if not response.get("ok"):
            error = response.get("error", {})
            code = str(error.get("code", "PLUGIN_LOAD_FAILED"))
            message = str(error.get("message", "plugin failed to start"))
            await self._shutdown(worker, reason="init_failed")
            raise PluginWorkerError(code, message)
        return worker

    async def _exchange(
        self,
        worker: _Worker,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        process = worker.process
        if process.returncode is not None:
            raise PluginWorkerError("PLUGIN_CRASHED", self._stderr_message(worker))
        if process.stdin is None or process.stdout is None:
            raise PluginWorkerError("PLUGIN_SPAWN_FAILED", "worker pipes are unavailable")
        try:
            encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PluginWorkerError("PLUGIN_CONTEXT_INVALID", str(exc)) from exc
        try:
            process.stdin.write(encoded + b"\n")
            await process.stdin.drain()
            line = await asyncio.wait_for(
                process.stdout.readline(), timeout=timeout or self._timeout
            )
        except TimeoutError as exc:
            raise PluginWorkerError(
                "PLUGIN_TIMEOUT", f"callback exceeded {timeout or self._timeout:.1f}s"
            ) from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise PluginWorkerError("PLUGIN_CRASHED", self._stderr_message(worker)) from exc
        except ValueError as exc:
            raise PluginWorkerError(
                "PLUGIN_PROTOCOL_ERROR", "worker response exceeds the 1 MiB frame limit"
            ) from exc
        if not line:
            raise PluginWorkerError("PLUGIN_CRASHED", self._stderr_message(worker))
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PluginWorkerError("PLUGIN_PROTOCOL_ERROR", "worker wrote invalid JSON") from exc
        if not isinstance(response, dict):
            raise PluginWorkerError("PLUGIN_PROTOCOL_ERROR", "worker response is not an object")
        return response

    async def _drain_stderr(self, process: asyncio.subprocess.Process, tail: deque[str]) -> None:
        if process.stderr is None:
            return
        while True:
            try:
                line = await process.stderr.readline()
            except ValueError:
                # A plugin can print a >1 MiB line. Diagnostics are best-effort; keep
                # draining so the task does not die with an unretrieved exception.
                tail.append("<stderr line exceeded the frame limit>")
                continue
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            tail.append(text[:500])
            logger.debug("plugin stderr: %s", text[:500])

    def _stderr_message(self, worker: _Worker) -> str:
        if not worker.stderr_tail:
            return "plugin worker exited without diagnostics"
        return worker.stderr_tail[-1][:500]

    async def _shutdown(self, worker: _Worker, *, reason: str) -> None:
        process = worker.process
        if process.returncode is None and process.stdin is not None:
            try:
                process.stdin.write(
                    json.dumps({"op": "close", "reason": reason}).encode("utf-8") + b"\n"
                )
                await process.stdin.drain()
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                pass
        if process.returncode is None:
            self._kill_process(process)
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except TimeoutError:
                pass
        if worker.stderr_task is not None:
            worker.stderr_task.cancel()

    async def _kill(self, strategy_id: str, worker: _Worker) -> None:
        self._workers.pop(strategy_id, None)
        self._kill_process(worker.process)
        if worker.stderr_task is not None:
            worker.stderr_task.cancel()
        try:
            await asyncio.wait_for(worker.process.wait(), timeout=1.0)
        except TimeoutError:
            pass

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - non-POSIX
                process.kill()
        except (ProcessLookupError, PermissionError):
            pass

    def _record_failure(self, strategy_id: str, code: str, message: str) -> None:
        now = asyncio.get_running_loop().time()
        failures = self._failures.setdefault(strategy_id, deque())
        failures.append(now)
        while failures and now - failures[0] > FAILURE_WINDOW_SECONDS:
            failures.popleft()
        if len(failures) >= MAX_FAILURES:
            self._quarantined[strategy_id] = (
                f"plugin quarantined after {len(failures)} failures in "
                f"{FAILURE_WINDOW_SECONDS:.0f}s: {code}: {message}"
            )
            logger.error("strategy %s %s", strategy_id, self._quarantined[strategy_id])

    @staticmethod
    def _config_digest(record: PluginRecord, params: Mapping[str, Any]) -> str:
        payload = json.dumps(
            {
                "id": record.manifest.id,
                "version": record.manifest.version,
                "entrypoint": record.manifest.entrypoint,
                "manifest_sha256": record.manifest_sha256,
                "root": None if record.root is None else str(record.root),
                "params": params,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["PluginHost", "PluginTick", "PluginWorkerError"]
