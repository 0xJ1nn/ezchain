"""The parameter schema is small, explicit and fail-closed on unknown keys."""

from __future__ import annotations

import pytest

from ezchain_sdk import ParamsError, validate_params

SCHEMA = {
    "type": "object",
    "properties": {
        "lookback": {"type": "integer", "minimum": 5, "maximum": 200, "default": 20},
        "risk_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
        "side": {"type": "string", "enum": ["long", "short"], "default": "long"},
        "mode": {"type": ["string", "null"], "pattern": "^(fast|slow)$"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["risk_pct"],
    "additionalProperties": False,
}


def test_defaults_are_applied_without_mutating_input() -> None:
    supplied = {"risk_pct": 1.0}
    result = validate_params(SCHEMA, supplied)
    assert result == {"risk_pct": 1.0, "lookback": 20, "side": "long"}
    assert supplied == {"risk_pct": 1.0}


def test_unknown_parameter_is_rejected() -> None:
    with pytest.raises(ParamsError) as caught:
        validate_params(SCHEMA, {"risk_pct": 1, "stop_los_pct": 5})
    assert caught.value.code == "PLUGIN_PARAMS_INVALID"
    assert caught.value.path == "$.stop_los_pct"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"risk_pct": 0},
        {"risk_pct": 6},
        {"risk_pct": 1, "lookback": 4},
        {"risk_pct": 1, "lookback": "20"},
        {"risk_pct": 1, "side": "both"},
        {"risk_pct": 1, "mode": "medium"},
        {"risk_pct": 1, "tags": ["a", "b", "c", "d"]},
        {"risk_pct": 1, "tags": [1]},
    ],
)
def test_invalid_values_fail_closed(params: dict[str, object]) -> None:
    with pytest.raises(ParamsError) as caught:
        validate_params(SCHEMA, params)
    assert caught.value.code == "PLUGIN_PARAMS_INVALID"


def test_unknown_schema_keyword_is_not_silently_ignored() -> None:
    with pytest.raises(ParamsError) as caught:
        validate_params({"type": "object", "dependentSchemas": {}}, {})
    assert caught.value.code == "PLUGIN_SCHEMA_UNSUPPORTED"


def test_any_of_and_nested_objects() -> None:
    schema = {
        "type": "object",
        "properties": {
            "size": {
                "anyOf": [
                    {"type": "number", "exclusiveMinimum": 0},
                    {"type": "string", "pattern": "^[0-9]+%$"},
                ]
            },
            "risk": {
                "type": "object",
                "properties": {"stop_pct": {"type": "number", "minimum": 0}},
                "required": ["stop_pct"],
            },
        },
        "required": ["size"],
    }
    assert validate_params(schema, {"size": "5%", "risk": {"stop_pct": 2}})["size"] == "5%"
    with pytest.raises(ParamsError):
        validate_params(schema, {"size": -1})


def test_empty_schema_accepts_any_object() -> None:
    assert validate_params({}, {"anything": 1}) == {"anything": 1}
    with pytest.raises(ParamsError):
        validate_params(None, ["not", "an", "object"])  # type: ignore[arg-type]
