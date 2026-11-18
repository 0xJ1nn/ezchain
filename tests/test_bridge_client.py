"""The typed bridge client: dispatch, fallback, and an observable backend.

The compiled extension is optional and is not built for this suite. A stand-in replays
the recorded scope outcomes from the bridge crate's parity corpus, so both the Rust
path and the Python-fallback path are driven over every recorded case with
``monkeypatch`` only.

Covers: identical results/refusals on both paths across the recorded set, the Python
original when the extension is absent, unchanged ``ApiKeyError``, and ``using_rust()``.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ezchain_api import native
from ezchain_api.services.api_keys import ApiKeyError
from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes

CASES_PATH = Path(__file__).resolve().parents[1] / "rust/ezchain-bridge/tests/cases.jsonl"
ALL_CASES = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line]
SCOPE_CASES = [case for case in ALL_CASES if case["fn"] == "normalize_scopes"]
RECORDED_COUNT = 53
SCOPE_OUTCOME_COUNTS = {"success": 7, "error": 46}


def _freeze(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _case_key(case: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(_freeze(arg) for arg in case["args"])


def _outcome(function: Callable[..., Any], args: list[Any]) -> tuple[Any, ...]:
    """A comparable record of a call: its value, or its exact typed error."""
    try:
        return ("success", function(*args))
    except Exception as exc:  # noqa: BLE001 - refusals are part of the recorded corpus
        return (
            "error",
            type(exc),
            str(exc),
            getattr(exc, "code", None),
            getattr(exc, "message", None),
        )


@pytest.fixture
def recorded() -> dict[tuple[Any, ...], tuple[Any, ...]]:
    """Record the oracle outcome of every scope case once (the parity-proven behaviour)."""
    return {_case_key(case): _outcome(python_normalize_scopes, case["args"]) for case in SCOPE_CASES}


class _RecordingBridge:
    """Stands in for the compiled module: replay recorded outcomes, count dispatch."""

    def __init__(self, table: dict[tuple[Any, ...], tuple[Any, ...]]) -> None:
        self.table = table
        self.calls: list[tuple[Any, ...]] = []

    def normalize_scopes(self, scopes: Any) -> tuple[str, ...]:
        self.calls.append(tuple(scopes or []))
        outcome = self.table[(_freeze(scopes),)]
        if outcome[0] == "success":
            return outcome[1]
        # The real bridge raises the original class; so does the stand-in.
        raise ApiKeyError(outcome[3], outcome[4])


def test_recorded_corpus_is_the_scope_parity_set(
    recorded: dict[tuple[Any, ...], tuple[Any, ...]],
) -> None:
    assert len(ALL_CASES) == 63
    assert len(SCOPE_CASES) == RECORDED_COUNT
    assert dict(Counter(outcome[0] for outcome in recorded.values())) == SCOPE_OUTCOME_COUNTS


def test_both_paths_match_for_every_recorded_case(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    bridge = _RecordingBridge(recorded)
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True

    for case in SCOPE_CASES:
        expected = recorded[_case_key(case)]
        assert _outcome(python_normalize_scopes, case["args"]) == expected
        assert _outcome(native.normalize_scopes, case["args"]) == expected

    assert len(bridge.calls) == len(SCOPE_CASES)

    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for case in SCOPE_CASES:
        assert _outcome(native.normalize_scopes, case["args"]) == recorded[_case_key(case)]


def test_import_failure_is_recorded_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    real_import_module = importlib.import_module
    attempted: list[str] = []

    def missing(name: str) -> Any:
        attempted.append(name)
        if name == native._BRIDGE_MODULE:
            raise ImportError("simulated: compiled extension absent")
        return real_import_module(name)

    monkeypatch.setattr(native.importlib, "import_module", missing)
    assert native._load_bridge() is None
    assert attempted == ["ezchain_bridge"]

    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    assert _outcome(native.normalize_scopes, [["bogus", "read"]]) == _outcome(
        python_normalize_scopes, [["bogus", "read"]]
    )


def test_successful_import_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _RecordingBridge({})
    monkeypatch.setattr(native.importlib, "import_module", lambda name: bridge)
    assert native._load_bridge() is bridge

    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True


def test_the_original_exception_contract_is_not_weakened(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", _RecordingBridge(recorded))
    with pytest.raises(ApiKeyError) as via_client:
        native.normalize_scopes(["read", "bogus", "admin"])
    with pytest.raises(ApiKeyError) as via_original:
        python_normalize_scopes(["read", "bogus", "admin"])
    assert type(via_client.value) is type(via_original.value)
    assert via_client.value.code == via_original.value.code
    assert via_client.value.message == via_original.value.message
    assert str(via_client.value) == str(via_original.value)


def test_client_stays_in_the_api_layer() -> None:
    """A core-py client reversed the import boundary by importing ezchain_api."""
    assert native.__name__ == "ezchain_api.native"
    assert importlib.util.find_spec("ezchain_core.native") is None
