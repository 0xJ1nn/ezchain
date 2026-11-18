"""The executor's typed bridge client: parity, fallback, and call-site wiring.

The executor is a separate deployable and cannot import ``ezchain_api.native`` without
reversing the service boundary, so it owns a small probe of the same optional
``ezchain_bridge`` extension. The compiled module is not built for this suite; a stand-in
replays the recorded ``quantize_money`` outcomes from the bridge crate's parity corpus,
so both the Rust path and the Python-fallback path are driven over every recorded case
with ``monkeypatch`` only. Refusals compare the exact exception class, message, ``code``
and ``message`` attribute on both paths.
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

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "rust/ezchain-bridge/tests/money_cases.jsonl"
ALL_CASES = [
    json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines() if line
]
QUANTIZE_CASES = [case for case in ALL_CASES if case["fn"] == "quantize_money"]


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


def _uses_bridge(args: list[Any]) -> bool:
    value = args[0]
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if not isinstance(value, str):
        return False
    try:
        Decimal(value)
    except InvalidOperation:
        return False
    return True


@pytest.fixture
def recorded() -> dict[tuple[Any, ...], tuple[Any, ...]]:
    """The oracle outcome of every recorded quantize case, keyed as the bridge sees it."""
    return {
        _key(case["args"][0], _field(case["args"])): _outcome(lambda _c=case: _oracle(_c["args"]))
        for case in QUANTIZE_CASES
    }


class _Bridge:
    """Stands in for the compiled module: replay recorded outcomes, count dispatch."""

    def __init__(self, table: dict[tuple[Any, ...], tuple[Any, ...]]) -> None:
        self.table = table
        self.calls: list[tuple[Any, ...]] = []

    def quantize_money(self, value: Any, field: str = "amount") -> Any:
        self.calls.append((value, field))
        outcome = self.table[_key(value, field)]
        if outcome[0] == "success":
            return outcome[1]
        raise outcome[1](outcome[2])


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


def test_both_paths_match_for_every_recorded_case(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    bridge = _Bridge(recorded)
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.using_rust() is True
    for case in QUANTIZE_CASES:
        assert _wrapper_outcome(case) == recorded[_key(case["args"][0], _field(case["args"]))]
    assert len(bridge.calls) == sum(1 for case in QUANTIZE_CASES if _uses_bridge(case["args"]))

    monkeypatch.setattr(native, "_BRIDGE", None)
    assert native.using_rust() is False
    for case in QUANTIZE_CASES:
        assert _wrapper_outcome(case) == recorded[_key(case["args"][0], _field(case["args"]))]


def test_refusals_keep_the_original_exception_on_both_paths(
    monkeypatch: pytest.MonkeyPatch, recorded: dict[tuple[Any, ...], tuple[Any, ...]]
) -> None:
    refusals = [
        case
        for case in QUANTIZE_CASES
        if recorded[_key(case["args"][0], _field(case["args"]))][0] == "error"
    ]
    assert len(refusals) == 15
    for path in (_Bridge(recorded), None):
        monkeypatch.setattr(native, "_BRIDGE", path)
        for case in refusals:
            expected = recorded[_key(case["args"][0], _field(case["args"]))]
            with pytest.raises(expected[1]) as caught:
                native.quantize_money(_decimal(case["args"][0]), field=_field(case["args"]))
            assert str(caught.value) == expected[2]
            assert getattr(caught.value, "code", None) == expected[3]
            assert getattr(caught.value, "message", None) == expected[4]


def test_field_keyword_reaches_the_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = _Bridge({("0.1", "notional_usd"): ("success", "0.10000000")})
    monkeypatch.setattr(native, "_BRIDGE", bridge)
    assert native.quantize_money(Decimal("0.1"), field="notional_usd") == Decimal("0.10000000")
    assert bridge.calls == [("0.1", "notional_usd")]


def test_import_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_quantize_callers_route_through_the_client() -> None:
    assert pipeline.quantize_money is native.quantize_money
    assert outbox.quantize_money is native.quantize_money


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
