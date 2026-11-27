"""Restricted one-shot plugin host — real child processes, no mocks."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from ezchain_sdk.plugin.host import (
    REASON_CRASH,
    REASON_MANIFEST_INVALID,
    REASON_OUTPUT_OVERSIZE,
    REASON_RESPONSE_INVALID,
    REASON_TIMEOUT,
    HostLimits,
    PluginHostError,
    PluginHostResult,
    run_plugin,
)

VALID: dict[str, object] = {
    "schema": "ezchain.plugin/2",
    "id": "com.example.hostprobe",
    "name": "Host probe",
    "version": "1.0.0",
    "kind": "strategy",
    "capabilities": ["read_market"],
    "required_scopes": ["read"],
    "compatibility": {"manifest": "2", "host": ">=0.1.0,<0.2.0"},
    "config_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "license": "MIT",
    "author": "Test Author",
    "source_hash": "sha256:" + "a" * 64,
}


def write_plugin(tmp_path: Path, source: str, *, manifest: dict[str, object] | None = None) -> Path:
    root = tmp_path / "probe_plugin_dir"
    root.mkdir()
    (root / "ezchain.plugin.json").write_text(json.dumps(manifest or VALID), encoding="utf-8")
    (root / "probe_plugin.py").write_text(source, encoding="utf-8")
    return root


def call(
    root: Path, entrypoint: str, *args: object, limits: HostLimits | None = None
) -> PluginHostResult:
    return run_plugin(
        root,
        {"entrypoint": f"probe_plugin:{entrypoint}", "args": list(args)},
        limits=limits or HostLimits(timeout_s=2.0),
    )


ECHO = """
import os

def echo(value):
    print("plugin diagnostic on stdout")
    return {"seen": value, "pid": os.getpid()}
"""


def test_valid_plugin_returns_json_result_and_separate_stderr(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, ECHO)
    result = call(root, "echo", {"n": 1})
    assert isinstance(result, PluginHostResult)
    assert result.result["seen"] == {"n": 1}
    assert isinstance(result.result["pid"], int)
    assert "plugin diagnostic on stdout" in result.stderr
    assert "plugin diagnostic" not in json.dumps(result.result)


HANG = """
import time

def hang():
    time.sleep(30)
"""


def test_timeout_raises_a_typed_reason_quickly(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, HANG)
    started = time.monotonic()
    with pytest.raises(PluginHostError) as exc:
        call(root, "hang", limits=HostLimits(timeout_s=0.3))
    assert exc.value.reason == REASON_TIMEOUT
    assert "0.3" in exc.value.message
    assert time.monotonic() - started < 2.0


HANG_WITH_CHILD = """
import subprocess
import time

def hang(marker):
    subprocess.Popen(["/bin/sh", "-c", "sleep 0.4; touch " + marker])
    time.sleep(30)
"""


@pytest.mark.skipif(not Path("/bin/sh").exists(), reason="no /bin/sh for the group-kill test")
def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild-survived"
    root = write_plugin(tmp_path, HANG_WITH_CHILD)
    with pytest.raises(PluginHostError) as exc:
        call(root, "hang", str(marker), limits=HostLimits(timeout_s=0.15))
    assert exc.value.reason == REASON_TIMEOUT
    time.sleep(0.6)
    assert not marker.exists()


CRASH = """
def boom():
    raise RuntimeError("boom-marker")
"""


def test_plugin_exception_is_a_crash(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, CRASH)
    with pytest.raises(PluginHostError) as exc:
        call(root, "boom")
    assert exc.value.reason == REASON_CRASH
    assert "boom-marker" in exc.value.message
    assert "RuntimeError" in exc.value.message


HARD_EXIT = """
import os

def leave():
    os._exit(7)
"""


def test_hard_exit_without_a_response_is_a_crash(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, HARD_EXIT)
    with pytest.raises(PluginHostError) as exc:
        call(root, "leave")
    assert exc.value.reason == REASON_CRASH
    assert "7" in exc.value.message


FLOOD_STDERR = """
def flood():
    while True:
        print("X" * 4096)
"""


FLOOD_STDOUT = """
import os

def flood():
    while True:
        os.write(1, b"Y" * 4096)
"""


@pytest.mark.parametrize("source", [FLOOD_STDERR, FLOOD_STDOUT])
def test_flooding_either_stream_is_capped_and_kills_the_child(tmp_path: Path, source: str) -> None:
    root = write_plugin(tmp_path, source)
    started = time.monotonic()
    with pytest.raises(PluginHostError) as exc:
        call(root, "flood", limits=HostLimits(max_output_bytes=8192, timeout_s=2.0))
    assert exc.value.reason == REASON_OUTPUT_OVERSIZE
    assert "8192" in exc.value.message
    assert time.monotonic() - started < 2.0


ENV_PROBE = """
import os

def probe():
    try:
        import ezchain_sdk
        sdk_visible = True
    except ImportError:
        sdk_visible = False
    return {
        "secret": os.environ.get("EZCHAIN_HOST_TEST_SECRET"),
        "home": os.environ.get("HOME"),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "hashseed": os.environ.get("PYTHONHASHSEED"),
        "locale": os.environ.get("LC_ALL"),
        "encoding": os.environ.get("PYTHONIOENCODING"),
        "keys": sorted(os.environ),
        "sdk_visible": sdk_visible,
    }
"""


def test_parent_secrets_and_pythonpath_do_not_reach_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EZCHAIN_HOST_TEST_SECRET", "SENTINEL-DO-NOT-LEAK")
    root = write_plugin(tmp_path, ENV_PROBE)
    data = call(root, "probe").result
    assert data["secret"] is None
    assert data["home"] is None
    assert data["pythonpath"] is None
    assert data["hashseed"] == "0"
    assert data["locale"] == "C.UTF-8"
    assert data["encoding"] == "utf-8"
    assert "EZCHAIN_HOST_TEST_SECRET" not in data["keys"]
    assert "HOME" not in data["keys"]
    assert "PYTHONPATH" not in data["keys"]
    for expected in ("PATH", "PYTHONHASHSEED", "LC_ALL", "PYTHONIOENCODING"):
        assert expected in data["keys"]
    assert data["sdk_visible"] is False


CWD_PROBE = """
import os

def cwd():
    return os.getcwd()
"""


def test_working_directory_is_a_private_temporary_directory(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, CWD_PROBE)
    child_cwd = Path(call(root, "cwd").result)
    assert child_cwd != Path.cwd()
    assert child_cwd.parent == Path(tempfile.gettempdir()).resolve()
    assert child_cwd.name.startswith("ezchain-plugin-")
    assert not child_cwd.exists()  # private directory was removed on exit


LIMITS_PROBE = """
import resource

def limits():
    names = ("RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_NOFILE", "RLIMIT_FSIZE")
    return {name: resource.getrlimit(getattr(resource, name)) for name in names}
"""


@pytest.mark.skipif(
    not all(
        hasattr(__import__("resource"), name)
        for name in ("RLIMIT_CPU", "RLIMIT_AS", "RLIMIT_NOFILE", "RLIMIT_FSIZE")
    ),
    reason="POSIX rlimits unavailable on this platform",
)
def test_posix_rlimits_are_applied_in_the_child(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, LIMITS_PROBE)
    bounds = {
        "RLIMIT_CPU": 3,
        "RLIMIT_AS": 256 * 1024 * 1024,
        "RLIMIT_NOFILE": 48,
        "RLIMIT_FSIZE": 8192,
    }
    got = call(
        root,
        "limits",
        limits=HostLimits(
            timeout_s=2.0,
            cpu_s=bounds["RLIMIT_CPU"],
            address_space_bytes=bounds["RLIMIT_AS"],
            open_files=bounds["RLIMIT_NOFILE"],
            file_bytes=bounds["RLIMIT_FSIZE"],
        ),
    ).result
    # JSON decodes rlimit tuples as lists. Applying limits is best effort:
    # macOS, for example, refuses to lower RLIMIT_AS. A limit that was not
    # applied must still be the value the child inherited, and only RLIMIT_AS
    # is allowed to be skipped on a POSIX host.
    resource = __import__("resource")
    skipped: set[str] = set()
    for name, value in bounds.items():
        if got[name] == [value, value]:
            continue
        inherited = resource.getrlimit(getattr(resource, name))
        assert got[name] == list(inherited), f"{name} was neither applied nor left unchanged"
        skipped.add(name)
    assert skipped <= {"RLIMIT_AS"}, f"unexpected skipped rlimits: {sorted(skipped)}"


IMPORT_MARKER = """
from pathlib import Path

Path({marker!r}).write_text("imported")

def echo():
    return "should never run"
"""


def test_invalid_manifest_is_refused_before_any_plugin_code_runs(tmp_path: Path) -> None:
    marker = tmp_path / "imported.marker"
    root = write_plugin(
        tmp_path,
        IMPORT_MARKER.format(marker=str(marker)),
        manifest={**VALID, "kind": "telepathy"},
    )
    with pytest.raises(PluginHostError) as exc:
        call(root, "echo")
    assert exc.value.reason == REASON_MANIFEST_INVALID
    assert "PLUGIN_KIND_UNKNOWN" in exc.value.message
    assert not marker.exists()


def test_host_version_is_validated_before_launch(tmp_path: Path) -> None:
    root = write_plugin(tmp_path, ECHO)
    with pytest.raises(PluginHostError) as exc:
        run_plugin(
            root,
            {"entrypoint": "probe_plugin:echo", "args": [1]},
            host_version="0.2.0",
        )
    assert exc.value.reason == REASON_MANIFEST_INVALID
    assert "PLUGIN_HOST_INCOMPATIBLE" in exc.value.message


MALFORMED = """
import os

def bad():
    os.write(1, b'not json\\n')
"""


BAD_SHAPE = """
import os

def bad():
    os.write(1, b'{"ok": "yes", "result": 1}\\n')
"""


@pytest.mark.parametrize("source", [MALFORMED, BAD_SHAPE])
def test_malformed_response_is_typed(tmp_path: Path, source: str) -> None:
    root = write_plugin(tmp_path, source)
    with pytest.raises(PluginHostError) as exc:
        call(root, "bad")
    assert exc.value.reason == REASON_RESPONSE_INVALID
