"""A deliberately small JSON Schema validator for plugin parameters.

The SDK does not ship a general-purpose JSON Schema engine. It supports the keywords a
strategy configuration needs and **rejects every keyword it does not implement** — a
schema that claims ``dependentSchemas`` must not be silently treated as unconstrained.

Safety default: an object with ``properties`` rejects unknown keys unless it explicitly
sets ``additionalProperties: true``. That makes a typo in a parameter name fail closed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "anyOf",
        "oneOf",
        "allOf",
    }
)
ANNOTATION_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)


class ParamsError(ValueError):
    """A parameter document that does not satisfy the plugin schema."""

    def __init__(self, code: str, message: str, *, path: str = "$") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "path": self.path}


def _fail(code: str, message: str, path: str) -> None:
    raise ParamsError(code, message, path=path)


def _check_keywords(schema: Mapping[str, Any], path: str) -> None:
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        _fail(
            "PLUGIN_SCHEMA_UNSUPPORTED",
            f"unsupported schema keyword(s): {', '.join(unknown)}",
            path,
        )


def _is_number(value: object) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def _decimal(value: object, path: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        _fail("PLUGIN_PARAMS_INVALID", "expected a finite number", path)
        raise AssertionError from exc
    if not result.is_finite():
        _fail("PLUGIN_PARAMS_INVALID", "expected a finite number", path)
    return result


def _type_matches(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            or (isinstance(value, float) and math.isfinite(value) and value.is_integer())
        )
    if expected == "number":
        return _is_number(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, Mapping)
    return False


def _check_type(value: object, schema: Mapping[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected is None:
        return
    options = [expected] if isinstance(expected, str) else expected
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        _fail("PLUGIN_SCHEMA_UNSUPPORTED", "type must be a string or list of strings", path)
    if not any(_type_matches(value, option) for option in options):
        names = " | ".join(str(option) for option in options)
        _fail("PLUGIN_PARAMS_INVALID", f"expected {names}", path)


def _check_number(value: object, schema: Mapping[str, Any], path: str) -> None:
    if not _is_number(value):
        return
    number = _decimal(value, path)
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    exclusive_min = schema.get("exclusiveMinimum")
    exclusive_max = schema.get("exclusiveMaximum")
    if minimum is not None and number < _decimal(minimum, path):
        _fail("PLUGIN_PARAMS_INVALID", f"must be >= {minimum}", path)
    if maximum is not None and number > _decimal(maximum, path):
        _fail("PLUGIN_PARAMS_INVALID", f"must be <= {maximum}", path)
    if exclusive_min is not None and number <= _decimal(exclusive_min, path):
        _fail("PLUGIN_PARAMS_INVALID", f"must be > {exclusive_min}", path)
    if exclusive_max is not None and number >= _decimal(exclusive_max, path):
        _fail("PLUGIN_PARAMS_INVALID", f"must be < {exclusive_max}", path)
    multiple = schema.get("multipleOf")
    if multiple is not None:
        divisor = _decimal(multiple, path)
        if divisor <= 0:
            _fail("PLUGIN_SCHEMA_UNSUPPORTED", "multipleOf must be > 0", path)
        if number % divisor != 0:
            _fail("PLUGIN_PARAMS_INVALID", f"must be a multiple of {multiple}", path)


def _check_string(value: object, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(value, str):
        return
    if "minLength" in schema and len(value) < int(schema["minLength"]):
        _fail("PLUGIN_PARAMS_INVALID", f"must be at least {schema['minLength']} characters", path)
    if "maxLength" in schema and len(value) > int(schema["maxLength"]):
        _fail("PLUGIN_PARAMS_INVALID", f"must be at most {schema['maxLength']} characters", path)
    pattern = schema.get("pattern")
    if pattern is not None:
        try:
            matched = re.search(str(pattern), value)
        except re.error as exc:
            _fail("PLUGIN_SCHEMA_UNSUPPORTED", f"invalid pattern: {exc}", path)
            raise AssertionError from exc
        if matched is None:
            _fail("PLUGIN_PARAMS_INVALID", f"must match {pattern}", path)


def _check_enum(value: object, schema: Mapping[str, Any], path: str) -> None:
    if "const" in schema and value != schema["const"]:
        _fail("PLUGIN_PARAMS_INVALID", f"must equal {schema['const']!r}", path)
    if "enum" in schema:
        options = schema["enum"]
        if not isinstance(options, list):
            _fail("PLUGIN_SCHEMA_UNSUPPORTED", "enum must be a list", path)
        if value not in options:
            _fail("PLUGIN_PARAMS_INVALID", f"must be one of {options!r}", path)


def _check_array(value: object, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(value, list):
        return
    if "minItems" in schema and len(value) < int(schema["minItems"]):
        _fail("PLUGIN_PARAMS_INVALID", f"must have at least {schema['minItems']} items", path)
    if "maxItems" in schema and len(value) > int(schema["maxItems"]):
        _fail("PLUGIN_PARAMS_INVALID", f"must have at most {schema['maxItems']} items", path)
    if schema.get("uniqueItems") is True:
        encoded = [repr(item) for item in value]
        if len(encoded) != len(set(encoded)):
            _fail("PLUGIN_PARAMS_INVALID", "items must be unique", path)
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            _fail("PLUGIN_SCHEMA_UNSUPPORTED", "items must be a schema object", path)
        for index, item in enumerate(value):
            _validate(item, items, f"{path}[{index}]")


def _validate_object(value: object, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(value, Mapping):
        return
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        _fail("PLUGIN_SCHEMA_UNSUPPORTED", "properties must be an object", path)
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        _fail("PLUGIN_SCHEMA_UNSUPPORTED", "required must be a list of strings", path)
    for key in required:
        if key not in value:
            _fail("PLUGIN_PARAMS_INVALID", f"missing required parameter {key!r}", f"{path}.{key}")
    additional = schema.get("additionalProperties", False if properties else True)
    if additional is not True and additional is not False and not isinstance(additional, Mapping):
        _fail("PLUGIN_SCHEMA_UNSUPPORTED", "additionalProperties must be boolean or schema", path)
    for key, item in value.items():
        child = f"{path}.{key}"
        if key in properties:
            child_schema = properties[key]
            if not isinstance(child_schema, Mapping):
                _fail("PLUGIN_SCHEMA_UNSUPPORTED", "property schema must be an object", child)
            _validate(item, child_schema, child)
        elif additional is False:
            _fail("PLUGIN_PARAMS_INVALID", f"unknown parameter {key!r}", child)
        elif isinstance(additional, Mapping):
            _validate(item, additional, child)


def _apply_defaults(value: object, schema: Mapping[str, Any]) -> object:
    """Return a copy with defaults applied to an object's missing properties."""
    if not isinstance(value, Mapping) or not isinstance(schema.get("properties"), Mapping):
        return value
    result = dict(value)
    for key, child_schema in schema["properties"].items():
        if not isinstance(child_schema, Mapping):
            continue
        if key not in result and "default" in child_schema:
            result[key] = child_schema["default"]
        elif key in result:
            result[key] = _apply_defaults(result[key], child_schema)
    return result


def _check_combinators(value: object, schema: Mapping[str, Any], path: str) -> None:
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        if not isinstance(branches, list) or not branches:
            _fail("PLUGIN_SCHEMA_UNSUPPORTED", f"{keyword} must be a non-empty list", path)
        results: list[bool] = []
        for branch in branches:
            if not isinstance(branch, Mapping):
                _fail("PLUGIN_SCHEMA_UNSUPPORTED", f"{keyword} entries must be schemas", path)
            try:
                _validate(value, branch, path)
                results.append(True)
            except ParamsError:
                results.append(False)
        if keyword == "allOf" and not all(results):
            _fail("PLUGIN_PARAMS_INVALID", "does not satisfy every allOf branch", path)
        if keyword == "anyOf" and not any(results):
            _fail("PLUGIN_PARAMS_INVALID", "does not satisfy any anyOf branch", path)
        if keyword == "oneOf" and sum(results) != 1:
            _fail("PLUGIN_PARAMS_INVALID", "must satisfy exactly one oneOf branch", path)


def _validate(value: object, schema: Mapping[str, Any], path: str) -> None:
    _check_keywords(schema, path)
    _check_type(value, schema, path)
    _check_enum(value, schema, path)
    _check_number(value, schema, path)
    _check_string(value, schema, path)
    _check_array(value, schema, path)
    _validate_object(value, schema, path)
    _check_combinators(value, schema, path)


def validate_params(
    schema: Mapping[str, Any] | None, params: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Validate plugin configuration and return it with schema defaults applied.

    ``schema=None`` or ``{}`` accepts any object. The top level must be an object so a
    strategy always receives a mapping.
    """
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ParamsError("PLUGIN_PARAMS_INVALID", "params must be an object")
    if schema is None or not schema:
        return dict(params)
    if not isinstance(schema, Mapping):
        raise ParamsError("PLUGIN_SCHEMA_UNSUPPORTED", "schema must be an object")
    if schema.get("type") not in (None, "object"):
        raise ParamsError("PLUGIN_SCHEMA_UNSUPPORTED", "top-level schema must be an object")
    _check_keywords(schema, "$")
    _validate(params, schema, "$")
    normalized = _apply_defaults(params, schema)
    if not isinstance(normalized, Mapping):
        raise ParamsError("PLUGIN_PARAMS_INVALID", "params must be an object")
    return dict(normalized)


__all__ = ["ParamsError", "SUPPORTED_KEYWORDS", "validate_params"]
