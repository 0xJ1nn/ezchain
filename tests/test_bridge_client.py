"""The typed bridge client: dispatch, fallback, and an observable backend.

The compiled extension is optional and is not built for this suite. A stand-in replays
the recorded scope outcomes from the bridge crate's parity corpus, so both the Rust
path and the Python-fallback path are driven over every recorded case with
``monkeypatch`` only.

Covers: identical results/refusals on both paths across the recorded set, the Python
original when the extension is absent, unchanged ``ApiKeyError``, and ``using_rust()``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from collections import Counter
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest

from ezchain_api import native
from ezchain_api.services import paper, trade_policy
from ezchain_api.services.api_keys import ApiKeyError
from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes
from ezchain_core import money

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


# The money conversions use the bridge crate's recorded money corpus. A string case is
# the caller's exact Decimal spelling; the oracle converts it with Decimal(s) before
# calling money.py, exactly as a production caller hands the wrapper a Decimal. The
# compiled extension is not built for this suite, so a stand-in replays each recorded
# outcome at the bridge boundary (str/int in, str/int or the original exception out).
ROOT = Path(__file__).resolve().parents[1]
MONEY_CASES_PATH = ROOT / "rust/ezchain-bridge/tests/money_cases.jsonl"
ALL_MONEY_CASES = [
    json.loads(line) for line in MONEY_CASES_PATH.read_text(encoding="utf-8").splitlines() if line
]
MONEY_FNS = ("quantize_money", "floor_money", "to_minor_units", "from_minor_units")
MONEY_CASES = [case for case in ALL_MONEY_CASES if case["fn"] in MONEY_FNS]
MONEY_RECORDED_COUNTS = {
    "quantize_money": 71,
    "floor_money": 50,
    "to_minor_units": 48,
    "from_minor_units": 32,
}
MONEY_RECORDED_OUTCOMES = {
    "quantize_money": (56, 15),
    "floor_money": (41, 9),
    "to_minor_units": (34, 14),
    "from_minor_units": (28, 4),
}

PYTHON_MONEY: dict[str, Callable[..., Any]] = {
    "quantize_money": lambda value, *, field="amount": money.quantize_money(value, field=field),
    "floor_money": lambda value, *, field="amount": money.floor_money(value, field=field),
    "to_minor_units": lambda value, *, field="amount": money.to_minor_units(value, field=field),
    "from_minor_units": lambda minor: money.from_minor_units(minor),
}


def _money_decimal(value: Any) -> Any:
    """A recorded string is an exact Decimal spelling; other types pass through."""
    return Decimal(value) if isinstance(value, str) else value


def _case_field(args: list[Any]) -> str:
    return args[1] if len(args) > 1 else "amount"


def _key_value(value: Any) -> Any:
    """Canonical Decimal spelling for a string, or the value itself for other types."""
    if isinstance(value, str):
        try:
            return str(Decimal(value))
        except InvalidOperation:
            # A non-numeric spelling never reaches the wrapper: Decimal(s) refuses it
            # first, matching the oracle. Keep it distinct as a table key anyway.
            return value
    return value


def _money_key(name: str, value: Any, field: str) -> tuple[Any, ...]:
    return (name, type(value).__name__, _key_value(value), field)


def _invoke(function: Callable[..., Any], name: str, args: list[Any]) -> Any:
    if name == "from_minor_units":
        return function(args[0])
    return function(_money_decimal(args[0]), field=_case_field(args))


def _money_outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    """A comparable record: the exact value (Decimal spelled out), or the typed error."""
    try:
        value = call()
        return ("success", str(value) if isinstance(value, Decimal) else value)
    except Exception as exc:  # noqa: BLE001 - refusals are part of the recorded corpus
        return (
            "error",
            type(exc),
            str(exc),
            getattr(exc, "code", None),
            getattr(exc, "message", None),
        )


def _uses_bridge(name: str, args: list[Any]) -> bool:
    """Decimal (or an int count) crosses the FFI; other types go to the oracle for their refusal."""
    value = args[0]
    if name == "from_minor_units":  # the count itself must be an int, never a decimal string
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if not isinstance(value, str):
        return False
    try:  # a non-numeric spelling is refused by Decimal(s) before the wrapper is called
        Decimal(value)
    except InvalidOperation:
        return False
    return True


@pytest.fixture
def money_recorded() -> dict[tuple[Any, ...], tuple[Any, ...]]:
    """The oracle outcome of every recorded money conversion case, keyed as the bridge sees it."""
    return {
        _money_key(case["fn"], case["args"][0], _case_field(case["args"])): _money_outcome(
            lambda: _invoke(PYTHON_MONEY[case["fn"]], case["fn"], case["args"])
        )
        for case in MONEY_CASES
    }


class _MoneyBridge:
    """Stands in for the compiled module: replay recorded money outcomes, count dispatch."""

    def __init__(self, table: dict[tuple[Any, ...], tuple[Any, ...]]) -> None:
        self.table = table
        self.calls: list[tuple[Any, ...]] = []

    def quantize_money(self, value: Any, field: str = "amount") -> Any:
        return self._dispatch("quantize_money", value, field)

    def floor_money(self, value: Any, field: str = "amount") -> Any:
        return self._dispatch("floor_money", value, field)

    def to_minor_units(self, value: Any, field: str = "amount") -> Any:
        return self._dispatch("to_minor_units", value, field)

    def from_minor_units(self, minor: Any) -> Any:
        return self._dispatch("from_minor_units", minor, "amount")

    def _dispatch(self, name: str, value: Any, field: str) -> Any:
        self.calls.append((name, value, field))
        outcome = self.table[_money_key(name, value, field)]
        if outcome[0] == "success":
            return outcome[1]
        raise outcome[1](outcome[2])


def _case_outcome(
    case: dict[str, Any], table: dict[tuple[Any, ...], tuple[Any, ...]]
) -> tuple[Any, ...]:
    return table[_money_key(case["fn"], case["args"][0], _case_field(case["args"]))]


def test_money_recorded_corpus_is_the_conversion_parity_set(
    money_recorded: dict[tuple[Any, ...], tuple[Any, ...]],
) -> None:
    counts = Counter(case["fn"] for case in MONEY_CASES)
    assert len(ALL_MONEY_CASES) == 417
    assert dict(counts) == MONEY_RECORDED_COUNTS
    totals = {
        name: (
            sum(
                1
                for case in MONEY_CASES
                if case["fn"] == name and _case_outcome(case, money_recorded)[0] == "success"
            ),
            sum(
                1
                for case in MONEY_CASES
                if case["fn"] == name and _case_outcome(case, money_recorded)[0] == "error"
            ),
        )
        for name in MONEY_FNS
    }
    assert totals == MONEY_RECORDED_OUTCOMES


def test_money_both_paths_match_for_every_recorded_case(
    monkeypatch: pytest.MonkeyPatch, money_recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    bridge = _MoneyBridge(money_recorded)
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True

    for case in MONEY_CASES:
        name = case["fn"]
        expected = _case_outcome(case, money_recorded)
        assert (
            _money_outcome(lambda: _invoke(getattr(native, name), name, case["args"])) == expected
        )

    assert len(bridge.calls) == sum(
        1 for case in MONEY_CASES if _uses_bridge(case["fn"], case["args"])
    )

    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for case in MONEY_CASES:
        name = case["fn"]
        expected = _case_outcome(case, money_recorded)
        assert (
            _money_outcome(lambda: _invoke(getattr(native, name), name, case["args"])) == expected
        )


def test_money_refusals_keep_the_original_exception_on_both_paths(
    monkeypatch: pytest.MonkeyPatch, money_recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    bridge = _MoneyBridge(money_recorded)
    refusals = [case for case in MONEY_CASES if _case_outcome(case, money_recorded)[0] == "error"]
    assert len(refusals) == 42

    for path in (bridge, None):
        monkeypatch.setattr(native, "_BRIDGE", path)
        for case in refusals:
            name = case["fn"]
            expected = _case_outcome(case, money_recorded)
            with pytest.raises(expected[1]) as caught:
                _invoke(getattr(native, name), name, case["args"])
            assert str(caught.value) == expected[2]
            assert getattr(caught.value, "code", None) == expected[3]
            assert getattr(caught.value, "message", None) == expected[4]


def test_money_field_keyword_reaches_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _MoneyBridge({("to_minor_units", "str", "0.1", "notional"): ("success", 1000000)})
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.to_minor_units(Decimal("0.1"), field="notional") == 1000000
    assert bridge.calls == [("to_minor_units", "0.1", "notional")]


def test_floor_money_field_keyword_reaches_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _MoneyBridge(
        {
            ("floor_money", "str", "50.000000049", "resolved_size_usd"): (
                "success",
                "50.00000004",
            )
        }
    )
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.floor_money(Decimal("50.000000049"), field="resolved_size_usd") == Decimal(
        "50.00000004"
    )
    assert bridge.calls == [("floor_money", "50.000000049", "resolved_size_usd")]


def test_quantization_callers_route_through_the_client() -> None:
    """The switched call sites must use the typed wrappers, not the core-py originals."""
    assert paper.quantize_money is native.quantize_money
    assert trade_policy.floor_money is native.floor_money


def test_core_py_never_imports_the_api_client() -> None:
    """No module in core-py may import this client: that would reverse the layer boundary."""
    offenders: list[tuple[str, str]] = []
    for path in (ROOT / "packages/core-py/src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module == "ezchain_api" or module.startswith("ezchain_api."):
                    offenders.append((str(path), module))
    assert offenders == []
