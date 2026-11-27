"""The API image and the frozen sidecar must not lose a route to a build artifact.

The bridge stays mandatory for the money kernels it owns, but a request's metric
label and access log are pure Python. These tests block the extension before the app
is imported, which is what the API image would do if the bridge were not installed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ezchain_api import native
from ezchain_api.observability import HttpMetrics, ObservabilityMiddleware, normalize_method

ROOT = Path(__file__).resolve().parents[2]

# Import the compiled extension under a finder that refuses it. Run in a fresh process:
# the client probes the bridge once at import, so an in-process test cannot undo a
# previous import. A real request is then issued through the full production app.
BRIDGE_ABSENT_PROBE = """
import json
import sys

class BlockBridge:
    def find_spec(self, name, path=None, target=None):
        if name == "ezchain_bridge":
            raise ImportError("blocked for the deployability test")

sys.meta_path.insert(0, BlockBridge())

from fastapi.testclient import TestClient
from ezchain_api import native
from ezchain_api.main import create_app

client = TestClient(create_app())
print("DEPLOYABILITY " + json.dumps({
    "bridge_loaded": native.using_rust(),
    "healthz": client.get("/healthz").status_code,
    "chains": client.get("/api/v1/chains").status_code,
}))
"""


def _source_paths() -> str:
    """Point the child at this checkout, ahead of any editable install in the venv."""
    paths = [ROOT / "packages" / name / "src" for name in ("core-py", "adapters", "sdk-py")]
    paths += [ROOT / "apps" / "api" / "src"]
    paths += [
        ROOT / "services" / name / "src"
        for name in ("engine", "agent-runtime", "executor", "ingest", "harness")
    ]
    return os.pathsep.join(str(path) for path in paths)


def test_healthz_and_a_normal_route_answer_200_when_the_bridge_module_is_absent() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = _source_paths()
    env.pop("EZCHAIN_API_TOKEN", None)
    env.pop("EZCHAIN_EXECUTOR_SECRET", None)
    completed = subprocess.run(
        [sys.executable, "-c", BRIDGE_ABSENT_PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    line = next(
        (entry for entry in completed.stdout.splitlines() if entry.startswith("DEPLOYABILITY ")),
        None,
    )
    assert line is not None, (
        "the app could not serve a request with the bridge absent: "
        f"exit={completed.returncode}\n{completed.stdout}\n{completed.stderr}"
    )
    result: dict[str, object] = json.loads(line.removeprefix("DEPLOYABILITY "))
    assert result["bridge_loaded"] is False
    assert result["healthz"] == 200
    assert result["chains"] == 200


def test_observability_does_not_call_the_bridge_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> str:
        raise AssertionError("observability called the bridge wrapper on a request path")

    monkeypatch.setattr(native, "normalize_method", forbidden)

    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware, metrics=HttpMetrics())

    @app.get("/api/v1/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/v1/ping").status_code == 200
    assert client.post("/api/v1/ping").status_code == 405


@pytest.mark.parametrize(
    ("method", "label"),
    [
        ("get", "GET"),
        ("pOsT", "POST"),
        ("PATCH", "PATCH"),
        ("delete", "DELETE"),
        ("BREW", "OTHER"),
        ("", "OTHER"),
        ("GET ", "OTHER"),
        ("poſt", "POST"),
        ("ß", "OTHER"),
        ("ge\u200bt", "OTHER"),
    ],
)
def test_pure_normalize_method_contract_is_bounded(method: str, label: str) -> None:
    assert normalize_method(method) == label
    assert label in {"GET", "POST", "PATCH", "DELETE", "PUT", "HEAD", "OPTIONS", "OTHER"}
