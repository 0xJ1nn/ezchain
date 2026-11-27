"""The typed bridge client: fail-closed dispatch, real-module parity, no fallback.

The compiled extension is a prerequisite: ``bash tools/build-native.sh``. It is probed
once at import; when it is absent every wrapper raises ``NativeBridgeUnavailable`` and
nothing silently runs a Python original. The ``real_bridge`` fixture imports the actual
compiled module and skips with an actionable message if it was not built. When present,
the recorded corpora are driven through the real module and compared outcome-by-outcome
to the Python oracle (class, str, ``code`` and ``message`` included).
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

from ezchain_api import native, observability
from ezchain_api.native import NativeBridgeUnavailable
from ezchain_api.observability import normalize_method as python_normalize_method
from ezchain_api.services import paper, trade_policy
from ezchain_api.services.api_keys import ApiKeyError
from ezchain_api.services.api_keys import normalize_scopes as python_normalize_scopes
from ezchain_core import money

CASES_PATH = Path(__file__).resolve().parents[1] / "rust/ezchain-bridge/tests/cases.jsonl"
ALL_CASES = [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line]
SCOPE_CASES = [case for case in ALL_CASES if case["fn"] == "normalize_scopes"]
RECORDED_COUNT = 53
SCOPE_OUTCOME_COUNTS = {"success": 7, "error": 46}


@pytest.fixture(scope="module")
def real_bridge() -> Any:
    """The actual compiled extension; skip with a remedy when it was not built."""
    bridge = pytest.importorskip(
        "ezchain_bridge", reason="native extension absent — run bash tools/build-native.sh"
    )
    assert hasattr(bridge, "normalize_scopes") and hasattr(bridge, "quantize_money")
    return bridge


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


def test_recorded_corpus_is_the_scope_parity_set(
    recorded: dict[tuple[Any, ...], tuple[Any, ...]],
) -> None:
    assert len(ALL_CASES) == 63
    assert len(SCOPE_CASES) == RECORDED_COUNT
    assert dict(Counter(outcome[0] for outcome in recorded.values())) == SCOPE_OUTCOME_COUNTS


# --- real compiled module: scope parity -------------------------------------------


def test_real_bridge_matches_python_oracle_for_every_recorded_scope_case(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    assert native.using_rust() is True
    for case in SCOPE_CASES:
        expected = recorded[_case_key(case)]
        assert _outcome(python_normalize_scopes, case["args"]) == expected
        assert _outcome(native.normalize_scopes, case["args"]) == expected
    print(f"\nREAL scope cases: {len(SCOPE_CASES)}")


def test_bridge_originated_exception_contract_is_preserved_with_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    with pytest.raises(ApiKeyError) as via_client:
        native.normalize_scopes(["read", "bogus", "admin"])
    with pytest.raises(ApiKeyError) as via_original:
        python_normalize_scopes(["read", "bogus", "admin"])
    assert type(via_client.value) is type(via_original.value)
    assert via_client.value.code == via_original.value.code
    assert via_client.value.message == via_original.value.message
    assert str(via_client.value) == str(via_original.value)


# --- absent extension: typed failure, no fallback ---------------------------------


def test_scope_wrapper_fails_closed_when_extension_absent(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for case in SCOPE_CASES:
        with pytest.raises(NativeBridgeUnavailable) as caught:
            native.normalize_scopes(case["args"])
        assert "bash tools/build-native.sh" in str(caught.value)
    print(f"\nABSENT scope cases (typed failure): {len(SCOPE_CASES)}")


def test_import_failure_is_recorded_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_module = importlib.import_module
    attempted: list[str] = []
    monkeypatch.setattr(native, "_BRIDGE_ERROR", None, raising=False)

    def missing(name: str) -> Any:
        attempted.append(name)
        if name == native._BRIDGE_MODULE:
            raise ImportError("simulated: compiled extension absent")
        return real_import_module(name)

    monkeypatch.setattr(native.importlib, "import_module", missing)
    assert native._load_bridge() is None
    assert attempted == ["ezchain_bridge"]
    assert isinstance(native._BRIDGE_ERROR, ImportError)

    monkeypatch.setattr(native, "_BRIDGE", None)
    with pytest.raises(NativeBridgeUnavailable) as caught:
        native.normalize_scopes(["read"])
    assert "simulated: compiled extension absent" in str(caught.value)
    assert "bash tools/build-native.sh" in str(caught.value)


def test_successful_import_is_recorded(real_bridge: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native.importlib, "import_module", lambda name: real_bridge)
    assert native._load_bridge() is real_bridge
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    assert native.using_rust() is True


def test_client_stays_in_the_api_layer() -> None:
    """A core-py client reversed the import boundary by importing ezchain_api."""
    assert native.__name__ == "ezchain_api.native"
    assert importlib.util.find_spec("ezchain_core.native") is None


# --- HTTP method normalisation parity ------------------------------------------------


# Recorded from ezchain_api.observability.normalize_method, the same 70 cases the Rust
# kernel keeps in rust/ezchain-rpc/src/method.rs. Unicode full-casing edges are included:
# poſt/optionſ/optıons uppercase INTO a safe method and must be accepted, while ß, ı and
# the fullwidth spellings stay OTHER. Each expected value is the Python oracle's output.
METHOD_CASES: list[tuple[str, str]] = [
    ("GET", "GET"),
    ("POST", "POST"),
    ("PATCH", "PATCH"),
    ("DELETE", "DELETE"),
    ("PUT", "PUT"),
    ("HEAD", "HEAD"),
    ("OPTIONS", "OPTIONS"),
    ("get", "GET"),
    ("post", "POST"),
    ("patch", "PATCH"),
    ("delete", "DELETE"),
    ("put", "PUT"),
    ("head", "HEAD"),
    ("options", "OPTIONS"),
    ("Get", "GET"),
    ("Post", "POST"),
    ("Patch", "PATCH"),
    ("Delete", "DELETE"),
    ("Put", "PUT"),
    ("Head", "HEAD"),
    ("Options", "OPTIONS"),
    ("gEt", "GET"),
    ("pOsT", "POST"),
    ("pAtCh", "PATCH"),
    ("dElEtE", "DELETE"),
    ("pUt", "PUT"),
    ("hEaD", "HEAD"),
    ("oPtIoNs", "OPTIONS"),
    ("", "OTHER"),
    (" ", "OTHER"),
    ("BREW", "OTHER"),
    ("brew", "OTHER"),
    ("PROPFIND", "OTHER"),
    ("TRACE", "OTHER"),
    ("CONNECT", "OTHER"),
    ("GET ", "OTHER"),
    (" GET", "OTHER"),
    ("GET\t", "OTHER"),
    ("get\n", "OTHER"),
    ("get/post", "OTHER"),
    ("g3t", "OTHER"),
    ("123", "OTHER"),
    ("get;", "OTHER"),
    ("GET\x00", "OTHER"),
    ("GET\u200b", "OTHER"),
    ("MKCOL", "OTHER"),
    ("COPY", "OTHER"),
    ("MOVE", "OTHER"),
    ("LOCK", "OTHER"),
    ("UNLOCK", "OTHER"),
    ("SEARCH", "OTHER"),
    ("ß", "OTHER"),
    ("ı", "OTHER"),
    ("i", "OTHER"),
    ("İ", "OTHER"),
    ("ﬁ", "OTHER"),
    ("K", "OTHER"),
    ("ｇｅｔ", "OTHER"),
    ("é", "OTHER"),
    ("gét", "OTHER"),
    ("日本語", "OTHER"),
    ("😀", "OTHER"),
    ("poſt", "POST"),
    ("optionſ", "OPTIONS"),
    ("optıons", "OPTIONS"),
    ("gęt", "OTHER"),
    ("gÉt", "OTHER"),
    ("GEſ", "OTHER"),
    ("poſt ", "OTHER"),
    ("poſt\n", "OTHER"),
]


def test_recorded_method_cases_match_the_python_oracle() -> None:
    """The embedded corpus is the recorded case set, not a hand-picked subset."""
    assert len(METHOD_CASES) == 70
    assert dict(Counter(expected for _, expected in METHOD_CASES)) == {
        "GET": 4,
        "POST": 5,
        "PATCH": 4,
        "DELETE": 4,
        "PUT": 4,
        "HEAD": 4,
        "OPTIONS": 6,
        "OTHER": 39,
    }
    for method, expected in METHOD_CASES:
        assert python_normalize_method(method) == expected


def test_real_bridge_matches_python_oracle_for_every_recorded_method_case(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    assert native.using_rust() is True
    for method, expected in METHOD_CASES:
        assert python_normalize_method(method) == expected
        assert native.normalize_method(method) == expected
    print(f"\nREAL method cases: {len(METHOD_CASES)}")


def test_method_wrapper_fails_closed_when_extension_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for method, _ in METHOD_CASES:
        with pytest.raises(NativeBridgeUnavailable) as caught:
            native.normalize_method(method)
        assert "bash tools/build-native.sh" in str(caught.value)
    print(f"\nABSENT method cases (typed failure): {len(METHOD_CASES)}")


def test_method_callers_no_longer_route_through_the_client() -> None:
    """The request path uses the pure function; the bridge wrapper stays a parity oracle."""
    source = Path(observability.__file__).read_text(encoding="utf-8")
    assert "native.normalize_method" not in source
    assert native.normalize_method is not python_normalize_method


# The money conversions use the bridge crate's recorded money corpus. A string case is
# the caller's exact Decimal spelling; the oracle converts it with Decimal(s) before
# calling money.py, exactly as a production caller hands the wrapper a Decimal.
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


@pytest.fixture
def money_recorded() -> dict[tuple[Any, ...], tuple[Any, ...]]:
    """The oracle outcome of every recorded money conversion case, keyed as the bridge sees it."""
    return {
        _money_key(case["fn"], case["args"][0], _case_field(case["args"])): _money_outcome(
            lambda: _invoke(PYTHON_MONEY[case["fn"]], case["fn"], case["args"])
        )
        for case in MONEY_CASES
    }


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


# --- real compiled module: money parity -------------------------------------------


def test_real_bridge_matches_python_oracle_for_every_recorded_money_case(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch, money_recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    assert native.using_rust() is True
    for case in MONEY_CASES:
        expected = _case_outcome(case, money_recorded)
        assert (
            _money_outcome(lambda: _invoke(getattr(native, case["fn"]), case["fn"], case["args"]))
            == expected
        )
    print(f"\nREAL money cases: {len(MONEY_CASES)}")


def test_money_refusals_keep_the_original_exception_with_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch, money_recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    refusals = [case for case in MONEY_CASES if _case_outcome(case, money_recorded)[0] == "error"]
    assert len(refusals) == 42
    for case in refusals:
        expected = _case_outcome(case, money_recorded)
        with pytest.raises(expected[1]) as caught:
            _invoke(getattr(native, case["fn"]), case["fn"], case["args"])
        assert str(caught.value) == expected[2]
        assert getattr(caught.value, "code", None) == expected[3]
        assert getattr(caught.value, "message", None) == expected[4]
    print(f"REAL money refusals: {len(refusals)}")


def test_money_field_keyword_reaches_the_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    value = Decimal("0.000000001")  # sub-quantum: the refusal must name the caller's field
    with pytest.raises(money.SubQuantumError) as via_client:
        native.to_minor_units(value, field="notional")
    with pytest.raises(money.SubQuantumError) as via_original:
        money.to_minor_units(value, field="notional")
    assert str(via_client.value) == str(via_original.value)


def test_floor_money_field_keyword_reaches_the_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    value = Decimal("50.000000049")
    assert native.floor_money(value, field="resolved_size_usd") == money.floor_money(
        value, field="resolved_size_usd"
    )


# --- absent extension: typed failure for every money wrapper ----------------------


def test_money_wrappers_fail_closed_when_extension_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pass each recorded value as-is: with no bridge loaded the wrapper fails at the
    # _require_bridge() probe before any value conversion or dispatch, so even a
    # non-decimal string must raise the typed error rather than fall back.
    monkeypatch.setattr(native, "_BRIDGE", None)
    for case in MONEY_CASES:
        fn = getattr(native, case["fn"])
        raw = case["args"][0]
        with pytest.raises(NativeBridgeUnavailable):
            if case["fn"] == "from_minor_units":
                fn(raw)
            else:
                fn(raw, field=_case_field(case["args"]))
    print(f"\nABSENT money cases (typed failure): {len(MONEY_CASES)}")


# --- wiring and boundary -----------------------------------------------------------


def test_quantization_callers_route_through_the_client() -> None:
    """The switched call sites must use the typed wrappers, not the core-py originals."""
    assert paper.quantize_money is native.quantize_money
    assert trade_policy.floor_money is native.floor_money


def test_no_wrapper_imports_a_python_original() -> None:
    """Fail-closed clients must not lazily import the Python originals."""
    path = Path(native.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "ezchain_core.money",
        "ezchain_api.services.api_keys",
        "ezchain_api.observability",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in forbidden:
            offenders.append(str(node.module))
        if isinstance(node, ast.Import):
            offenders.extend(alias.name for alias in node.names if alias.name in forbidden)
    assert offenders == []


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
