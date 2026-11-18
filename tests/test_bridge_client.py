"""The typed bridge client: dispatch, fallback, and an observable backend.

The compiled extension is optional and is not built for this suite. A stand-in replays
the recorded outcomes from the bridge crate's parity corpus, so both the Rust path and
the Python-fallback path are driven over every recorded case with ``monkeypatch`` only.

Covers: identical results/refusals on both paths across the recorded set, the Python
originals on import failure, unchanged ``ApiKeyError`` contract, and ``using_rust()``.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ezchain_api.services.api_keys import ApiKeyError
from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes
from ezchain_core import native
from ezchain_core.freshness import stale_reason as python_stale_reason

CASES_PATH = Path(__file__).resolve().parents[1] / "rust/ezchain-bridge/tests/cases.jsonl"
CASES = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line]
RECORDED_COUNTS = {"stale_reason": 10, "normalize_scopes": 53}
SCOPE_OUTCOME_COUNTS = {"success": 7, "error": 46}
ORIGINALS: dict[str, Callable[..., Any]] = {
    "stale_reason": python_stale_reason,
    "normalize_scopes": python_normalize_scopes,
}
WRAPPERS: dict[str, Callable[..., Any]] = {
    "stale_reason": native.stale_reason,
    "normalize_scopes": native.normalize_scopes,
}


def _freeze(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _key(case: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    return (case["fn"], tuple(_freeze(arg) for arg in case["args"]))


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
def recorded() -> dict[tuple[str, tuple[Any, ...]], tuple[Any, ...]]:
    """Record the oracle outcome of every case once (the parity-proven behaviour)."""
    return {_key(case): _outcome(ORIGINALS[case["fn"]], case["args"]) for case in CASES}


class _RecordingBridge:
    """Stands in for the compiled module: replay recorded outcomes, count dispatch."""

    def __init__(self, table: dict[tuple[str, tuple[Any, ...]], tuple[Any, ...]]) -> None:
        self.table = table
        self.calls: list[tuple[str, Any]] = []

    def stale_reason(self, source_class: str) -> str:
        self.calls.append(("stale_reason", source_class))
        outcome = self.table[("stale_reason", (source_class,))]
        assert outcome[0] == "success"
        return outcome[1]

    def normalize_scopes(self, scopes: Any) -> tuple[str, ...]:
        self.calls.append(("normalize_scopes", tuple(scopes or [])))
        outcome = self.table[("normalize_scopes", (_freeze(scopes),))]
        if outcome[0] == "success":
            return outcome[1]
        # The real bridge raises the original class; so does the stand-in.
        raise ApiKeyError(outcome[3], outcome[4])


def test_recorded_corpus_is_the_parity_set(recorded: dict[Any, Any]) -> None:
    assert Counter(case["fn"] for case in CASES) == RECORDED_COUNTS
    scope_outcomes = Counter(recorded[key][0] for key in recorded if key[0] == "normalize_scopes")
    assert dict(scope_outcomes) == SCOPE_OUTCOME_COUNTS


def test_both_paths_match_for_every_recorded_case(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[Any, Any]
) -> None:
    bridge = _RecordingBridge(recorded)
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True

    for case in CASES:
        name, args = case["fn"], case["args"]
        expected = recorded[_key(case)]
        assert _outcome(ORIGINALS[name], args) == expected
        assert _outcome(WRAPPERS[name], args) == expected

    assert len(bridge.calls) == len(CASES)


def test_python_originals_are_used_without_a_bridge(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[Any, Any]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False

    for case in CASES:
        assert _outcome(WRAPPERS[case["fn"]], case["args"]) == recorded[_key(case)]


def test_import_failure_is_recorded_and_falls_back(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[Any, Any]
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
    assert native.stale_reason("quote") == python_stale_reason("quote")


def test_successful_import_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _RecordingBridge({})
    monkeypatch.setattr(native.importlib, "import_module", lambda name: bridge)
    assert native._load_bridge() is bridge

    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True


def test_the_original_exception_contract_is_not_weakened(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[Any, Any]
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
