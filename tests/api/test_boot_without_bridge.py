"""The API boots and serves its probes with no compiled extension present.

The bridge is a prerequisite for the money and scope kernels, and it is deliberately
fail-closed there. It must not be a prerequisite for the process itself: a missing
``ezchain_bridge`` may refuse a money call, and must never refuse ``/healthz``, a metrics
scrape or a read. These tests simulate the extension being absent in a subprocess (the
import is memoised at module load, so a monkeypatch inside this process would not prove
anything about a cold boot) and assert the probes still answer.

The regression this locks out: ``ObservabilityMiddleware`` called ``native.normalize_method``
on every request, so a deployment without the extension — including the container image,
which built no Rust at all — answered 500 to everything, health check included.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Blocks the extension before any application import. A module-level ``sys.modules`` entry of
# ``None`` makes ``importlib.import_module`` raise ImportError, which is exactly what a wheel
# without the compiled artifact does.
BLOCKER = textwrap.dedent(
    """
    import json
    import sys

    class Block:
        def find_spec(self, name, path=None, target=None):
            if name == "ezchain_bridge":
                raise ImportError("simulated: the compiled extension is not installed")
            return None

    sys.meta_path.insert(0, Block())
    """
)


def _run(body: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a fresh interpreter with the bridge blocked."""
    script = BLOCKER + textwrap.dedent(body)
    descriptor, database = tempfile.mkstemp(suffix=".db", prefix="ezchain-nobridge-")
    os.close(descriptor)
    Path(database).unlink(missing_ok=True)
    env = {
        **os.environ,
        # The venv is the gate; PYTHONPATH makes the workspace sources win over editable
        # installs that may resolve elsewhere.
        "PYTHONPATH": os.pathsep.join(
            [
                str(ROOT / "packages" / "core-py" / "src"),
                str(ROOT / "packages" / "adapters" / "src"),
                str(ROOT / "apps" / "api" / "src"),
                str(ROOT / "services" / "engine" / "src"),
                str(ROOT / "services" / "executor" / "src"),
            ]
        ),
        "EZCHAIN_ENVIRONMENT": "test",
        "EZCHAIN_AUTH_ENABLED": "false",
        # A file-backed SQLite database, created by the app: the lifespan seeds the paper
        # genesis account and the kill-switch row, and an in-memory URL would give each
        # connection its own empty database.
        "EZCHAIN_AUTO_CREATE_SCHEMA": "true",
        "EZCHAIN_DATABASE_URL": f"sqlite:///{database}",
    }
    try:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(database + suffix).unlink(missing_ok=True)


def test_healthz_answers_without_the_bridge() -> None:
    result = _run(
        """
        from fastapi.testclient import TestClient
        from ezchain_api.main import app

        with TestClient(app) as client:
            response = client.get("/healthz")
        print(json.dumps({"status": response.status_code, "body": response.json()}))
        """
    )
    assert result.returncode == 0, result.stderr[-4000:]
    last = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last)
    assert payload["status"] == 200, payload
    assert payload["body"]["status"] == "ok"


def test_the_middleware_does_not_import_the_bridge() -> None:
    """A metered label is not worth a native dependency on every request."""
    source = (ROOT / "apps" / "api" / "src" / "ezchain_api" / "observability.py").read_text(
        encoding="utf-8"
    )
    assert "import native" not in source
    assert "native." not in source
    # The Python original is still the one the recorded corpus compares against.
    assert "def normalize_method(" in source


def test_a_money_call_still_fails_closed_without_the_bridge() -> None:
    """The contract on the money path is unchanged: no extension, no silent Python fallback."""
    result = _run(
        """
        from decimal import Decimal
        from ezchain_api.native import NativeBridgeUnavailable, quantize_money

        try:
            quantize_money(Decimal("1.5"))
        except NativeBridgeUnavailable as exc:
            print(json.dumps({"refused": True, "message": str(exc)}))
        else:
            print(json.dumps({"refused": False}))
        """
    )
    assert result.returncode == 0, result.stderr[-4000:]
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["refused"] is True
    assert "bash tools/build-native.sh" in payload["message"]
