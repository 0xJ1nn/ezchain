"""The gate logic is tested on synthetic contracts before it guards the real ones."""

from __future__ import annotations

from typing import Any

from .gate import (
    Change,
    evaluate,
    flatten,
    next_minor,
    surface_diff,
)

SURFACES = ("openapi", "mcp_tools", "sdk_interfaces")


def _contract(amount: str = "25") -> dict[str, Any]:
    return {
        "openapi": {
            "paths": {
                "/api/v1/paper/orders": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"properties": {"amount_usd": {"type": "string"}}}
                                }
                            }
                        }
                    }
                }
            }
        },
        "mcp_tools": {
            "ezchain_place_paper_order": {
                "inputSchema": {"properties": {"amount_usd": {"type": "string"}}}
            }
        },
        "sdk_interfaces": {"Intent": {"fields": {"amount_usd": {"type": "string"}}}},
    }


def _with_amount(contract: dict[str, Any], amount: str) -> dict[str, Any]:
    updated: dict[str, Any] = {
        "openapi": contract["openapi"],
        "mcp_tools": contract["mcp_tools"],
        "sdk_interfaces": contract["sdk_interfaces"],
    }
    for surface in SURFACES:
        updated[surface] = _replace_leaf(contract[surface], "type", "string", amount)
    return updated


def _replace_leaf(value: Any, key: str, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {
            name: new if name == key and item == old else _replace_leaf(item, key, old, new)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_leaf(item, key, old, new) for item in value]
    return value


def test_flatten_names_nested_dicts_and_lists() -> None:
    flat = flatten({"a": {"b": [1, {"c": "x"}]}})
    assert flat == {"a.b#length": 2, "a.b[0]": 1, "a.b[1].c": "x"}


def test_surface_diff_names_the_exact_field_that_moved() -> None:
    old = {"components": {"schemas": {"Fill": {"properties": {"fee_usd": {"type": "string"}}}}}}
    new = {"components": {"schemas": {"Fill": {"properties": {"fee_usd": {"type": "number"}}}}}}
    changes = surface_diff("openapi", new, old)
    assert changes == [
        Change(
            surface="openapi",
            path="components.schemas.Fill.properties.fee_usd.type",
            kind="changed",
            old="string",
            new="number",
        )
    ]
    assert "fee_usd.type" in str(changes[0])
    assert "string" in str(changes[0]) and "number" in str(changes[0])


def test_an_unchanged_snapshot_passes() -> None:
    contract = _contract()
    problems = evaluate(contract, contract, "0.1.0", [], "0.1.0")
    assert problems == []


def test_a_change_without_a_version_bump_is_refused_and_names_the_field() -> None:
    previous = _contract()
    current = _with_amount(previous, "number")
    problems = evaluate(current, previous, "0.1.0", [], "0.1.0")
    assert len(problems) == 3
    for surface in SURFACES:
        assert any(surface in problem for problem in problems)
    assert any("without a version bump" in problem for problem in problems)
    assert any("amount_usd.type" in problem for problem in problems)


def test_a_version_bump_requires_the_snapshots_to_be_regenerated() -> None:
    previous = _contract()
    current = _with_amount(previous, "number")
    problems = evaluate(current, previous, "0.1.0", [], "0.2.0")
    assert len(problems) == 3
    assert all("snapshots are stale" in problem for problem in problems)
    assert all("0.2.0" in problem for problem in problems)


def test_removing_a_field_without_a_deprecation_record_is_refused() -> None:
    previous = _contract()
    current = _contract()
    del current["sdk_interfaces"]["Intent"]["fields"]["amount_usd"]
    problems = evaluate(current, previous, "0.1.0", [], "0.2.0")
    assert any("removed without a deprecation record" in problem for problem in problems)
    assert any("Intent.fields.amount_usd" in problem for problem in problems)


def test_a_deprecation_must_last_exactly_one_minor_version() -> None:
    previous = _contract()
    current = _contract()
    del current["sdk_interfaces"]["Intent"]["fields"]["amount_usd"]
    bad = [
        {
            "surface": "sdk_interfaces",
            "path": "Intent.fields.amount_usd",
            "deprecated_in": "0.1.0",
            "removed_in": "0.3.0",
            "warning": "use size_usd instead",
        }
    ]
    problems = evaluate(current, previous, "0.1.0", bad, "0.3.0")
    assert any("exactly one minor" in problem for problem in problems)
    assert next_minor("0.1.0") == "0.2.0"


def test_a_deprecated_field_must_still_work_and_warn_during_its_window() -> None:
    warned = _contract()
    warned["sdk_interfaces"]["Intent"]["fields"]["amount_usd"]["description"] = (
        "Deprecated: use size_usd instead"
    )
    unwarned = _contract()
    record = [
        {
            "surface": "sdk_interfaces",
            "path": "Intent.fields.amount_usd",
            "deprecated_in": "0.1.0",
            "removed_in": "0.2.0",
            "warning": "use size_usd instead",
        }
    ]
    # The field is still present in both cases; only its warning marker differs.
    problems = evaluate(unwarned, warned, "0.1.5", record, "0.1.5")
    assert any("advertise its warning" in problem for problem in problems)
    assert evaluate(warned, warned, "0.1.5", record, "0.1.5") == []


def test_a_deprecated_field_cannot_disappear_before_its_removal_version() -> None:
    previous = _contract()
    current = _contract()
    del current["sdk_interfaces"]["Intent"]["fields"]["amount_usd"]
    record = [
        {
            "surface": "sdk_interfaces",
            "path": "Intent.fields.amount_usd",
            "deprecated_in": "0.1.0",
            "removed_in": "0.2.0",
            "warning": "use size_usd instead",
        }
    ]
    problems = evaluate(current, previous, "0.1.0", record, "0.1.5")
    assert any("before its deprecation ended" in problem for problem in problems)
