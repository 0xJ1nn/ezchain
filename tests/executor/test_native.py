"""The executor's typed bridge client: fail-closed dispatch and real-module parity.

The executor is a separate deployable and cannot import ``ezchain_api.native`` without
reversing the service boundary, so it owns a small probe of the same ``ezchain_bridge``
extension. The extension is a prerequisite: when absent, every wrapper raises
``NativeBridgeUnavailable`` and no Python original is used. The ``real_bridge`` fixture
imports the actual compiled module (skipping with a remedy if it was not built) and the
recorded ``quantize_money`` corpus is compared outcome-by-outcome to the Python oracle,
including the exact exception class, message, ``code`` and ``message``.
"""

from __future__ import annotations

import ast
import importlib
import json
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest

from ezchain_core import money
from ezchain_executor import native, outbox, pipeline
from ezchain_executor.native import NativeBridgeUnavailable

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "rust/ezchain-bridge/tests/money_cases.jsonl"
ALL_CASES = [
    json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line
]
QUANTIZE_CASES = [case for case in ALL_CASES if case["fn"] == "quantize_money"]


@pytest.fixture(scope="module")
def real_bridge() -> Any:
    """The actual compiled extension; skip with a remedy when it was not built."""
    bridge = pytest.importorskip(
        "ezchain_bridge", reason="native extension absent — run bash tools/build-native.sh"
    )
    assert hasattr(bridge, "quantize_money")
    return bridge


def _field(args: list[Any]) -> str:
    return args[1] if len(args) > 1 else "amount"


def _decimal(value: Any) -> Any:
    return Decimal(value) if isinstance(value, str) else value


def _outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    try:
        return ("success", str(call()))
    except Exception as exc:  # noqa: BLE001 - refusals are part of the recorded corpus
        return (
            "error",
            type(exc),
            str(exc),
            getattr(exc, "code", None),
            getattr(exc, "message", None),
        )


def _oracle(args: list[Any]) -> Decimal:
    return money.quantize_money(_decimal(args[0]), field=_field(args))


def _key_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return str(Decimal(value))
        except InvalidOperation:
            return value
    return value


def _key(value: Any, field: str) -> tuple[Any, ...]:
    return (_key_value(value), field)


@pytest.fixture
def recorded() -> dict[tuple[Any, ...], tuple[Any, ...]]:
    """The oracle outcome of every recorded quantize case, keyed as the bridge sees it."""
    return {
        _key(case["args"][0], _field(case["args"])): _outcome(lambda _c=case: _oracle(_c["args"]))
        for case in QUANTIZE_CASES
    }


def _wrapper_outcome(case: dict[str, Any]) -> tuple[Any, ...]:
    return _outcome(
        lambda: native.quantize_money(_decimal(case["args"][0]), field=_field(case["args"]))
    )


def test_recorded_corpus_is_the_quantize_parity_set(
    recorded: dict[tuple[Any, ...], tuple[Any, ...]],
) -> None:
    assert len(ALL_CASES) == 417
    assert len(QUANTIZE_CASES) == 71
    errors = sum(
        1
        for case in QUANTIZE_CASES
        if recorded[_key(case["args"][0], _field(case["args"]))][0] == "error"
    )
    assert errors == 15


# --- real compiled module: parity ---------------------------------------------------


def test_real_bridge_matches_python_oracle_for_every_recorded_case(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    assert native.using_rust() is True
    for case in QUANTIZE_CASES:
        assert _wrapper_outcome(case) == recorded[_key(case["args"][0], _field(case["args"]))]
    print(f"\nREAL executor quantize cases: {len(QUANTIZE_CASES)}")


def test_bridge_originated_refusals_keep_the_original_exception_with_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    refusals = [
        case
        for case in QUANTIZE_CASES
        if recorded[_key(case["args"][0], _field(case["args"]))][0] == "error"
    ]
    assert len(refusals) == 15
    for case in refusals:
        expected = recorded[_key(case["args"][0], _field(case["args"]))]
        with pytest.raises(expected[1]) as caught:
            native.quantize_money(_decimal(case["args"][0]), field=_field(case["args"]))
        assert str(caught.value) == expected[2]
        assert getattr(caught.value, "code", None) == expected[3]
        assert getattr(caught.value, "message", None) == expected[4]
    print(f"REAL executor refusals: {len(refusals)}")


def test_field_keyword_reaches_the_real_bridge(
    real_bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # quantize_money rounds computed values, so the field-dependent spelling is asserted
    # through a refusal: a float's typed error names the caller's field.
    monkeypatch.setattr(native, "_BRIDGE", real_bridge)
    with pytest.raises(money.FloatMoneyError) as via_client:
        native.quantize_money(0.1, field="notional_usd")
    with pytest.raises(money.FloatMoneyError) as via_original:
        money.quantize_money(0.1, field="notional_usd")
    assert str(via_client.value) == str(via_original.value)


# --- absent extension: typed failure ------------------------------------------------


def test_wrapper_fails_closed_when_extension_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # The wrapper fails at the _require_bridge() probe before any value conversion, so
    # every recorded value — including non-decimal strings — must raise the typed error.
    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for case in QUANTIZE_CASES:
        with pytest.raises(NativeBridgeUnavailable) as caught:
            native.quantize_money(case["args"][0], field=_field(case["args"]))
        assert "bash tools/build-native.sh" in str(caught.value)
    print(f"\nABSENT executor quantize cases (typed failure): {len(QUANTIZE_CASES)}")


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
        native.quantize_money(Decimal("0.1"))
    assert "simulated: compiled extension absent" in str(caught.value)
    assert "bash tools/build-native.sh" in str(caught.value)


# --- wiring and boundary ------------------------------------------------------------


def test_quantize_callers_route_through_the_client() -> None:
    assert pipeline.quantize_money is native.quantize_money
    assert outbox.quantize_money is native.quantize_money


def test_no_wrapper_imports_a_python_original() -> None:
    """Fail-closed clients must not lazily import the Python originals."""
    path = Path(native.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ezchain_core.money":
            offenders.append(str(node.module))
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if alias.name == "ezchain_core.money"
            )
    assert offenders == []


def test_executor_never_imports_the_api_client() -> None:
    """No executor module may import ezchain_api: that reverses the service boundary."""
    offenders: list[tuple[str, str]] = []
    for path in (ROOT / "services/executor/src").rglob("*.py"):
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
